from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "tests/corpus/user_questions.yaml"
CATALOG_PATH = ROOT / "docs/requirements/skill_catalog.md"
CAPABILITY_PATTERN = re.compile(r"^CAP-[A-Z0-9]+(?:-[A-Z0-9]+)+$")
CATALOG_ROW_PATTERN = re.compile(
    r"^\|\s*`(CAP-[A-Z0-9]+(?:-[A-Z0-9]+)+)`\s*\|", re.MULTILINE
)


def _load_corpus() -> dict:
    return yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))


def _catalog_capabilities() -> set[str]:
    return set(CATALOG_ROW_PATTERN.findall(CATALOG_PATH.read_text(encoding="utf-8")))


def test_corpus_has_exactly_116_unique_contiguous_ids() -> None:
    corpus = _load_corpus()
    scenarios = corpus["scenarios"]
    ids = [scenario["id"] for scenario in scenarios]

    assert corpus["scenario_count"] == 116
    assert len(scenarios) == 116
    assert len(ids) == len(set(ids))
    assert ids == [f"Q{number:03d}" for number in range(1, 117)]


def test_scenario_shape_and_declared_type_counts() -> None:
    scenarios = _load_corpus()["scenarios"]
    allowed_types = {"data", "documentation", "follow_up", "negative", "interaction"}

    for scenario in scenarios:
        assert scenario["type"] in allowed_types
        assert isinstance(scenario["category"], str) and scenario["category"].strip()
        assert isinstance(scenario["question"], str) and scenario["question"].strip()
        assert isinstance(scenario["expected_behavior"], str)
        assert scenario["expected_behavior"].strip()
        assert isinstance(scenario["capability_ids"], list)
        assert len(scenario["capability_ids"]) == len(set(scenario["capability_ids"]))

    assert Counter(scenario["type"] for scenario in scenarios) == {
        "data": 85,
        "documentation": 10,
        "follow_up": 11,
        "negative": 9,
        "interaction": 1,
    }


def test_follow_up_context_points_only_to_prior_scenarios() -> None:
    scenarios = _load_corpus()["scenarios"]
    positions = {scenario["id"]: index for index, scenario in enumerate(scenarios)}

    for scenario in scenarios:
        if scenario["type"] != "follow_up":
            continue
        previous = scenario.get("context", {}).get("previous_scenario")
        if previous is None:
            sequence = scenario.get("sequence", [])
            assert len(sequence) >= 2, scenario["id"]
            continue
        assert previous in positions, scenario["id"]
        assert positions[previous] < positions[scenario["id"]], scenario["id"]


def test_corpus_covers_all_87_catalog_capabilities() -> None:
    scenarios = _load_corpus()["scenarios"]
    catalog_capabilities = _catalog_capabilities()
    corpus_capabilities = {
        capability
        for scenario in scenarios
        for capability in scenario["capability_ids"]
    }

    assert len(catalog_capabilities) == 87
    assert all(CAPABILITY_PATTERN.fullmatch(item) for item in corpus_capabilities)
    assert corpus_capabilities == catalog_capabilities


def test_acceptance_special_scenario_sets_are_stable() -> None:
    scenarios = _load_corpus()["scenarios"]
    by_type = {
        scenario_type: [item["id"] for item in scenarios if item["type"] == scenario_type]
        for scenario_type in {"follow_up", "negative", "interaction"}
    }

    assert by_type["follow_up"] == [
        "Q037", "Q042", "Q063", "Q064", "Q073", "Q082",
        "Q092", "Q093", "Q095", "Q097", "Q108",
    ]
    assert by_type["negative"] == [f"Q{number:03d}" for number in range(98, 107)]
    assert by_type["interaction"] == ["Q107"]

