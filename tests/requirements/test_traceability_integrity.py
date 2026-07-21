from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "docs/requirements/product_requirements.md"
ACCEPTANCE = ROOT / "docs/requirements/acceptance_criteria.md"
TRACEABILITY = ROOT / "docs/requirements/requirements_traceability.md"
ARCH_MAPPING = ROOT / "docs/architecture/requirements_mapping.md"
CATALOG = ROOT / "docs/requirements/skill_catalog.md"
CORPUS = ROOT / "tests/corpus/user_questions.yaml"
ORACLE_MANIFEST = ROOT / "tests/oracles/acceptance_observable_state.yaml"
SOURCE_INVENTORY = ROOT / "docs/source_inventory.md"
BASIC_PERFORMANCE_SCENARIOS = [
    "Q001",
    "Q011",
    "Q031",
    "Q041",
    "Q051",
    "Q062",
    "Q071",
    "Q081",
    "Q102",
    "Q107",
]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _expected(prefix: str, count: int) -> set[str]:
    return {f"{prefix}-{number:03d}" for number in range(1, count + 1)}


def _first_column_ids(text: str, prefix: str, backticked: bool) -> set[str]:
    marker = r"`" if backticked else ""
    pattern = rf"^\|\s*{marker}({prefix}-\d{{3}}){marker}\s*\|"
    return set(re.findall(pattern, text, flags=re.MULTILINE))


def test_requirement_and_acceptance_definition_counts_are_exact() -> None:
    requirements = _text(REQUIREMENTS)
    acceptance = _text(ACCEPTANCE)

    fr = set(re.findall(r"^-\s+`(FR-\d{3})`\.", requirements, flags=re.MULTILINE))
    nfr = set(re.findall(r"^-\s+`(NFR-\d{3})`\.", requirements, flags=re.MULTILINE))
    ac = set(re.findall(r"^-\s+`(AC-\d{3})`\.", acceptance, flags=re.MULTILINE))

    assert fr == _expected("FR", 55)
    assert nfr == _expected("NFR", 14)
    assert ac == _expected("AC", 59)


def test_product_traceability_has_one_row_for_every_fr_and_nfr() -> None:
    traceability = _text(TRACEABILITY)

    assert _first_column_ids(traceability, "FR", backticked=True) == _expected("FR", 55)
    assert _first_column_ids(traceability, "NFR", backticked=True) == _expected("NFR", 14)


def test_architecture_mapping_has_complete_requirement_and_gate_rows() -> None:
    mapping = _text(ARCH_MAPPING)

    assert _first_column_ids(mapping, "FR", backticked=False) == _expected("FR", 55)
    assert _first_column_ids(mapping, "NFR", backticked=False) == _expected("NFR", 14)
    assert _first_column_ids(mapping, "AC", backticked=False) == _expected("AC", 59)


def test_catalog_definitions_and_corpus_capability_references_match() -> None:
    catalog_ids = set(
        re.findall(
            r"^\|\s*`(CAP-[A-Z0-9]+(?:-[A-Z0-9]+)+)`\s*\|",
            _text(CATALOG),
            flags=re.MULTILINE,
        )
    )
    corpus = yaml.safe_load(_text(CORPUS))
    corpus_ids = {
        capability
        for scenario in corpus["scenarios"]
        for capability in scenario["capability_ids"]
    }

    assert len(catalog_ids) == 87
    assert corpus_ids == catalog_ids


def test_no_traceability_reference_points_outside_declared_id_ranges() -> None:
    declared = _expected("FR", 55) | _expected("NFR", 14) | _expected("AC", 59)
    referenced = set(
        re.findall(
            r"\b(?:FR|NFR|AC)-\d{3}\b",
            "\n".join(_text(path) for path in (TRACEABILITY, ARCH_MAPPING)),
        )
    )

    assert referenced <= declared


def test_performance_scenario_sets_match_acceptance_requirements() -> None:
    acceptance = _text(ACCEPTANCE)
    ac_047 = re.search(
        r"^-\s+`AC-047`\.(.*?)(?=^-\s+`AC-048`\.)",
        acceptance,
        flags=re.MULTILINE | re.DOTALL,
    )
    ac_048 = re.search(
        r"^-\s+`AC-048`\.(.*?)(?=^-\s+`AC-049`\.)",
        acceptance,
        flags=re.MULTILINE | re.DOTALL,
    )
    manifest = yaml.safe_load(_text(ORACLE_MANIFEST))["performance_policy"]

    assert ac_047 is not None
    assert ac_048 is not None
    assert re.findall(r"Q\d{3}", ac_047.group(1)) == BASIC_PERFORMANCE_SCENARIOS
    assert re.findall(r"Q\d{3}", ac_048.group(1)) == ["Q001", "Q116"]
    assert manifest["basic_scenarios"] == BASIC_PERFORMANCE_SCENARIOS
    assert manifest["basic_p100_max_seconds"] == 30
    assert manifest["supported_scenario_range"] == ["Q001", "Q116"]
    assert manifest["supported_p95_max_seconds"] == 90


def test_all_87_capabilities_are_mandatory_in_current_baseline() -> None:
    acceptance = _text(ACCEPTANCE)
    ac_001 = re.search(
        r"^-\s+`AC-001`\.(.*?)(?=^-\s+`AC-002`\.)",
        acceptance,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert ac_001 is not None
    assert "87" in ac_001.group(1)
    assert "не допускается" in ac_001.group(1)


def test_deepseek_json_mode_smoke_has_source_inventory_evidence() -> None:
    inventory = _text(SOURCE_INVENTORY)
    deepseek = re.search(
        r"^## DeepSeek$(.*?)(?=^## |\Z)",
        inventory,
        flags=re.MULTILINE | re.DOTALL,
    )

    assert deepseek is not None
    evidence = deepseek.group(1)
    assert "HTTP `200`" in evidence
    assert 'response_format={"type":"json_object"}' in evidence
    assert "валидный JSON" in evidence
    assert "Значение ключа не выводилось и не сохранялось" in evidence
