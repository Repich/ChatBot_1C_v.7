"""FastAPI and server-rendered Russian chat boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import Field
from starlette.responses import StreamingResponse

from chatbot1c import __version__
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.models import (
    ContinuationRequest,
    MaintenanceClearRequest,
    MaintenanceConfirmRequest,
)
from chatbot1c.bootstrap import RuntimeApplication, build_runtime
from chatbot1c.config import Settings
from chatbot1c.contracts.errors import ContractIssue, ContractValidationError
from chatbot1c.contracts.json_limits import MAX_DOCUMENT_BYTES
from chatbot1c.domain.base import ClosedModel
from chatbot1c.domain.evidence import EvidenceBundle
from chatbot1c.domain.skill import Skill

API_PREFIX = "/api/v1"


class SessionCreate(ClosedModel):
    title: Annotated[str, Field(min_length=1, max_length=200)] = "Новый диалог"


class MessageCreate(ClosedModel):
    text: Annotated[str, Field(min_length=1, max_length=8000)]
    client_message_id: Annotated[str, Field(min_length=8, max_length=120)] | None = None
    expected_context_version: Annotated[int, Field(ge=1)] | None = None


def create_app(
    runtime: RuntimeApplication | None = None,
    *,
    settings: Settings | None = None,
) -> FastAPI:
    """Create and initialize the local application on an explicit factory call."""

    composed = runtime or build_runtime(settings)
    task_set: set[asyncio.Task[object]] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if task_set:
            await asyncio.gather(*task_set, return_exceptions=True)
        await composed.close()

    app = FastAPI(
        title="ChatBot 1C",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.runtime = composed
    app.state.turn_tasks = task_set

    package_root = files("chatbot1c")
    template_dir = Path(str(package_root.joinpath("templates")))
    static_dir = Path(str(package_root.joinpath("static")))
    templates = Jinja2Templates(directory=template_dir)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.exception_handler(ApplicationError)
    async def application_error_handler(
        _: Request, error: ApplicationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {"code": error.code, "message": error.message_ru},
                "errors": _application_issues(error),
            },
        )

    @app.exception_handler(ContractValidationError)
    async def contract_error_handler(
        _: Request, error: ContractValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"errors": _issues(error.issues)},
        )

    @app.exception_handler(RequestValidationError)
    async def request_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        if request.url.path.endswith("/continuations"):
            return _public_rejection(
                ApplicationError(
                    "CONTINUATION_HANDLE_INVALID",
                    "Continuation handle имеет неверный формат.",
                    422,
                )
            )
        if request.url.path.endswith("/maintenance/clear"):
            return _public_rejection(
                ApplicationError(
                    "CLEAR_SCOPES_INVALID",
                    "Maintenance clear DTO не соответствует контракту.",
                    422,
                )
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "REQUEST_VALIDATION_ERROR",
                    "message": "Параметры HTTP-запроса не прошли проверку.",
                },
                "details": error.errors(),
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def chat_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="chat.html",
            context={"sessions": composed.store.list_sessions()},
        )

    @app.get(f"{API_PREFIX}/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "live"}

    @app.get(f"{API_PREFIX}/health/ready")
    async def health_ready() -> JSONResponse:
        catalog = composed.catalog.pin()
        help_revision = composed.help_index.active_revision()
        ready = bool(catalog.skills) and help_revision is not None
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "not_ready",
                "database": "ready",
                "catalog_revision": catalog.revision,
                "catalog_skill_count": len(catalog.skills),
                "help_revision": None if help_revision is None else help_revision[0],
            },
        )

    @app.get(f"{API_PREFIX}/diagnostics/dependencies")
    async def dependency_diagnostics() -> dict[str, object]:
        return {
            "deepseek": {
                "status": (
                    "configured"
                    if composed.settings.deepseek_api_key is not None
                    else "unavailable"
                )
            },
            "mcp": {"status": "configured", "tool_allowlist": ["execute_query", "get_metadata"]},
        }

    @app.post(f"{API_PREFIX}/sessions", status_code=201)
    async def create_session(body: SessionCreate) -> dict[str, object]:
        return _session_summary(composed.store.create_session(body.title))

    @app.get(f"{API_PREFIX}/sessions")
    async def list_sessions() -> dict[str, object]:
        return {
            "sessions": [
                _session_summary(session) for session in composed.store.list_sessions()
            ]
        }

    @app.get(f"{API_PREFIX}/sessions/{{session_id}}")
    async def get_session(session_id: UUID) -> dict[str, object]:
        session = composed.store.get_session(session_id)
        if session is None:
            raise ApplicationError("SESSION_NOT_FOUND", "Диалог не найден.", 404)
        messages: list[dict[str, object]] = []
        for turn in composed.store.list_turns(session_id):
            messages.append(
                {
                    "role": "user",
                    "text": turn.user_text,
                    "created_at": turn.created_at.isoformat(),
                    "turn_id": str(turn.turn_id),
                }
            )
            if turn.assistant_text is not None:
                public_turn = _turn_public(turn, composed)
                messages.append(
                    {
                        "role": "assistant",
                        "text": turn.assistant_text,
                        "created_at": (
                            turn.completed_at or turn.created_at
                        ).isoformat(),
                        "turn_id": str(turn.turn_id),
                        "outcome": turn.outcome,
                        "citations": _turn_citations(turn.evidence_json),
                        "pagination": public_turn.get("pagination"),
                    }
                )
        result = _session_summary(session)
        result["messages"] = messages
        return result

    @app.delete(f"{API_PREFIX}/sessions/{{session_id}}", status_code=204)
    async def delete_session(session_id: UUID) -> Response:
        if not composed.store.delete_session(session_id):
            raise ApplicationError("SESSION_NOT_FOUND", "Диалог не найден.", 404)
        return Response(status_code=204)

    @app.post(f"{API_PREFIX}/sessions/{{session_id}}/messages", status_code=202)
    async def create_message(
        session_id: UUID, body: MessageCreate
    ) -> dict[str, object]:
        session = composed.store.get_session(session_id)
        if session is None:
            raise ApplicationError("SESSION_NOT_FOUND", "Диалог не найден.", 404)
        turn, created = composed.chat.submit_message(
            session_id=session_id,
            text=body.text,
            client_message_id=body.client_message_id or str(uuid4()),
            expected_context_version=(
                body.expected_context_version
                if body.expected_context_version is not None
                else session.context_version
            ),
        )
        if created:
            task = asyncio.create_task(composed.chat.process_turn(turn.turn_id))
            task_set.add(task)
            task.add_done_callback(task_set.discard)
        return {
            "turn_id": str(turn.turn_id),
            "trace_id": str(turn.trace_id),
            "status": turn.status,
        }

    @app.post(
        f"{API_PREFIX}/sessions/{{session_id}}/continuations", status_code=202
    )
    async def continue_list(
        session_id: UUID, body: ContinuationRequest
    ) -> JSONResponse:
        try:
            turn = composed.chat.submit_continuation(
                session_id=session_id,
                continuation_handle=body.continuation_handle,
            )
        except ApplicationError as error:
            return _public_rejection(error)
        task = asyncio.create_task(composed.chat.process_turn(turn.turn_id))
        task_set.add(task)
        task.add_done_callback(task_set.discard)
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "turn_id": str(turn.turn_id),
                "trace_id": str(turn.trace_id),
            },
        )

    @app.get(f"{API_PREFIX}/turns/{{turn_id}}")
    async def get_turn(turn_id: UUID) -> dict[str, object]:
        turn = composed.store.get_turn(turn_id)
        if turn is None:
            raise ApplicationError("TURN_NOT_FOUND", "Ход диалога не найден.", 404)
        return _turn_public(turn, composed)

    @app.get(f"{API_PREFIX}/turns/{{turn_id}}/details")
    async def get_turn_details(turn_id: UUID) -> dict[str, object]:
        turn = composed.store.get_turn(turn_id)
        if turn is None:
            raise ApplicationError("TURN_NOT_FOUND", "Ход диалога не найден.", 404)
        evidence = _evidence(turn.evidence_json)
        return {
            "turn_id": str(turn.turn_id),
            "trace_id": str(turn.trace_id),
            "status": turn.status,
            "outcome": turn.outcome,
            "plan": None if turn.plan_json is None else json.loads(turn.plan_json),
            "coverage": None if evidence is None else evidence.coverage.model_dump(mode="json"),
            "skills": (
                []
                if evidence is None
                else [skill.model_dump(mode="json") for skill in evidence.catalog_snapshot.skills]
            ),
        }

    @app.get(f"{API_PREFIX}/turns/{{turn_id}}/events")
    async def turn_events(turn_id: UUID, request: Request) -> StreamingResponse:
        if composed.store.get_turn(turn_id) is None:
            raise ApplicationError("TURN_NOT_FOUND", "Ход диалога не найден.", 404)

        async def stream() -> AsyncIterator[bytes]:
            last_sequence = 0
            while True:
                events = composed.store.events(turn_id, after=last_sequence)
                for event in events:
                    last_sequence = event.sequence
                    payload = {
                        "turn_id": str(turn_id),
                        "sequence": event.sequence,
                        "stage": event.event_name,
                        "status": event.status,
                        "occurred_at": event.timestamp.isoformat(),
                        "payload": dict(event.payload),
                    }
                    yield (
                        "data: "
                        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        + "\n\n"
                    ).encode("utf-8")
                    if event.event_name in {"request.completed", "request.failed"}:
                        return
                turn = composed.store.get_turn(turn_id)
                if turn is None or turn.status in {"completed", "failed", "interrupted"}:
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(f"{API_PREFIX}/skills")
    async def list_skills() -> dict[str, object]:
        catalog = composed.catalog.pin()
        return {
            "catalog_revision": catalog.revision,
            "catalog_snapshot_id": str(catalog.snapshot_id),
            "catalog_digest": catalog.digest,
            "skills": [_skill_public(skill) for skill in catalog.skills.values()],
        }

    @app.get(f"{API_PREFIX}/skills/{{skill_id}}")
    async def get_skill(skill_id: str) -> dict[str, object]:
        skill = composed.catalog.pin().skills.get(skill_id)
        if skill is None:
            raise ApplicationError("SKILL_NOT_FOUND", "Навык не найден.", 404)
        return _skill_public(skill)

    @app.get(f"{API_PREFIX}/skills/{{skill_id}}/export")
    async def export_skill(
        skill_id: str,
        closure: Annotated[Literal["bare", "embedded"], Query()] = "bare",
    ) -> Response:
        payload = composed.catalog_service.export_skill(skill_id, closure=closure)
        suffix = "package.json" if closure == "embedded" else "skill.json"
        return Response(
            payload,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{skill_id}.{suffix}"'
            },
        )

    @app.post(f"{API_PREFIX}/skill-packages/import")
    @app.put(f"{API_PREFIX}/skill-packages/import")
    async def import_package(
        file: Annotated[UploadFile, File()],
        mode: Annotated[Literal["create", "replace"], Query()] = "create",
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> JSONResponse:
        payload = await file.read(MAX_DOCUMENT_BYTES + 1)
        try:
            result = composed.catalog_service.import_package(
                payload,
                mode=mode,
                if_match=if_match,
            )
        except ContractValidationError as error:
            return _catalog_rejection(composed, 422, error.issues)
        except ApplicationError as error:
            issue = ContractIssue(
                code=error.code,
                json_pointer="",
                message_ru=error.message_ru,
            )
            return _catalog_rejection(composed, error.status_code, (issue,))
        return JSONResponse(status_code=200, content=_import_public(result))

    @app.get(f"{API_PREFIX}/skill-packages/export")
    async def export_package(skill_id: list[str] | None = Query(default=None)) -> Response:
        payload = composed.catalog_service.export_package(skill_id)
        return Response(
            payload,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="chatbot1c-skills.package.json"'
            },
        )

    @app.delete(f"{API_PREFIX}/skills/{{skill_id}}")
    async def delete_skill(
        skill_id: str,
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> dict[str, object]:
        catalog = composed.catalog_service.delete_skill(skill_id, if_match=if_match)
        return {
            "status": "accepted",
            "catalog_revision": catalog.revision,
            "catalog_snapshot_id": str(catalog.snapshot_id),
        }

    @app.get(f"{API_PREFIX}/traces/{{trace_id}}/export")
    async def export_trace(trace_id: UUID) -> Response:
        payload = composed.diagnostics.export(trace_id)
        return Response(
            payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="trace-{trace_id}.zip"'
            },
        )

    @app.post(f"{API_PREFIX}/maintenance/clear")
    async def maintenance_clear(body: MaintenanceClearRequest) -> JSONResponse:
        try:
            if isinstance(body, MaintenanceConfirmRequest):
                result = composed.store.confirm_clear(
                    body.confirmation_token, body.scopes
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "cleared",
                        "scopes": list(result.scopes),
                        "deleted": dict(result.counts),
                    },
                )
            result = composed.store.preview_clear(body.scopes)
            return JSONResponse(
                status_code=200,
                content={
                    "status": "preview",
                    "scopes": list(result.scopes),
                    "counts": dict(result.counts),
                    "confirmation_token": result.confirmation_token,
                    "expires_at": result.expires_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                },
            )
        except ApplicationError as error:
            return _public_rejection(error)

    return app


def _session_summary(session: object) -> dict[str, object]:
    from chatbot1c.application.models import SessionRecord

    if not isinstance(session, SessionRecord):
        raise TypeError("expected SessionRecord")
    return {
        "session_id": str(session.session_id),
        "title": session.title,
        "context_version": session.context_version,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


def _turn_public(
    turn: object, runtime: RuntimeApplication
) -> dict[str, object]:
    from chatbot1c.application.models import TurnRecord

    if not isinstance(turn, TurnRecord):
        raise TypeError("expected TurnRecord")
    result: dict[str, object] = {
        "turn_id": str(turn.turn_id),
        "trace_id": str(turn.trace_id),
        "status": turn.status,
        "outcome": turn.outcome,
        "assistant_message": {
            "text": turn.assistant_text or "",
            "citations": _turn_citations(turn.evidence_json),
        },
        "pinned": {
            "catalog_revision": turn.catalog_revision,
            "catalog_snapshot_id": (
                None
                if turn.catalog_snapshot_id is None
                else str(turn.catalog_snapshot_id)
            ),
        },
        "created_at": turn.created_at.isoformat(),
        "completed_at": (
            None if turn.completed_at is None else turn.completed_at.isoformat()
        ),
    }
    if turn.error_code is not None:
        result["error"] = {
            "code": turn.error_code,
            "message": turn.assistant_text or "Ошибка обработки запроса.",
        }
    evidence = _evidence(turn.evidence_json)
    if evidence is not None and evidence.empty_reason is not None:
        result["reason"] = evidence.empty_reason
    if evidence is not None and evidence.pagination is not None:
        page = evidence.pagination
        continuation: dict[str, str] | None = None
        if page.has_more and page.continuation_handle is not None:
            stored = runtime.store.get_continuation(page.continuation_handle)
            if stored is not None:
                continuation = {
                    "handle": stored.handle,
                    "expires_at": stored.expires_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                }
        result["pagination"] = {
            "shown": page.shown,
            "page_size": page.page_size,
            "has_more": page.has_more,
            "continuation": continuation,
        }
    return result


def _evidence(value: str | None) -> EvidenceBundle | None:
    if value is None:
        return None
    return EvidenceBundle.model_validate_json(value)


def _turn_citations(value: str | None) -> list[dict[str, str]]:
    evidence = _evidence(value)
    if evidence is None:
        return []
    return [
        {"title": citation.title, "source_uri": citation.source_uri}
        for citation in evidence.citations
    ]


def _skill_public(skill: Skill) -> dict[str, object]:
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "digest": skill.integrity.digest,
        "name_ru": skill.display.name_ru,
        "purpose_ru": skill.display.purpose_ru,
        "limitations_ru": list(skill.display.limitations_ru),
        "capability_ids": list(skill.provides.capability_ids),
        "fact_types": list(skill.provides.fact_types),
        "parameters": [
            {
                "name": parameter.name,
                "title_ru": parameter.title_ru,
                "description_ru": parameter.description_ru,
                "value_type": parameter.value_type.value,
                "required": parameter.required,
                "semantic_type": parameter.semantic_type,
                "normalization": parameter.normalization,
                "allowed_values": parameter.allowed_values,
            }
            for parameter in skill.parameters
        ],
        "output": {
            "cardinality": skill.output_contract.cardinality,
            "facts": [
                {
                    "fact_id": fact.fact_id,
                    "semantic_type": fact.semantic_type,
                    "title_ru": fact.title_ru,
                }
                for fact in skill.output_contract.facts
            ],
        },
        "compatibility": skill.compatibility.model_dump(mode="json"),
        "examples": [example.model_dump(mode="json") for example in skill.examples],
        "dependencies": [
            dependency.skill_id for dependency in skill.dependencies.skills
        ],
    }


def _import_public(result: object) -> dict[str, object]:
    from chatbot1c.application.models import ImportResult

    if not isinstance(result, ImportResult):
        raise TypeError("expected ImportResult")
    return {
        "status": "accepted",
        "catalog_revision": result.revision,
        "catalog_snapshot_id": str(result.snapshot_id),
        "skills": [
            {"skill_id": skill_id, "version": version, "digest": digest}
            for skill_id, version, digest in result.skills
        ],
    }


def _catalog_rejection(
    runtime: RuntimeApplication,
    status_code: int,
    issues: tuple[ContractIssue, ...],
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "rejected",
            "catalog_revision": runtime.catalog.pin().revision,
            "errors": _issues(issues),
        },
    )


def _issues(issues: tuple[ContractIssue, ...]) -> list[dict[str, object]]:
    return [issue.model_dump(mode="json") for issue in issues]


def _application_issues(error: ApplicationError) -> list[dict[str, object]]:
    if error.issues:
        return _issues(error.issues)
    return [
        {"code": error.code, "json_pointer": "", "message_ru": error.message_ru}
    ]


def _public_rejection(error: ApplicationError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "status": "rejected",
            "trace_id": str(uuid4()),
            "error": {
                "code": error.code,
                "message_ru": error.message_ru,
                "retryable": False,
            },
        },
    )
