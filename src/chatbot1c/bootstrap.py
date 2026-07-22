"""Explicit composition roots for contract tooling and the slice 1 runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import cast

from chatbot1c.adapters.database_marker import ExplicitDatabaseStateMarker
from chatbot1c.adapters.deepseek import DeepSeekPlanner
from chatbot1c.adapters.diagnostics import DiagnosticExporter
from chatbot1c.adapters.help_index import HelpIndexBuilder, SQLiteHelpIndex
from chatbot1c.adapters.mcp import LiveMcpTransport, McpReadOnlyAdapter
from chatbot1c.adapters.persistence import SQLiteStore
from chatbot1c.application.catalog import CatalogManager, CatalogService
from chatbot1c.application.chat import ChatService
from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.execution import PlanExecutor
from chatbot1c.application.models import ConfigurationProfile, PlannerRequest
from chatbot1c.application.ports import (
    DatabaseStateMarkerPort,
    PlannerPort,
    ReadOnly1CPort,
)
from chatbot1c.application.shortlist import LexicalSkillShortlist
from chatbot1c.config import Settings, load_settings
from chatbot1c.contracts.digest import canonicalize
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.contracts.json_limits import validate_json_structure
from chatbot1c.domain.plan import PlannerOutput


@dataclass(frozen=True, slots=True)
class Application:
    settings: Settings
    contracts: ContractHarness


class UnavailablePlanner(PlannerPort):
    """Fail closed when the server was intentionally started without an API key."""

    def outbound_http_request(self, request: PlannerRequest) -> bytes | None:
        del request
        return None

    async def plan(self, request: PlannerRequest) -> PlannerOutput:
        del request
        raise ApplicationError(
            "LLM_UNAVAILABLE",
            "DeepSeek API key не настроен; запрос к данным не выполнялся.",
            503,
        )


@dataclass(slots=True)
class RuntimeApplication:
    settings: Settings
    contracts: ContractHarness
    store: SQLiteStore
    catalog: CatalogManager
    catalog_service: CatalogService
    help_index: SQLiteHelpIndex
    help_builder: HelpIndexBuilder
    planner: PlannerPort
    one_c: ReadOnly1CPort
    executor: PlanExecutor
    chat: ChatService
    diagnostics: DiagnosticExporter
    marker: DatabaseStateMarkerPort

    async def close(self) -> None:
        if isinstance(self.planner, DeepSeekPlanner):
            await self.planner.close()
        self.store.engine.dispose()


def build_application(
    settings: Settings | None = None,
    *,
    project_root: Path | str | None = None,
    env_file: Path | str | None = ".env.local",
) -> Application:
    """Compose local services without creating external adapters or reading at import."""

    resolved_settings = settings if settings is not None else load_settings(env_file)
    return Application(
        settings=resolved_settings,
        contracts=ContractHarness.discover(project_root),
    )


def build_runtime(
    settings: Settings | None = None,
    *,
    project_root: Path | str | None = None,
    env_file: Path | str | None = ".env.local",
    planner: PlannerPort | None = None,
    one_c: ReadOnly1CPort | None = None,
    profile: ConfigurationProfile | None = None,
    marker: DatabaseStateMarkerPort | None = None,
    auto_import: bool | None = None,
) -> RuntimeApplication:
    """Initialize all local state and compose bounded production adapters."""

    base = build_application(
        settings,
        project_root=project_root,
        env_file=env_file,
    )
    resolved = base.settings
    store = SQLiteStore(resolved.effective_database_url)
    store.initialize()
    configuration_profile = profile or load_configuration_profile(
        resolved.database_profile_path
    )
    catalog = CatalogManager(store)
    catalog_service = CatalogService(
        base.contracts,
        store,
        catalog,
        configuration_profile,
    )
    should_import = (
        resolved.auto_import_builtin_skills if auto_import is None else auto_import
    )
    if should_import and not catalog.pin().skills:
        catalog_service.import_package(_starter_package_bytes(resolved), mode="create")

    help_builder = HelpIndexBuilder(store.engine)
    if resolved.build_help_index_on_start:
        if resolved.ut_config_dir is None:
            raise ApplicationError(
                "HELP_SOURCE_NOT_CONFIGURED",
                "Для build_help_index_on_start требуется UT_CONFIG_DIR.",
                503,
            )
        help_builder.build(resolved.ut_config_dir)
    help_index = SQLiteHelpIndex(store.engine)
    active_help = help_index.active_revision()
    profile_digest = hashlib.sha256(
        canonicalize(
            {
                "configuration_id": configuration_profile.configuration_id,
                "configuration_name": configuration_profile.configuration_name,
                "release": configuration_profile.release,
                "compatibility_mode": configuration_profile.compatibility_mode,
                "metadata": {
                    name: sorted(attributes)
                    for name, attributes in sorted(configuration_profile.metadata.items())
                },
            }
        )
    ).hexdigest()
    marker_adapter = marker or ExplicitDatabaseStateMarker(
        marker_value=resolved.database_state_marker,
        configuration_profile_digest=profile_digest,
        documentation_revision=(active_help[0] if active_help else "unavailable"),
        documentation_manifest_digest=(
            active_help[1]
            if active_help
            else hashlib.sha256(b"unavailable").hexdigest()
        ),
    )

    planner_adapter = planner or _live_planner(resolved, base.contracts)
    one_c_adapter = one_c or McpReadOnlyAdapter(
        LiveMcpTransport(resolved.mcp_url, channel=resolved.mcp_channel)
    )
    executor = PlanExecutor(
        one_c_adapter,
        help_index,
        store,
        documentation_revision=(active_help[0] if active_help else "unavailable"),
        documentation_digest=(active_help[1] if active_help else None),
    )
    chat = ChatService(
        sessions=store,
        traces=store,
        catalog=catalog,
        planner=planner_adapter,
        executor=executor,
        harness=base.contracts,
        shortlist=LexicalSkillShortlist(),
        continuations=store,
        marker=marker_adapter,
        default_list_limit=resolved.default_list_limit,
        request_deadline_seconds=resolved.request_deadline_seconds,
    )
    secrets = (
        ()
        if resolved.deepseek_api_key is None
        else (resolved.deepseek_api_key.get_secret_value(),)
    )
    diagnostics = DiagnosticExporter(store, secret_values=secrets)
    return RuntimeApplication(
        settings=resolved,
        contracts=base.contracts,
        store=store,
        catalog=catalog,
        catalog_service=catalog_service,
        help_index=help_index,
        help_builder=help_builder,
        planner=planner_adapter,
        one_c=one_c_adapter,
        executor=executor,
        chat=chat,
        diagnostics=diagnostics,
        marker=marker_adapter,
    )


def load_configuration_profile(path: Path | None = None) -> ConfigurationProfile:
    payload = (
        path.expanduser().read_bytes()
        if path is not None
        else files("chatbot1c")
        .joinpath("resources", "ut-11.5.27.56-profile.json")
        .read_bytes()
    )
    if len(payload) > 1024 * 1024:
        raise ValueError("configuration profile exceeds 1 MiB")
    value: object = json.loads(payload)
    validate_json_structure(value)
    if not isinstance(value, dict):
        raise ValueError("configuration profile must be an object")
    document = cast(dict[str, object], value)
    expected_keys = {
        "configuration_id",
        "configuration_name",
        "release",
        "compatibility_mode",
        "metadata",
    }
    if set(document) != expected_keys:
        raise ValueError("configuration profile has missing or extra fields")
    metadata_value = document["metadata"]
    if not isinstance(metadata_value, dict):
        raise ValueError("configuration profile metadata must be an object")
    metadata: dict[str, frozenset[str]] = {}
    for object_name, attributes in metadata_value.items():
        if not isinstance(object_name, str) or not isinstance(attributes, list):
            raise ValueError("configuration profile metadata entry is invalid")
        if not all(isinstance(attribute, str) for attribute in attributes):
            raise ValueError("configuration metadata attributes must be strings")
        metadata[object_name] = frozenset(cast(list[str], attributes))
    strings = {
        key: document[key]
        for key in (
            "configuration_id",
            "configuration_name",
            "release",
            "compatibility_mode",
        )
    }
    if not all(isinstance(item, str) for item in strings.values()):
        raise ValueError("configuration profile identity fields must be strings")
    return ConfigurationProfile(
        configuration_id=cast(str, strings["configuration_id"]),
        configuration_name=cast(str, strings["configuration_name"]),
        release=cast(str, strings["release"]),
        compatibility_mode=cast(str, strings["compatibility_mode"]),
        metadata=metadata,
    )


def _live_planner(settings: Settings, contracts: ContractHarness) -> PlannerPort:
    if settings.deepseek_api_key is None:
        return UnavailablePlanner()
    return DeepSeekPlanner(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        harness=contracts,
    )


def _starter_package_bytes(settings: Settings) -> bytes:
    if settings.starter_package_path is not None:
        return settings.starter_package_path.expanduser().read_bytes()
    relative = Path(
        "ut-11.5.27.56",
        "ut.starter.slice-two.package.json",
    )
    packaged = files("chatbot1c").joinpath("builtin_skills", *relative.parts)
    if packaged.is_file():
        return packaged.read_bytes()
    canonical = Path(__file__).resolve().parents[2] / "skills" / relative
    if canonical.is_file():
        return canonical.read_bytes()
    raise FileNotFoundError("starter skill package is absent from package resources")
