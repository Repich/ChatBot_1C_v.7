"""Bounded OpenAI-compatible DeepSeek planner adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, cast

import httpx
from pydantic import ValidationError

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.models import PlannerRequest
from chatbot1c.application.ports import PlannerPort
from chatbot1c.contracts.errors import ContractValidationError
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.contracts.json_limits import loads_bounded_json, validate_json_structure
from chatbot1c.domain.plan import PlannerOutput

MAX_PROVIDER_BODY = 1024 * 1024
MAX_PLANNER_CONTENT = 256 * 1024
MAX_PLANNER_REQUEST = 512 * 1024


class DeepSeekPlanner(PlannerPort):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        harness: ContractHarness,
        client: httpx.AsyncClient | None = None,
        max_tokens: int = 4096,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeek API key is required to compose live planner")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._harness = harness
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._max_tokens = max_tokens
        self._sleep = sleep

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def plan(self, request: PlannerRequest) -> PlannerOutput:
        messages = self._messages(request)
        content = await self._completion(messages, attempts=2, timeout=12.0)
        try:
            return self._parse(content)
        except (ContractValidationError, ValidationError, ValueError) as first_error:
            repair = [
                {
                    "role": "system",
                    "content": (
                        "Исправь JSON строго по переданной schema. Не добавляй query, "
                        "код, tool arguments или новые skill IDs. Верни только object."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "invalid_json": content,
                            "validation_errors": _compact_errors(first_error),
                            "schema": self._harness.schemas.schema(
                                "planner-output.schema.json"
                            ),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ]
            repaired = await self._completion(repair, attempts=1, timeout=10.0)
            try:
                return self._parse(repaired)
            except (ContractValidationError, ValidationError, ValueError) as error:
                raise ApplicationError(
                    "DEEPSEEK_STRUCTURED_OUTPUT_INVALID",
                    "Планировщик дважды вернул JSON, не соответствующий контракту.",
                    503,
                ) from error

    def _messages(self, request: PlannerRequest) -> list[dict[str, str]]:
        context = [
            {
                "handle": fact.handle,
                "semantic_type": fact.semantic_type,
                "presentation": fact.presentation,
                "origin_turn_id": str(fact.origin_turn_id),
            }
            for fact in request.confirmed_facts
        ]
        cards = [card.model_dump(mode="json") for card in request.skill_cards]
        payload = {
            "question_ru": request.message,
            "turn_time": request.turn_time.isoformat(),
            "context": {
                "confirmed_facts": context,
                "recent_user_messages": list(request.recent_user_messages),
                "context_version": request.context_version,
            },
            "skill_manifest": cards,
            "expected_echo": {
                "request_id": str(request.request_id),
                "session_context_version": request.context_version,
                "catalog_snapshot_id": str(request.catalog_snapshot_id),
                "catalog_revision": request.catalog_revision,
            },
            "planner_schema": self._harness.schemas.schema(
                "planner-output.schema.json"
            ),
        }
        if _contains_forbidden_manifest_key(cards):
            raise AssertionError("planner payload must never contain query text")
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > MAX_PLANNER_REQUEST:
            raise ApplicationError(
                "PLANNER_REQUEST_LIMIT",
                "Bounded planner request превышает 512 KiB.",
                422,
            )
        return [
            {
                "role": "system",
                "content": (
                    "Ты ограниченный планировщик read-only ассистента 1С УТ. "
                    "Используй только skill IDs из manifest и opaque context handles. "
                    "Не создавай query, код, MCP arguments или object refs. "
                    "Верни только JSON object по planner schema."
                ),
            },
            {"role": "user", "content": serialized},
        ]

    async def _completion(
        self, messages: list[dict[str, str]], *, attempts: int, timeout: float
    ) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
        }
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                    timeout=httpx.Timeout(timeout, connect=2.0),
                )
                if len(response.content) > MAX_PROVIDER_BODY:
                    raise ApplicationError(
                        "DEEPSEEK_OUTPUT_LIMIT",
                        "Ответ DeepSeek превышает допустимый размер.",
                        503,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise _RetryableStatus(response.status_code)
                if response.status_code >= 400:
                    raise ApplicationError(
                        "DEEPSEEK_REQUEST_REJECTED",
                        f"DeepSeek отклонил запрос (HTTP {response.status_code}).",
                        503,
                    )
                try:
                    outer = _bounded_provider_object(response.content)
                    content = _choice_content(outer)
                except ValueError as error:
                    raise ApplicationError(
                        "DEEPSEEK_ENVELOPE_INVALID",
                        "Ответ DeepSeek не содержит ожидаемый completion envelope.",
                        503,
                    ) from error
                if len(content.encode("utf-8")) > MAX_PLANNER_CONTENT:
                    raise ApplicationError(
                        "DEEPSEEK_OUTPUT_LIMIT",
                        "Planner JSON превышает 256 KiB.",
                        503,
                    )
                return content
            except ApplicationError:
                raise
            except (httpx.TimeoutException, httpx.TransportError, _RetryableStatus) as error:
                last_error = error
                if attempt < attempts:
                    await self._sleep(0.5)
        raise ApplicationError(
            "LLM_UNAVAILABLE",
            "DeepSeek временно недоступен; запрос к данным не выполнялся.",
            503,
        ) from last_error

    def _parse(self, content: str) -> PlannerOutput:
        document = loads_bounded_json(content.encode("utf-8"))
        self._harness.schemas.validate(document, "planner-output.schema.json")
        return PlannerOutput.model_validate(document)


class FixturePlanner(PlannerPort):
    """Deterministic queue adapter for application and UI tests."""

    def __init__(self, outputs: list[PlannerOutput]) -> None:
        self._outputs = list(outputs)
        self.requests: list[PlannerRequest] = []

    async def plan(self, request: PlannerRequest) -> PlannerOutput:
        self.requests.append(request)
        if not self._outputs:
            raise ApplicationError(
                "FIXTURE_PLANNER_EXHAUSTED", "Fixture planner не содержит ответа.", 503
            )
        return self._outputs.pop(0)


class _RetryableStatus(Exception):
    pass


def _bounded_provider_object(payload: bytes) -> dict[str, Any]:
    try:
        value: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("DeepSeek response is not JSON") from error
    validate_json_structure(value)
    if not isinstance(value, dict):
        raise ValueError("DeepSeek response root is not an object")
    return cast(dict[str, Any], value)


def _choice_content(document: dict[str, Any]) -> str:
    try:
        choices = document["choices"]
        if not isinstance(choices, list) or not choices:
            raise TypeError
        choice = choices[0]
        if not isinstance(choice, dict):
            raise TypeError
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError
        content = message["content"]
        if not isinstance(content, str):
            raise TypeError
        return content
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("DeepSeek response has no choices[0].message.content") from error


def _compact_errors(error: BaseException) -> list[dict[str, str]]:
    if isinstance(error, ContractValidationError):
        return [
            {
                "code": issue.code,
                "json_pointer": issue.json_pointer,
                "message": issue.message_ru[:300],
            }
            for issue in error.issues[:20]
        ]
    if isinstance(error, ValidationError):
        return [
            {
                "code": str(item["type"]),
                "json_pointer": "/" + "/".join(map(str, item["loc"])),
                "message": str(item["msg"])[:300],
            }
            for item in error.errors(include_url=False)[:20]
        ]
    return [{"code": "JSON_PARSE_ERROR", "json_pointer": "", "message": str(error)[:300]}]


def _contains_forbidden_manifest_key(value: object) -> bool:
    if isinstance(value, dict):
        if any(key in {"query", "query_text", "query_template", "mcp_arguments"} for key in value):
            return True
        return any(_contains_forbidden_manifest_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_manifest_key(item) for item in value)
    return False
