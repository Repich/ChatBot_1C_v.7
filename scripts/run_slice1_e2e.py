"""Run the independent slice-1 E2E suite with local deterministic dependencies."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, TextIO
from urllib.error import URLError
from urllib.request import urlopen

from chatbot1c.contracts.digest import generate_integrity
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.package import SkillPackage

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills/ut-11.5.27.56/ut.starter.slice-one.package.json"
RESPONSES = ROOT / "src/chatbot1c/resources/slice1-planner-responses.json"
CANARY = "SYNTHETIC-SLICE1-SECRET-CANARY"


def _port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_http(url: str, process: subprocess.Popen[str], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with code {process.returncode}")
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310
                if response.status < 500:
                    return
        except (OSError, URLError):
            time.sleep(0.1)
    raise TimeoutError(f"server did not become live: {url}")


def _help_fixture(root: Path) -> None:
    extension = root / "Documents/CustomerOrder/Ext"
    help_dir = extension / "Help"
    help_dir.mkdir(parents=True)
    (extension / "Help.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><Help><Page>ru</Page></Help>',
        encoding="utf-8",
    )
    (help_dir / "ru.html").write_bytes(
        b"\xef\xbb\xbf"
        + (
            "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01 Transitional//EN\">"
            "<html><body><h1>Заказ клиента</h1><a name=\"definition\"></a>"
            "<h2>Назначение</h2><p>Заказ клиента - это запрос клиента на "
            "поставку товаров или оказание услуг в установленные сроки.</p>"
            "</body></html>"
        ).encode("utf-8")
    )


def _replacement_package(target: Path) -> tuple[Path, str]:
    harness = ContractHarness.discover(ROOT)
    source = harness.validate_json_bytes(PACKAGE.read_bytes())
    if not isinstance(source, SkillPackage):
        raise TypeError("starter fixture is not a package")
    original = next(
        skill
        for skill in source.skills
        if skill.skill_id == "ut115.ref.item.resolve-article-exact"
    )
    skill_document = original.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    skill_document["version"] = "1.0.1"
    skill_document["provenance"]["change_note_ru"] = (
        "Fixture hot-reload replacement без изменения query contract."
    )
    replacement_skill = generate_integrity(skill_document)

    package_document: dict[str, Any] = {
        "schema_version": "1.0.0",
        "document_type": "skill_package",
        "package_id": "ut.starter.slice-one-replacement",
        "version": "1.0.1",
        "display": {
            "name_ru": "Fixture замена поиска по артикулу",
            "description_ru": "Детерминированный package для проверки hot reload.",
        },
        "target": source.target.model_dump(mode="json", by_alias=True),
        "skills": [replacement_skill],
        "dependency_lock": [
            {
                "skill_id": replacement_skill["skill_id"],
                "version": replacement_skill["version"],
                "digest": replacement_skill["integrity"]["digest"],
            }
        ],
        "provenance": {
            "author": "ChatBot 1C fixture runner",
            "created_at": original.provenance.created_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "release_note_ru": "Hot reload fixture replacement.",
            "source_references": [
                {
                    "kind": "test_evidence",
                    "uri": "test-evidence://slice1/hot-reload",
                }
            ],
        },
    }
    signed = generate_integrity(package_document)
    validated = harness.validate_document(signed)
    if not isinstance(validated, SkillPackage):
        raise TypeError("replacement fixture is not a package")
    path = target / "replacement.package.json"
    path.write_text(
        json.dumps(signed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path, original.integrity.digest


def _app_environment(
    *, data_dir: Path, fixture_url: str, help_root: Path, auto_import: bool
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_DATA_DIR": str(data_dir),
            "AUTO_IMPORT_BUILTIN_SKILLS": "true" if auto_import else "false",
            "STARTER_PACKAGE_PATH": str(PACKAGE),
            "BUILD_HELP_INDEX_ON_START": "true",
            "UT_CONFIG_DIR": str(help_root),
            "DEEPSEEK_BASE_URL": fixture_url,
            "DEEPSEEK_API_KEY": CANARY,
            "MCP_URL": f"{fixture_url}/mcp",
            "LOG_LEVEL": "WARNING",
        }
    )
    return environment


def _start_app(
    *, port: int, environment: dict[str, str], log_path: Path
) -> tuple[subprocess.Popen[str], TextIO]:
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "chatbot1c.cli",
            "--env-file",
            str(log_path.with_suffix(".absent.env")),
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(work: Path) -> int:
    _help_fixture(work / "config")
    replacement, replace_digest = _replacement_package(work)
    fixture = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/slice1_fixture_server.py")],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert fixture.stdout is not None
    fixture_url = json.loads(fixture.stdout.readline())["base_url"]
    normal_port, clean_port = _port(), _port()
    normal, normal_log = _start_app(
        port=normal_port,
        environment=_app_environment(
            data_dir=work / "normal-data",
            fixture_url=fixture_url,
            help_root=work / "config",
            auto_import=True,
        ),
        log_path=work / "normal.log",
    )
    clean, clean_log = _start_app(
        port=clean_port,
        environment=_app_environment(
            data_dir=work / "clean-data",
            fixture_url=fixture_url,
            help_root=work / "config",
            auto_import=False,
        ),
        log_path=work / "clean.log",
    )
    try:
        _wait_http(f"http://127.0.0.1:{normal_port}/api/v1/health/live", normal)
        _wait_http(f"http://127.0.0.1:{clean_port}/api/v1/health/live", clean)
        environment = os.environ.copy()
        environment.update(
            {
                "SLICE1_BASE_URL": f"http://127.0.0.1:{normal_port}",
                "SLICE1_CLEAN_BASE_URL": f"http://127.0.0.1:{clean_port}",
                "SLICE1_FIXTURE_URL": fixture_url,
                "SLICE1_PLANNER_RESPONSES_PATH": str(RESPONSES),
                "SLICE1_PACKAGE_PATH": str(PACKAGE),
                "SLICE1_REPLACEMENT_PACKAGE_PATH": str(replacement),
                "SLICE1_REPLACE_IF_MATCH": replace_digest,
                "SLICE1_HOT_RELOAD_QUESTION": (
                    "Найди номенклатуру с артикулом V100123588."
                ),
                "SLICE1_CLI": " ".join(
                    shlex.quote(item)
                    for item in (
                        sys.executable,
                        "-m",
                        "chatbot1c.cli",
                        "--env-file",
                        str(work / "cli.absent.env"),
                    )
                ),
                "SLICE1_SECRET_CANARY": CANARY,
            }
        )
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/e2e"],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        return completed.returncode
    finally:
        _stop(normal)
        _stop(clean)
        _stop(fixture)
        normal_log.close()
        clean_log.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.work_dir is not None:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        return run(args.work_dir.resolve())
    with tempfile.TemporaryDirectory(prefix="chatbot1c-slice1-e2e-") as temporary:
        return run(Path(temporary))


if __name__ == "__main__":
    raise SystemExit(main())
