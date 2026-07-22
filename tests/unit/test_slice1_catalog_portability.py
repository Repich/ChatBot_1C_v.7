from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chatbot1c.bootstrap import build_runtime
from chatbot1c.cli import main
from chatbot1c.config import Settings
from chatbot1c.contracts.errors import ContractValidationError
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.skill import Skill
from chatbot1c.web import create_app

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STARTER = (
    ROOT / "skills/ut-11.5.27.56/ut.starter.slice-three.package.json"
)
LEGACY_STARTER_SHA256 = {
    "ut.starter.slice-one.package.json": (
        "4de313a696cf9bff478746b5d0fe9e779948b090ac3f277785c2f4818df01420"
    ),
    "ut.starter.slice-two.package.json": (
        "e0f7e68d7a0bcfcc89705e6e445b8b3cef9e577199efd24ba7792acd7ed3fc5b"
    ),
}
ARTICLE_SKILL = "ut115.ref.item.resolve-article-exact"
ORDER_LINES_SKILL = "ut115.sales.order-lines"


def _runtime(path: Path, *, auto_import: bool) -> Any:
    return build_runtime(
        Settings(app_data_dir=path, auto_import_builtin_skills=auto_import),
        auto_import=auto_import,
    )


def _assert_no_null(value: object) -> None:
    assert value is not None
    if isinstance(value, dict):
        for child in value.values():
            _assert_no_null(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_null(child)


def test_exported_skill_and_explicit_closure_round_trip_clean_catalogs(
    tmp_path: Path,
) -> None:
    source = _runtime(tmp_path / "source", auto_import=True)
    leaf = source.catalog_service.export_skill(ARTICLE_SKILL)
    dependency_bare = source.catalog_service.export_skill(ORDER_LINES_SKILL)
    embedded = source.catalog_service.export_skill(
        ORDER_LINES_SKILL, closure="embedded"
    )

    leaf_document = source.contracts.validate_json_bytes(leaf)
    bare_document = source.contracts.validate_json_bytes(dependency_bare)
    package_document = source.contracts.validate_json_bytes(embedded)
    assert isinstance(leaf_document, Skill)
    assert isinstance(bare_document, Skill)
    assert isinstance(package_document, SkillPackage)
    _assert_no_null(json.loads(leaf))
    _assert_no_null(json.loads(embedded))
    assert embedded == source.catalog_service.export_skill(
        ORDER_LINES_SKILL, closure="embedded"
    )

    other_subset = source.catalog_service.export_package((ARTICLE_SKILL,))
    other_document = source.contracts.validate_json_bytes(other_subset)
    assert isinstance(other_document, SkillPackage)
    assert other_document.package_id != package_document.package_id

    clean_leaf = _runtime(tmp_path / "clean-leaf", auto_import=False)
    clean_leaf.catalog_service.import_package(leaf)
    assert set(clean_leaf.catalog.pin().skills) == {ARTICLE_SKILL}

    clean_bare = _runtime(tmp_path / "clean-bare", auto_import=False)
    with pytest.raises(ContractValidationError) as bare_error:
        clean_bare.catalog_service.import_package(dependency_bare)
    assert {issue.code for issue in bare_error.value.issues} == {
        "SKILL_DEPENDENCY_MISSING"
    }
    assert not clean_bare.catalog.pin().skills

    clean_embedded = _runtime(tmp_path / "clean-embedded", auto_import=False)
    clean_embedded.catalog_service.import_package(embedded)
    assert set(clean_embedded.catalog.pin().skills) == {
        "ut115.sales.order-header-status-by-number",
        ORDER_LINES_SKILL,
    }

    for runtime in (source, clean_leaf, clean_bare, clean_embedded):
        asyncio.run(runtime.close())


def test_web_and_cli_export_identical_canonical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    web_runtime = _runtime(tmp_path / "web", auto_import=True)
    with TestClient(create_app(web_runtime)) as client:
        bare_web = client.get(f"/api/v1/skills/{ARTICLE_SKILL}/export")
        embedded_web = client.get(
            f"/api/v1/skills/{ORDER_LINES_SKILL}/export?closure=embedded"
        )
        all_web = client.get("/api/v1/skill-packages/export")
    assert (
        bare_web.status_code
        == embedded_web.status_code
        == all_web.status_code
        == 200
    )

    cli_data = tmp_path / "cli"
    env_file = tmp_path / "absent.env"
    monkeypatch.setenv("APP_DATA_DIR", str(cli_data))
    monkeypatch.setenv("AUTO_IMPORT_BUILTIN_SKILLS", "false")
    assert main(
        [
            "--env-file",
            str(env_file),
            "skills",
            "import",
            str(DEFAULT_STARTER),
        ]
    ) == 0
    capsys.readouterr()

    bare_path = tmp_path / "leaf.json"
    embedded_path = tmp_path / "embedded.json"
    all_path = tmp_path / "all.json"
    commands = (
        ["skills", "export", ARTICLE_SKILL, "--output", str(bare_path)],
        [
            "skills",
            "export",
            ORDER_LINES_SKILL,
            "--with-dependencies",
            "--output",
            str(embedded_path),
        ],
        ["skills", "export", "--all", "--output", str(all_path)],
    )
    for command in commands:
        assert main(["--env-file", str(env_file), *command]) == 0
        capsys.readouterr()

    assert bare_path.read_bytes() == bare_web.content
    assert embedded_path.read_bytes() == embedded_web.content
    assert all_path.read_bytes() == all_web.content


def test_published_legacy_starter_package_bytes_are_preserved() -> None:
    skills_dir = ROOT / "skills/ut-11.5.27.56"
    actual = {
        name: hashlib.sha256((skills_dir / name).read_bytes()).hexdigest()
        for name in LEGACY_STARTER_SHA256
    }
    assert actual == LEGACY_STARTER_SHA256
