from __future__ import annotations

import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_NAMES = {
    "evidence.schema.json",
    "planner-output.schema.json",
    "skill-package.schema.json",
    "skill.schema.json",
}
APPLICATION_RESOURCES = {
    "chatbot1c/static/app.css",
    "chatbot1c/static/app.js",
    "chatbot1c/templates/chat.html",
    "chatbot1c/resources/ut-11.5.27.56-profile.json",
    (
        "chatbot1c/builtin_skills/ut-11.5.27.56/"
        "ut.starter.slice-one.package.json"
    ),
}


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def test_built_wheel_loads_canonical_schemas_outside_checkout(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    _run(["uv", "build", "--clear", "--out-dir", str(dist)], ROOT)

    wheel = next(dist.glob("chatbot1c-*.whl"))
    sdist = next(dist.glob("chatbot1c-*.tar.gz"))
    expected_resources = {f"chatbot1c/schemas/{name}" for name in SCHEMA_NAMES}

    with zipfile.ZipFile(wheel) as archive:
        wheel_members = set(archive.namelist())
        assert expected_resources <= wheel_members
        assert APPLICATION_RESOURCES <= wheel_members
        for name in SCHEMA_NAMES:
            assert archive.read(f"chatbot1c/schemas/{name}") == (
                ROOT / "schemas" / name
            ).read_bytes()

    with tarfile.open(sdist, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        for name in SCHEMA_NAMES:
            suffix = f"/schemas/{name}"
            member = next(
                member for path, member in members.items() if path.endswith(suffix)
            )
            extracted = archive.extractfile(member)
            assert extracted is not None
            assert extracted.read() == (ROOT / "schemas" / name).read_bytes()
        assert any(path.endswith("/src/chatbot1c/templates/chat.html") for path in members)
        assert any(path.endswith("/src/chatbot1c/static/app.css") for path in members)
        assert any(
            path.endswith(
                "/skills/ut-11.5.27.56/ut.starter.slice-one.package.json"
            )
            for path in members
        )

    clean_cwd = tmp_path / "clean-cwd"
    clean_cwd.mkdir()
    venv = tmp_path / "runtime-venv"
    _run(["uv", "venv", "--python", "3.12", str(venv)], clean_cwd)
    python = _venv_python(venv)
    _run(["uv", "pip", "install", "--python", str(python), str(wheel)], clean_cwd)

    probe = """
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from chatbot1c.config import Settings
from chatbot1c.contracts import SchemaRepository
from chatbot1c.web import create_app

expected = {
    "evidence.schema.json",
    "planner-output.schema.json",
    "skill-package.schema.json",
    "skill.schema.json",
}
repository = SchemaRepository.discover()
resource_directory = files("chatbot1c").joinpath("schemas")
assert str(repository.schemas_dir) == str(resource_directory)
assert set(repository.names) == expected
for name in expected:
    schema = repository.schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
with TemporaryDirectory() as data_dir:
    settings = Settings(
        app_data_dir=Path(data_dir),
        auto_import_builtin_skills=True,
    )
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.css").status_code == 200
        catalog = client.get("/api/v1/skills").json()
        assert len(catalog["skills"]) == 8
print(",".join(repository.names))
"""
    result = _run([str(python), "-I", "-c", probe], clean_cwd)
    assert set(result.stdout.strip().split(",")) == SCHEMA_NAMES
