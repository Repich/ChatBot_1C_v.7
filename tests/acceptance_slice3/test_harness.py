from __future__ import annotations

import hashlib
import json

import rfc8785

from .support import FixtureClient
from .synthetic_package import (
    ASSET_SKILL,
    DETAIL_SKILL,
    FIXED_MOMENT,
    PHYSICAL_TYPE,
    SET_SKILL,
)


def test_fixture_server_records_control_state(fixture: FixtureClient) -> None:
    configured = fixture.configure("display", asset_count=3, has_more=True)
    assert configured["scenario"] == "display"
    assert configured["options"] == {"asset_count": 3, "has_more": True}
    assert fixture.requests() == []


def test_portable_package_is_self_consistent_without_production_oracle(
    portable_package: bytes,
) -> None:
    package = json.loads(portable_package)
    assert package["schema_version"] == "1.1.0"
    assert package["document_type"] == "skill_package"
    skills = {skill["skill_id"]: skill for skill in package["skills"]}
    assert set(skills) == {ASSET_SKILL, DETAIL_SKILL, SET_SKILL}
    resolver = skills[ASSET_SKILL]
    assert resolver["output_contract"]["resolution"]["protocol"] == (
        "typed_entity_resolver_v1"
    )
    assert resolver["output_contract"]["context_export_policy"][0]["mode"] == (
        "selected_only"
    )
    detail = skills[DETAIL_SKILL]
    assert detail["parameters"][0]["context_slot_keys"] == ["selection.synthetic_asset"]
    assert detail["output_contract"]["context_export_policy"][0] == {
        "fact_id": "snapshot.moment",
        "slot_key": "filter.synthetic_snapshot",
        "mode": "confirmed_filter",
        "semantic_type": "synthetic.snapshot",
        "value_type": "datetime",
        "lifetime": {"mode": "session"},
    }
    assert FIXED_MOMENT in portable_package.decode("utf-8")
    assert PHYSICAL_TYPE in portable_package.decode("utf-8")
    for document in [*package["skills"], package]:
        unsigned = {key: value for key, value in document.items() if key != "integrity"}
        assert (
            document["integrity"]["digest"]
            == hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
        )


def test_fixture_vocabulary_is_unseen_and_deliberately_non_ut() -> None:
    assert ASSET_SKILL.startswith("qa.synthetic.")
    assert "СинтетическийАктив" in PHYSICAL_TYPE
    assert "synthetic.snapshot" not in {"catalog.item", "catalog.warehouse"}


def test_fixture_mcp_transport_records_and_returns_exact_ref(
    fixture: FixtureClient,
) -> None:
    fixture.configure("display", asset_count=1)
    response = fixture.http.json(
        "POST",
        "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "execute_query",
                "arguments": {
                    "query": "ВЫБРАТЬ Активы.Ссылка КАК Актив ИЗ Справочник.СинтетическиеАктивы КАК Активы",
                    "params": {},
                    "limit": 20,
                    "include_schema": True,
                },
            },
        },
    )
    assert response.status == 200
    envelope = response.json()["result"]["structuredContent"]
    assert envelope["success"] is True
    assert envelope["data"][0]["Актив"]["_objectRef"] is True
    assert len(fixture.requests("mcp_execute_query")) == 1


def test_fixture_deepseek_transport_builds_schema_echoed_plan(
    fixture: FixtureClient,
) -> None:
    fixture.configure("display", asset_count=1)

    def card(skill_id: str, facts: list[tuple[str, str]]) -> dict:
        return {
            "skill_id": skill_id,
            "version": "1.1.0",
            "output": {
                "cardinality": "many",
                "facts": [
                    {
                        "fact_id": fact_id,
                        "semantic_type": semantic,
                        "value_type": "entity_ref"
                        if fact_id.endswith("ref")
                        else "string",
                        "required": True,
                        "nullable": False,
                    }
                    for fact_id, semantic in facts
                ],
            },
        }

    planner_input = {
        "expected_echo": {
            "request_id": "00000000-0000-4000-8000-000000009001",
            "session_context_version": 1,
            "catalog_snapshot_id": "00000000-0000-4000-8000-000000009002",
            "catalog_revision": 1,
        },
        "context": {"confirmed_facts": []},
        "skill_manifest": [
            card(
                ASSET_SKILL,
                [
                    ("asset.ref", "synthetic.asset"),
                    ("asset.name", "synthetic.asset.name"),
                    ("asset.code", "synthetic.asset.code"),
                ],
            ),
            card(
                DETAIL_SKILL,
                [
                    ("asset.ref", "synthetic.asset"),
                    ("asset.name", "synthetic.asset.name"),
                    ("snapshot.moment", "synthetic.snapshot"),
                    ("snapshot.value", "synthetic.snapshot.value"),
                ],
            ),
            card(SET_SKILL, [("asset.count", "synthetic.asset.count")]),
        ],
    }
    response = fixture.http.json(
        "POST",
        "/chat/completions",
        {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(planner_input, ensure_ascii=False),
                }
            ],
        },
    )
    assert response.status == 200
    plan = json.loads(response.json()["choices"][0]["message"]["content"])
    assert plan["request_id"] == planner_input["expected_echo"]["request_id"]
    assert plan["result"]["steps"][0]["skill_id"] == ASSET_SKILL
    assert len(fixture.requests("deepseek")) == 1
