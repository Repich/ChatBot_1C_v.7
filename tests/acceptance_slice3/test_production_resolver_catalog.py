from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from chatbot1c.bootstrap import build_runtime
from chatbot1c.config import Settings
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.skill import Skill

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills/ut-11.5.27.56"
PROFILE = ROOT / "src/chatbot1c/resources/ut-11.5.27.56-profile.json"
REFERENCE = SKILLS / "ut115-reference-1.1.0.package.json"
UPGRADE = SKILLS / "ut115-reference-existing-upgrade-1.1.0.package.json"
ADDITIONS = SKILLS / "ut115-reference-slice3-additions-1.0.0.package.json"
STARTER = SKILLS / "ut.starter.slice-three.package.json"
ANALYTICS_SHA = "4f88fd400e114ac936b34c710bee40efb7abe4a9f8c833b2a32619e90e52880e"


def _load_generator() -> ModuleType:
    path = ROOT / "scripts/build_slice3_resolver_skills.py"
    spec = importlib.util.spec_from_file_location("build_slice3_resolver_skills", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load generator from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()
CONSUMER_IDS = GENERATOR.CONSUMER_IDS
DOCUMENT_PRODUCER_IDS = GENERATOR.DOCUMENT_PRODUCER_IDS
LEGACY_BYTES = GENERATOR.LEGACY_BYTES
R01_IDS = GENERATOR.R01_IDS
R02_R12_IDS = GENERATOR.R02_R12_IDS
build_packages = GENERATOR.build_packages
build_skills = GENERATOR.build_skills


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _generated_skills() -> dict[str, dict]:
    return {item["skill_id"]: item for item in build_skills()}


def test_frozen_inventory_is_exact_27_22_5_plus_typed_closure() -> None:
    assert len(R02_R12_IDS) == 27
    assert len(CONSUMER_IDS) == 5
    assert len(R02_R12_IDS - CONSUMER_IDS) == 22
    assert len(R01_IDS) == 4
    assert len(DOCUMENT_PRODUCER_IDS) == 5
    generated = _generated_skills()
    assert set(generated) == R01_IDS | R02_R12_IDS | DOCUMENT_PRODUCER_IDS
    assert len(generated) == 36


def test_every_generated_skill_is_strict_v11_and_has_three_fixture_outcomes() -> None:
    harness = ContractHarness.discover(ROOT)
    for skill_id, payload in _generated_skills().items():
        validated = harness.validate_document(payload)
        assert isinstance(validated, Skill), skill_id
        assert payload["schema_version"] == "1.1.0"
        assert all("context_slot_keys" in item for item in payload["parameters"])
        statuses = {item["expected"]["status"] for item in payload["tests"]}
        assert "success_with_rows" in statuses, skill_id
        assert "success_empty" in statuses, skill_id
        assert "query_error" in statuses, skill_id


def test_resolvers_and_consumers_have_exact_context_contracts() -> None:
    skills = _generated_skills()
    for skill_id in R02_R12_IDS - CONSUMER_IDS:
        output = skills[skill_id]["output_contract"]
        assert output["cardinality"] == "many", skill_id
        assert output["resolution"]["protocol"] == "typed_entity_resolver_v1"
        identity = output["resolution"]["identity_fact_id"]
        assert identity in output["row_identity_fact_ids"]
        assert output["context_export_policy"] == [
            {
                "fact_id": identity,
                "slot_key": output["resolution"]["default_slot_key"],
                "mode": "selected_only",
                "lifetime": {"mode": "session"},
                "max_members": 100,
            }
        ]
    for skill_id in CONSUMER_IDS:
        output = skills[skill_id]["output_contract"]
        assert output["cardinality"] == "many"
        assert output["resolution"] is None
        assert output["context_export_policy"] == []
        assert skills[skill_id]["result_constraints"]


def test_role_qualified_resolvers_are_separate_and_metadata_proven() -> None:
    skills = _generated_skills()
    for criterion in ("name-contains", "code-exact", "inn-exact"):
        partner = skills[f"ut115.ref.partner.resolve-{criterion}"]
        customer = skills[f"ut115.ref.customer.resolve-{criterion}"]
        supplier = skills[f"ut115.ref.supplier.resolve-{criterion}"]
        assert partner["output_contract"]["resolution"]["role_proof_fact_ids"] == []
        assert customer["output_contract"]["resolution"]["role_proof_fact_ids"] == [
            "partner.is_customer"
        ]
        assert supplier["output_contract"]["resolution"]["role_proof_fact_ids"] == [
            "partner.is_supplier"
        ]
        assert "party.customer" in customer["provides"]["fact_types"]
        assert "party.supplier" in supplier["provides"]["fact_types"]
    enterprise = skills["ut115.ref.cash-desk.enterprise.resolve"]
    pos = skills["ut115.ref.cash-desk.pos.resolve"]
    assert enterprise["output_contract"]["facts"][0]["semantic_type"] == (
        "finance.cash_desk.enterprise"
    )
    assert pos["output_contract"]["facts"][0]["semantic_type"] == (
        "finance.cash_desk.pos"
    )
    assert enterprise["result_constraints"][0]["parameter"] == "organization"
    assert pos["result_constraints"][0]["parameter"] == "organization"
    warehouse = skills["ut115.ref.warehouse.resolve"]
    assert warehouse["output_contract"]["resolution"]["role_proof_fact_ids"] == []


def test_r11_uses_only_item_analytics_relation_and_exact_hash() -> None:
    skills = _generated_skills()
    for skill_id in (
        "ut115.ref.item-series.resolve-name-contains",
        "ut115.ref.item-series.resolve-number-exact",
    ):
        skill = skills[skill_id]
        query = skill["operation"]["query_template"]["text"]
        assert "РегистрСведений.АналитикаУчетаНоменклатуры" in query
        assert "Аналитика.Номенклатура = &Номенклатура" in query
        assert "Аналитика.Характеристика = &Характеристика" in query
        assert "Аналитика.МестоХранения = &МестоХранения" in query
        assert "Аналитика.Назначение = &Назначение" in query
        assert "ВладелецСерий" not in query
        assert "ВидНоменклатуры" not in query
        references = skill["provenance"]["source_references"]
        analytics = next(
            item
            for item in references
            if item["uri"].endswith(
                "/InformationRegisters/АналитикаУчетаНоменклатуры.xml"
            )
        )
        assert analytics["sha256"] == ANALYTICS_SHA


def test_codelength_zero_catalogs_never_advertise_code() -> None:
    profile = _load(PROFILE)["metadata"]
    no_code = {
        "Справочник.Организации",
        "Справочник.Склады",
        "Справочник.ВидыЦен",
        "Справочник.Контрагенты",
        "Справочник.СерииНоменклатуры",
    }
    for object_name in no_code:
        assert "Код" not in profile[object_name], object_name
    skills = _generated_skills()
    for skill_id in (
        "ut115.ref.organization.resolve-name-contains",
        "ut115.ref.organization.resolve-inn-exact",
        "ut115.ref.organization.resolve-kpp-exact",
    ):
        query = skills[skill_id]["operation"]["query_template"]["text"]
        assert "Организации.Код" not in query
        assert "organization.code" not in json.dumps(
            skills[skill_id], ensure_ascii=False
        )
    assert not {
        "ut115.ref.organization.resolve-code-exact",
        "ut115.ref.warehouse.resolve-code-exact",
        "ut115.ref.price-type.resolve-code-exact",
    } & set(skills)


def test_profile_contains_every_declared_required_metadata_attribute() -> None:
    profile = _load(PROFILE)["metadata"]
    for skill_id, skill in _generated_skills().items():
        for requirement in skill["compatibility"]["required_metadata"]:
            object_name = requirement["object_name"]
            assert object_name in profile, (skill_id, object_name)
            missing = set(requirement["attributes"]) - set(profile[object_name])
            assert not missing, (skill_id, object_name, missing)


def test_package_topology_locks_and_overlapping_bytes_are_exact() -> None:
    harness = ContractHarness.discover(ROOT)
    paths = [REFERENCE, UPGRADE, ADDITIONS, STARTER]
    packages = {path.name: _load(path) for path in paths}
    generated = _generated_skills()
    available = [
        Skill.model_validate(generated[skill_id])
        for skill_id in sorted(R01_IDS | {"ut115.ref.warehouse.resolve"})
    ]
    for name, package in packages.items():
        validated = harness.validate_document(
            package,
            available_skills=available if name == ADDITIONS.name else (),
        )
        assert isinstance(validated, SkillPackage)
        ids = [item["skill_id"] for item in package["skills"]]
        assert len(ids) == len(set(ids))
        lock_pairs = [
            (item["skill_id"], item["version"])
            for item in package["dependency_lock"]
        ]
        assert len(lock_pairs) == len(set(lock_pairs))
    assert len(packages[REFERENCE.name]["skills"]) == 31
    assert len(packages[UPGRADE.name]["skills"]) == 5
    assert len(packages[ADDITIONS.name]["skills"]) == 26
    assert len(packages[STARTER.name]["skills"]) == 39
    full_by_id = {
        item["skill_id"]: item for item in packages[REFERENCE.name]["skills"]
    }
    for name in (UPGRADE.name, ADDITIONS.name):
        for embedded in packages[name]["skills"]:
            assert embedded == full_by_id[embedded["skill_id"]]


def test_generator_is_deterministic_and_preserves_all_published_bytes() -> None:
    before = {
        name: hashlib.sha256((SKILLS / name).read_bytes()).hexdigest()
        for name in LEGACY_BYTES
    }
    assert before == LEGACY_BYTES
    generated = build_skills()
    packages = build_packages(generated)
    for skill in generated:
        path = SKILLS / f"{skill['skill_id']}-{skill['version']}.skill.json"
        assert path.read_text(encoding="utf-8") == (
            json.dumps(skill, ensure_ascii=False, indent=2) + "\n"
        )
    for name, package in packages.items():
        assert _load(SKILLS / name) == package
    after = {
        name: hashlib.sha256((SKILLS / name).read_bytes()).hexdigest()
        for name in LEGACY_BYTES
    }
    assert after == before


def test_reference_package_round_trips_through_two_clean_catalogs(
    tmp_path: Path,
) -> None:
    source = build_runtime(
        Settings(app_data_dir=tmp_path / "source", auto_import_builtin_skills=False),
        auto_import=False,
    )
    target = build_runtime(
        Settings(app_data_dir=tmp_path / "target", auto_import_builtin_skills=False),
        auto_import=False,
    )
    try:
        source.catalog_service.import_package(REFERENCE.read_bytes())
        ids = tuple(sorted(source.catalog.pin().skills))
        assert set(ids) == R01_IDS | R02_R12_IDS
        exported = source.catalog_service.export_package(ids)
        target.catalog_service.import_package(exported)
        assert set(target.catalog.pin().skills) == set(ids)
        for skill_id in ids:
            assert (
                target.catalog.pin().skills[skill_id].integrity.digest
                == source.catalog.pin().skills[skill_id].integrity.digest
            )
    finally:
        asyncio.run(source.close())
        asyncio.run(target.close())
