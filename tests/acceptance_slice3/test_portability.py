from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlencode

from .support import FixtureClient
from .synthetic_package import ASSET_SKILL, DETAIL_SKILL, PHYSICAL_TYPE, SET_SKILL

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_strict_v11_package_imports_into_empty_catalog(
    app_factory, portable_package: bytes
) -> None:
    clean = app_factory(name="strict-import", import_package=False)
    assert clean.api is not None
    response = clean.api.import_package(portable_package)
    assert response.status == 200, response.body.decode("utf-8", "replace")
    imported = {item["skill_id"] for item in response.json()["skills"]}
    assert ASSET_SKILL in imported


def test_synthetic_package_runs_in_two_clean_data_directories(
    app_factory, fixture: FixtureClient
) -> None:
    first = app_factory(name="portable-one")
    second = app_factory(name="portable-two")
    for app, ordinal in ((first, 81), (second, 82)):
        assert app.api is not None
        fixture.configure("resolve_one", asset_count=1, uuid_start=ordinal)
        session = app.api.create_session(f"portable-{ordinal}")
        turn = app.api.ask(
            session["session_id"],
            "Найди ультрамариновый артефакт и покажи synthetic snapshot",
            context_version=session["context_version"],
        )
        assert turn["outcome"] == "success_with_rows"
        skills = app.api.http.request("GET", "/api/v1/skills").json()["skills"]
        assert ASSET_SKILL in {skill["skill_id"] for skill in skills}


def test_exported_package_round_trips_into_second_clean_instance(
    app_factory,
) -> None:
    source = app_factory(name="export-source")
    target = app_factory(name="export-target", import_package=False)
    assert source.api is not None and target.api is not None
    query = urlencode(
        [("skill_id", value) for value in (ASSET_SKILL, DETAIL_SKILL, SET_SKILL)]
    )
    exported = source.api.http.request("GET", f"/api/v1/skill-packages/export?{query}")
    assert exported.status == 200
    assert b"/Users/" not in exported.body and b"C:\\" not in exported.body
    imported = target.api.import_package(exported.body)
    assert imported.status == 200, imported.body.decode("utf-8", "replace")
    assert {skill["skill_id"] for skill in imported.json()["skills"]} == {
        ASSET_SKILL,
        DETAIL_SKILL,
        SET_SKILL,
    }


def test_unseen_vocabulary_executes_without_application_source_change(
    api, fixture: FixtureClient
) -> None:
    fixture.configure("resolve_one", asset_count=1, uuid_start=83)
    session = api.create_session()
    turn = api.ask(
        session["session_id"],
        "Покажи кобальтовый квазиснимок ультрамаринового артефакта",
        context_version=session["context_version"],
    )
    assert turn["outcome"] == "success_with_rows"


def test_source_has_no_object_qid_or_lexical_selection_branches() -> None:
    source_files = sorted((REPO_ROOT / "src/chatbot1c").rglob("*.py"))
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    forbidden_literals = [
        "synthetic.asset",
        "selection.synthetic_asset",
        PHYSICAL_TYPE,
        "СинтетическиеАктивы",
        "_DOC_SIGNALS",
        "_DATA_SIGNALS",
        "def _intent(",
    ]
    assert not [literal for literal in forbidden_literals if literal in source]
    assert re.search(r"\bQ(?:0[0-9]{2}|1[01][0-9])\b", source) is None


def test_source_does_not_map_semantic_prefixes_to_physical_classes() -> None:
    source_files = sorted((REPO_ROOT / "src/chatbot1c").rglob("*.py"))
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    patterns = [
        r"startswith\([\"']catalog\.",
        r"startswith\([\"']document\.",
        r"startswith\([\"']СправочникСсылка\.",
        r"startswith\([\"']ДокументСсылка\.",
    ]
    assert not [pattern for pattern in patterns if re.search(pattern, source)]
