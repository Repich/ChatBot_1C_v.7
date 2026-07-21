"""Explicit database-state marker strategies for paging consistency checks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from chatbot1c.application.models import PinnedCatalog
from chatbot1c.application.ports import DatabaseStateMarkerPort
from chatbot1c.contracts.digest import canonicalize
from chatbot1c.domain.evidence import DatabaseStateMarker


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExplicitDatabaseStateMarker(DatabaseStateMarkerPort):
    """Stable marker explicitly advanced by deployment/operator configuration.

    The 1C MCP contract has no data-revision API. This marker therefore proves
    equality of an explicit operator marker and observable application inputs;
    it does not claim a transactional snapshot of live 1C data.
    """

    marker_value: str
    configuration_profile_digest: str
    documentation_revision: str
    documentation_manifest_digest: str

    def capture(self, catalog: PinnedCatalog) -> DatabaseStateMarker:
        return _capture(
            marker_value=self.marker_value,
            configuration_profile_digest=self.configuration_profile_digest,
            documentation_revision=self.documentation_revision,
            documentation_manifest_digest=self.documentation_manifest_digest,
            catalog=catalog,
        )


class MutableFixtureDatabaseStateMarker(DatabaseStateMarkerPort):
    """Controllable equivalent used by deterministic adapter/E2E fixtures."""

    def __init__(
        self,
        marker_value: str,
        *,
        configuration_profile_digest: str,
        documentation_revision: str,
        documentation_manifest_digest: str,
    ) -> None:
        self.marker_value = marker_value
        self.configuration_profile_digest = configuration_profile_digest
        self.documentation_revision = documentation_revision
        self.documentation_manifest_digest = documentation_manifest_digest

    def set_marker(self, marker_value: str) -> None:
        self.marker_value = marker_value

    def capture(self, catalog: PinnedCatalog) -> DatabaseStateMarker:
        return _capture(
            marker_value=self.marker_value,
            configuration_profile_digest=self.configuration_profile_digest,
            documentation_revision=self.documentation_revision,
            documentation_manifest_digest=self.documentation_manifest_digest,
            catalog=catalog,
        )


def _capture(
    *,
    marker_value: str,
    configuration_profile_digest: str,
    documentation_revision: str,
    documentation_manifest_digest: str,
    catalog: PinnedCatalog,
) -> DatabaseStateMarker:
    explicit_marker_digest = _sha(marker_value)
    components = {
        "scope": "explicit_mvp_marker_and_application_inputs",
        "configuration_revision": "11.5.27.56",
        "configuration_profile_digest": configuration_profile_digest,
        "catalog_revision": catalog.revision,
        "catalog_snapshot_digest": catalog.digest,
        "documentation_revision": documentation_revision,
        "documentation_manifest_digest": documentation_manifest_digest,
        "explicit_database_marker_digest": explicit_marker_digest,
    }
    digest = hashlib.sha256(canonicalize(components)).hexdigest()
    return DatabaseStateMarker(
        marker_id=uuid5(NAMESPACE_URL, digest),
        algorithm="sha256",
        scope="acceptance_observable_state",
        digest=digest,
        captured_at=datetime.now(UTC),
        profile_version="1.0.0",
        acceptance_suite_version="q001-q116-v1",
        configuration_revision="11.5.27.56",
        configuration_profile_digest=configuration_profile_digest,
        catalog_revision=catalog.revision,
        catalog_snapshot_digest=catalog.digest,
        documentation_revision=documentation_revision,
        documentation_manifest_digest=documentation_manifest_digest,
        projection_manifest_digest=explicit_marker_digest,
    )
