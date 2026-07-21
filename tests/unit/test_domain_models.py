from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from chatbot1c.domain.base import ClosedModel
from chatbot1c.domain.evidence import EvidenceBundle
from chatbot1c.domain.outcomes import CoverageStatus, Outcome
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.plan import PlannerOutput
from chatbot1c.domain.skill import Skill
from chatbot1c.domain.types import EntityRef

ROOT = Path(__file__).resolve().parents[2]
VALID = ROOT / "tests/fixtures/contracts/valid"


def _json(name: str) -> dict:
    return json.loads((VALID / name).read_text(encoding="utf-8"))


def _all_model_subclasses(model: type[ClosedModel]) -> set[type[ClosedModel]]:
    found: set[type[ClosedModel]] = set()
    pending = list(model.__subclasses__())
    while pending:
        child = pending.pop()
        if child in found:
            continue
        found.add(child)
        pending.extend(child.__subclasses__())
    return found


def test_every_domain_dto_forbids_extra_fields() -> None:
    models = _all_model_subclasses(ClosedModel)
    assert models
    assert all(model.model_config.get("extra") == "forbid" for model in models)


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("data_skill.json", Skill),
        ("documentation_skill.json", Skill),
        ("skill_package.json", SkillPackage),
        ("planner_execute.json", PlannerOutput),
        ("planner_clarify.json", PlannerOutput),
        ("planner_refuse.json", PlannerOutput),
        ("planner_capability_gap.json", PlannerOutput),
        ("data_evidence_rows.json", EvidenceBundle),
        ("data_evidence_empty.json", EvidenceBundle),
        ("data_evidence_zero_aggregate.json", EvidenceBundle),
        ("documentation_evidence_disagreement.json", EvidenceBundle),
    ],
)
def test_all_positive_contract_shapes_instantiate_closed_models(
    filename: str, model: type[ClosedModel]
) -> None:
    parsed = model.model_validate(_json(filename))
    assert parsed.model_dump(mode="json", by_alias=True)["document_type"]


def test_nested_extra_field_is_rejected_by_dto() -> None:
    plan = _json("planner_execute.json")
    plan["result"]["steps"][0]["query"] = "ВЫБРАТЬ 1"

    with pytest.raises(ValidationError) as caught:
        PlannerOutput.model_validate(plan)

    assert any(error["type"] == "extra_forbidden" for error in caught.value.errors())


def test_entity_ref_uses_contract_aliases_on_serialization() -> None:
    raw = _json("data_evidence_rows.json")["facts"][0]["value"]
    entity = EntityRef.model_validate(raw)

    assert entity.object_ref is True
    assert entity.model_dump(mode="json", by_alias=True) == raw


def test_outcome_and_coverage_enums_keep_boundary_values_distinct() -> None:
    assert Outcome.SUCCESS_EMPTY != Outcome.ZERO_AGGREGATE
    assert {status.value for status in CoverageStatus} == {
        "covered",
        "missing",
        "ambiguous",
        "incompatible_unit",
        "wrong_cardinality",
        "wrong_time_scope",
    }
