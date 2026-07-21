from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from chatbot1c.adapters.deepseek import DeepSeekPlanner
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.models import PinnedCatalog, PlannerRequest
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.package import SkillPackage

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "skills/ut-11.5.27.56/ut.starter.slice-one.package.json"


def _request(message: str = "Найди query_template как обычный текст") -> PlannerRequest:
    harness = ContractHarness.discover(ROOT)
    document = harness.validate_json_bytes(PACKAGE.read_bytes())
    assert isinstance(document, SkillPackage)
    snapshot = PinnedCatalog.create(
        uuid4(), 2, {skill.skill_id: skill for skill in document.skills}
    )
    return PlannerRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        message=message,
        turn_time=datetime.now(UTC),
        context_version=1,
        catalog_snapshot_id=snapshot.snapshot_id,
        catalog_revision=snapshot.revision,
        confirmed_facts=(),
        recent_user_messages=(),
        skill_cards=snapshot.cards(limit=16),
    )


def _planner(client: httpx.AsyncClient) -> DeepSeekPlanner:
    return DeepSeekPlanner(
        api_key="fixture-secret",
        base_url="https://deepseek.invalid",
        model="deepseek-chat",
        harness=ContractHarness.discover(ROOT),
        client=client,
        sleep=lambda _: None,
    )


def _completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}]
        },
    )


def _clarification(request: PlannerRequest) -> str:
    return json.dumps(
        {
            "schema_version": "1.0.0",
            "document_type": "planner_output",
            "request_id": str(request.request_id),
            "session_context_version": request.context_version,
            "catalog_snapshot_id": str(request.catalog_snapshot_id),
            "catalog_revision": request.catalog_revision,
            "decision": "clarify",
            "interpretation": {
                "intent_kind": "data",
                "goal_ru": "Уточнить значение пользовательского текста.",
                "required_facts": [
                    {
                        "requirement_id": "r1",
                        "semantic_type": "catalog.item",
                        "value_type": "entity_ref",
                        "cardinality": "one",
                        "required": True,
                    }
                ],
                "slots": [
                    {
                        "slot_id": "item",
                        "semantic_type": "catalog.item",
                        "value_type": "entity_ref",
                        "status": "missing",
                        "mentions": ["query_template"],
                    }
                ],
            },
            "result": {
                "kind": "clarify",
                "question_ru": "Какой товар требуется найти?",
                "missing_requirement_ids": ["r1"],
                "choices": [],
            },
        },
        ensure_ascii=False,
    )


def test_user_text_query_template_is_allowed_and_manifest_never_leaks_query() -> None:
    request = _request()

    def handler(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content)
        serialized = json.dumps(payload, ensure_ascii=False)
        assert request.message in serialized
        for card in request.skill_cards:
            assert card.purpose_ru in serialized
            assert all(parameter.title_ru in serialized for parameter in card.parameters)
        package = json.loads(PACKAGE.read_bytes())
        for skill in package["skills"]:
            query = skill.get("operation", {}).get("query_template", {}).get("text")
            if query:
                assert query not in serialized
        return _completion(_clarification(request))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = _planner(client)
    result = asyncio.run(planner.plan(request))
    assert result.decision == "clarify"
    asyncio.run(client.aclose())


def test_malformed_provider_envelope_is_not_misclassified_as_planner_json() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    )
    planner = _planner(client)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(planner.plan(_request()))
    assert rejected.value.code == "DEEPSEEK_ENVELOPE_INVALID"
    asyncio.run(client.aclose())


def test_invalid_planner_json_gets_one_bounded_repair_then_fails() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _completion("not-json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    planner = _planner(client)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(planner.plan(_request()))
    assert rejected.value.code == "DEEPSEEK_STRUCTURED_OUTPUT_INVALID"
    assert calls == 2
    asyncio.run(client.aclose())
