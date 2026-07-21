"""Command-line boundary over the same slice 1 application use cases."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Literal
from uuid import UUID

import uvicorn

from chatbot1c.application.errors import ApplicationError
from chatbot1c.bootstrap import RuntimeApplication, build_runtime
from chatbot1c.config import load_settings
from chatbot1c.contracts.errors import ContractValidationError
from chatbot1c.domain.package import SkillPackage
from chatbot1c.domain.skill import Skill


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "start":
        return _start(args)
    runtime: RuntimeApplication | None = None
    try:
        settings = load_settings(args.env_file)
        runtime = build_runtime(settings, auto_import=False)
        result = _dispatch(runtime, args)
        _print_json(result)
        return 0
    except ContractValidationError as error:
        _print_json(
            {
                "status": "rejected",
                "errors": [issue.model_dump(mode="json") for issue in error.issues],
            }
        )
        return 2
    except ApplicationError as error:
        _print_json(
            {
                "status": "rejected",
                "error": {"code": error.code, "message_ru": error.message_ru},
            }
        )
        return 3 if error.status_code == 409 else 4
    except (OSError, ValueError) as error:
        _print_json(
            {
                "status": "rejected",
                "error": {"code": "CLI_INPUT_ERROR", "message_ru": str(error)},
            }
        )
        return 2
    finally:
        if runtime is not None:
            asyncio.run(runtime.close())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chatbot1c")
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="Запустить локальный web server")
    start.add_argument("--host")
    start.add_argument("--port", type=int)

    skills = commands.add_parser("skills", help="Управление каталогом навыков")
    skill_commands = skills.add_subparsers(dest="skills_command", required=True)
    validate = skill_commands.add_parser("validate")
    validate.add_argument("file", type=Path)
    skill_import = skill_commands.add_parser("import")
    _import_arguments(skill_import)
    skill_export = skill_commands.add_parser("export")
    _export_arguments(skill_export)
    skill_delete = skill_commands.add_parser("delete")
    skill_delete.add_argument("skill_id")
    skill_delete.add_argument("--if-match", required=True)
    skill_commands.add_parser("list")

    docs = commands.add_parser("docs", help="Индекс встроенной справки")
    docs_commands = docs.add_subparsers(dest="docs_command", required=True)
    build_index = docs_commands.add_parser("build-index")
    build_index.add_argument("--config-dir", type=Path)

    diagnostics = commands.add_parser("diagnostics")
    diagnostic_commands = diagnostics.add_subparsers(
        dest="diagnostics_command", required=True
    )
    diagnostic_export = diagnostic_commands.add_parser("export")
    diagnostic_export.add_argument("trace_id", type=UUID)
    diagnostic_export.add_argument("--output", type=Path, required=True)

    index_alias = commands.add_parser("index")
    index_alias.add_argument("--config-dir", type=Path)
    import_alias = commands.add_parser("import")
    _import_arguments(import_alias)
    export_alias = commands.add_parser("export")
    _export_arguments(export_alias)
    return parser


def _import_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path)
    parser.add_argument("--mode", choices=("create", "replace"), default="create")
    parser.add_argument("--if-match")


def _export_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("skill_id", nargs="?")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--with-dependencies", action="store_true")
    parser.add_argument("--output", type=Path, required=True)


def _start(args: argparse.Namespace) -> int:
    from chatbot1c.web import create_app

    settings = load_settings(args.env_file)
    host = args.host or settings.app_host
    port = args.port or settings.app_port
    uvicorn.run(
        create_app(settings=settings),
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
    )
    return 0


def _dispatch(runtime: RuntimeApplication, args: argparse.Namespace) -> dict[str, object]:
    if args.command == "skills":
        if args.skills_command == "validate":
            return _validate(runtime, args.file)
        if args.skills_command == "import":
            return _import(runtime, args.file, args.mode, args.if_match)
        if args.skills_command == "export":
            return _export(
                runtime,
                args.skill_id,
                args.all,
                args.with_dependencies,
                args.output,
            )
        if args.skills_command == "delete":
            catalog = runtime.catalog_service.delete_skill(
                args.skill_id, if_match=args.if_match
            )
            return {
                "status": "accepted",
                "catalog_revision": catalog.revision,
                "catalog_snapshot_id": str(catalog.snapshot_id),
            }
        if args.skills_command == "list":
            return _catalog(runtime)
    if args.command == "docs" and args.docs_command == "build-index":
        return _index(runtime, args.config_dir)
    if args.command == "diagnostics" and args.diagnostics_command == "export":
        payload = runtime.diagnostics.export(args.trace_id)
        _write(args.output, payload)
        return {
            "status": "accepted",
            "trace_id": str(args.trace_id),
            "output": str(args.output),
            "bytes": len(payload),
        }
    if args.command == "index":
        return _index(runtime, args.config_dir)
    if args.command == "import":
        return _import(runtime, args.file, args.mode, args.if_match)
    if args.command == "export":
        return _export(
            runtime,
            args.skill_id,
            args.all,
            args.with_dependencies,
            args.output,
        )
    raise ValueError("unsupported command")


def _validate(runtime: RuntimeApplication, path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    document = runtime.contracts.validate_json_bytes(
        payload,
        available_skills=tuple(runtime.catalog.pin().skills.values()),
    )
    if isinstance(document, SkillPackage):
        document = runtime.catalog_service.validate_package(payload)
        identity = document.package_id
    elif isinstance(document, Skill):
        identity = document.skill_id
    else:
        raise ApplicationError(
            "SKILL_DOCUMENT_REQUIRED",
            "Команда принимает portable skill или skill package.",
            422,
        )
    return {
        "status": "accepted",
        "document_type": document.document_type,
        "identity": identity,
        "version": document.version,
        "digest": document.integrity.digest,
    }


def _import(
    runtime: RuntimeApplication,
    path: Path,
    mode: str,
    if_match: str | None,
) -> dict[str, object]:
    if mode not in {"create", "replace"}:
        raise ValueError("mode must be create or replace")
    typed_mode: Literal["create", "replace"] = (
        "create" if mode == "create" else "replace"
    )
    result = runtime.catalog_service.import_package(
        path.read_bytes(),
        mode=typed_mode,
        if_match=if_match,
    )
    return {
        "status": "accepted",
        "catalog_revision": result.revision,
        "catalog_snapshot_id": str(result.snapshot_id),
        "skills": [
            {"skill_id": skill_id, "version": version, "digest": digest}
            for skill_id, version, digest in result.skills
        ],
    }


def _export(
    runtime: RuntimeApplication,
    skill_id: str | None,
    all_skills: bool,
    with_dependencies: bool,
    output: Path,
) -> dict[str, object]:
    if all_skills:
        payload = runtime.catalog_service.export_package()
        identity = "catalog"
    elif skill_id is not None:
        payload = runtime.catalog_service.export_skill(
            skill_id,
            closure="embedded" if with_dependencies else "bare",
        )
        identity = skill_id
    else:
        raise ValueError("skill_id or --all is required")
    _write(output, payload)
    return {
        "status": "accepted",
        "identity": identity,
        "output": str(output),
        "bytes": len(payload),
        "catalog_revision": runtime.catalog.pin().revision,
    }


def _index(runtime: RuntimeApplication, config_dir: Path | None) -> dict[str, object]:
    source = config_dir or runtime.settings.ut_config_dir
    if source is None:
        raise ApplicationError(
            "HELP_SOURCE_NOT_CONFIGURED",
            "Укажите --config-dir или UT_CONFIG_DIR.",
            422,
        )
    result = runtime.help_builder.build(source)
    return {
        "status": "accepted",
        "revision": result.revision,
        "manifest_digest": result.manifest_digest,
        "source_count": result.source_count,
        "chunk_count": result.chunk_count,
    }


def _catalog(runtime: RuntimeApplication) -> dict[str, object]:
    catalog = runtime.catalog.pin()
    return {
        "status": "accepted",
        "catalog_revision": catalog.revision,
        "catalog_snapshot_id": str(catalog.snapshot_id),
        "skills": [
            {
                "skill_id": skill.skill_id,
                "version": skill.version,
                "digest": skill.integrity.digest,
            }
            for skill in catalog.skills.values()
        ],
    }


def _write(path: Path, payload: bytes) -> None:
    path.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _print_json(value: dict[str, object]) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
