"""Build the immutable UT 11.5.27.56 production resolver catalog (slice 3B).

This generator is intentionally independent from ``build_builtin_skills.py``.
It never removes or rewrites earlier starter artifacts: published bytes are an
input invariant and every new skill is written to a versioned filename.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from chatbot1c.contracts.digest import generate_integrity
from chatbot1c.contracts.harness import ContractHarness
from chatbot1c.domain.skill import Skill

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "skills" / "ut-11.5.27.56"
PROFILE = ROOT / "src/chatbot1c/resources/ut-11.5.27.56-profile.json"
SKILL_CATALOG = ROOT / "docs/requirements/skill_catalog.md"
CREATED = "2026-07-22T00:00:00Z"
CONFIG_ID = "УправлениеТорговлейБазовая"
CONFIG_NAME = "1С:Управление торговлей (базовая), редакция 11"
RELEASE = "11.5.27.56"
MODE = "8.3.27"

HASHES = {
    "item": "5fd976edf70c1dd0c65aeabce8362e6767876bcc9e33985d0aa47bdbbe8a86f4",
    "barcode": "fdee46a99d1b44dfe590f6e4b1c1c468d92159e437b9285fcd3daa7a58521bd3",
    "partner": "e1621de92d0e0c2fcbf6779c6060b06e57c6eb39a4333bbf7a5fe4ae55f471a2",
    "contractor": "af02379381c5c0514831b8a9282f1733716d4c714411c678b100d27e3f6e450e",
    "organization": "90c61aca0b35ac7f573685956b6c815736c5ab405483b765a4dcbfc58388c875",
    "warehouse": "f5ed42f385cbd1b4efeffa216178e20f7266e84a08bd10e3b1995874af01d768",
    "cash_enterprise": "7979ca4e782ad20e68a00725aad4a11f302ead9c84be670e3f98e610fa328f75",
    "cash_pos": "7465fda311181ebb1d3b9dcac1affdc990102aeb213f6c373c41b62e07cd45e6",
    "price_type": "4d1c064b6ba10a78748481fbac192cc5d735ed251319f38fe4665dcd5ef32c1f",
    "purpose": "2c93b290c09cbf111ecf02ac2c88350f2d94688c6fb7fcc48c2a0bf2b763b0c3",
    "characteristic": "280d30d59159b283fd47aa14098bfcbaf51668df0d8db48ad2df938442c0a04b",
    "series": "272babdfd5a8b5cdb4437be1f8289b75f2b3c3611c1b7fa057f1054d975bb548",
    "analytics": "4f88fd400e114ac936b34c710bee40efb7abe4a9f8c833b2a32619e90e52880e",
    "sales_order": "dbb8b95230a929de03c7af4534adea7550d6de72fdaca9e2defb9c38978d1a61",
    "shipment": "1ebe12622345cbce608283575d39b8835013a61d817787c4e98c6ddbbb4d105d",
    "receipt": "f839987386ff03c7bb67b69c53b7ec4309760639f622a9cc8035e801e005acf7",
    "purchase_order": "473dbe17168d7bc2b7947784affb177c8660d44551c4c8cbcbff72ebde65a460",
    "transfer": "66cc7e484af93c58b058a89ce21996a09dbe1da45a260c5f35415e87263b5039",
}

LEGACY_BYTES = {
    "ut.starter.slice-one.package.json": "4de313a696cf9bff478746b5d0fe9e779948b090ac3f277785c2f4818df01420",
    "ut.starter.slice-two.package.json": "e0f7e68d7a0bcfcc89705e6e445b8b3cef9e577199efd24ba7792acd7ed3fc5b",
    "ut115.doc.term.skill.json": "6192be111979a8f21b8864e185d6d3c97f198cc547209c243d15484d13c8d0a3",
    "ut115.ref.item.resolve-article-exact.skill.json": "55e14aa9fb4c5d75e0c598437c03fd95555109625bf5388108820c88d96f471f",
    "ut115.ref.item.resolve-barcode-exact.skill.json": "1cd1fd129c92cca31e25b4392248b73bde5b893b343117fbb0dd5a775a9b0e19",
    "ut115.ref.item.resolve-code-exact.skill.json": "97b34dbdb7596780b2c579ad9303949de0b7f4cac29d96b27a19be29096dfcf1",
    "ut115.ref.item.resolve-name-contains.skill.json": "7153bcd14625ec7f4ab401b5045be314b73b7fa47070073a3ad50ada8b5ee212",
    "ut115.ref.warehouse.resolve.skill.json": "f1535539c536374d9abcba3d1c95f9cd213523a610898863c6fa7203acf9f218",
    "ut115.sales.order-header-status-by-number.skill.json": "d25c85da8623aa4a2efec2b075c467504d9582053bdef36ddbff41dd49ac2ac5",
    "ut115.sales.order-lines.skill.json": "95b9dd941e71861245b27c45a036be8df33ef31faf38d28aedaa1a423443777d",
    "ut115.sales.shipment-list.skill.json": "c405535c7fdf828fa279f4fc0c6a00a3414451f12a15c937125adc09f9bcdafa",
    "ut115.stock.balance.skill.json": "b9a9d1160c0f59f01dc82603b5b285e6a8f362b8f68304daa17b03f5bd963996",
}

R01_IDS = {
    "ut115.ref.item.resolve-article-exact",
    "ut115.ref.item.resolve-barcode-exact",
    "ut115.ref.item.resolve-code-exact",
    "ut115.ref.item.resolve-name-contains",
}
R02_R12_IDS = {
    "ut115.ref.item.details",
    "ut115.ref.item-group.resolve-name-contains",
    "ut115.ref.item-group.resolve-code-exact",
    "ut115.ref.item.group-members",
    *{
        f"ut115.ref.{role}.resolve-{criterion}"
        for role in ("partner", "customer", "supplier")
        for criterion in ("name-contains", "code-exact", "inn-exact")
    },
    "ut115.ref.partner.details",
    "ut115.ref.customer.details",
    "ut115.ref.supplier.details",
    "ut115.ref.warehouse.resolve",
    "ut115.ref.cash-desk.enterprise.resolve",
    "ut115.ref.cash-desk.pos.resolve",
    "ut115.ref.price-type.resolve",
    "ut115.ref.organization.resolve-name-contains",
    "ut115.ref.organization.resolve-inn-exact",
    "ut115.ref.organization.resolve-kpp-exact",
    "ut115.ref.item-characteristic.resolve-name-contains",
    "ut115.ref.item-series.resolve-name-contains",
    "ut115.ref.item-series.resolve-number-exact",
    "ut115.ref.inventory-purpose.resolve-name-contains",
}
CONSUMER_IDS = {
    "ut115.ref.item.details",
    "ut115.ref.item.group-members",
    "ut115.ref.partner.details",
    "ut115.ref.customer.details",
    "ut115.ref.supplier.details",
}
DOCUMENT_PRODUCER_IDS = {
    "ut115.sales.order-header-status-by-number",
    "ut115.sales.shipment-list",
    "ut115.purchase.receipt-list",
    "ut115.purchase.order-list",
    "ut115.logistics.transfer-list",
}

# Product capability IDs are an explicit contract, not a derivative of skill_id.
# Selector-only support skills advertise the atomic common entity operation; they
# do not claim a downstream detail, list, status, or composition capability.
CAPABILITY_IDS_BY_SKILL_ID = {
    "ut115.logistics.transfer-list": (
        "CAP-COMMON-ENTITY",
        "CAP-MOVE-DIRECTION",
        "CAP-MOVE-LIST",
    ),
    "ut115.purchase.order-list": ("CAP-PURCHASE-ORDER-LIST",),
    "ut115.purchase.receipt-list": ("CAP-PURCHASE-RECEIPT-LIST",),
    "ut115.ref.cash-desk.enterprise.resolve": ("CAP-REF-CASH-DESK-FIND",),
    "ut115.ref.cash-desk.pos.resolve": ("CAP-REF-CASH-DESK-FIND",),
    "ut115.ref.customer.details": (
        "CAP-COMMON-DETAIL",
        "CAP-REF-PARTNER-DETAILS",
    ),
    "ut115.ref.customer.resolve-code-exact": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.customer.resolve-inn-exact": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.customer.resolve-name-contains": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.inventory-purpose.resolve-name-contains": ("CAP-COMMON-ENTITY",),
    "ut115.ref.item-characteristic.resolve-name-contains": (
        "CAP-COMMON-ENTITY",
    ),
    "ut115.ref.item-group.resolve-code-exact": ("CAP-COMMON-ENTITY",),
    "ut115.ref.item-group.resolve-name-contains": ("CAP-COMMON-ENTITY",),
    "ut115.ref.item-series.resolve-name-contains": ("CAP-COMMON-ENTITY",),
    "ut115.ref.item-series.resolve-number-exact": ("CAP-COMMON-ENTITY",),
    "ut115.ref.item.details": (
        "CAP-COMMON-DETAIL",
        "CAP-REF-ITEM-DETAILS",
    ),
    "ut115.ref.item.group-members": ("CAP-REF-ITEM-GROUP",),
    "ut115.ref.item.resolve-article-exact": ("CAP-REF-ITEM-FIND",),
    "ut115.ref.item.resolve-barcode-exact": ("CAP-REF-ITEM-FIND",),
    "ut115.ref.item.resolve-code-exact": ("CAP-REF-ITEM-FIND",),
    "ut115.ref.item.resolve-name-contains": ("CAP-REF-ITEM-FIND",),
    "ut115.ref.organization.resolve-inn-exact": ("CAP-COMMON-ENTITY",),
    "ut115.ref.organization.resolve-kpp-exact": ("CAP-COMMON-ENTITY",),
    "ut115.ref.organization.resolve-name-contains": ("CAP-COMMON-ENTITY",),
    "ut115.ref.partner.details": (
        "CAP-COMMON-DETAIL",
        "CAP-REF-PARTNER-DETAILS",
    ),
    "ut115.ref.partner.resolve-code-exact": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.partner.resolve-inn-exact": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.partner.resolve-name-contains": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.price-type.resolve": ("CAP-REF-PRICE-TYPE-FIND",),
    "ut115.ref.supplier.details": (
        "CAP-COMMON-DETAIL",
        "CAP-REF-PARTNER-DETAILS",
    ),
    "ut115.ref.supplier.resolve-code-exact": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.supplier.resolve-inn-exact": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.supplier.resolve-name-contains": (
        "CAP-COMMON-ENTITY",
        "CAP-REF-PARTNER-FIND",
    ),
    "ut115.ref.warehouse.resolve": ("CAP-REF-WAREHOUSE-FIND",),
    "ut115.sales.order-header-status-by-number": (
        "CAP-COMMON-ENTITY",
        "CAP-SALES-ORDER-HEADER",
        "CAP-SALES-ORDER-STATUS",
    ),
    "ut115.sales.shipment-list": ("CAP-SALES-SHIPMENT-LIST",),
}

PREVIOUS_SKILL_VERSIONS = {
    "ut115.logistics.transfer-list": "1.0.0",
    "ut115.purchase.order-list": "1.0.0",
    "ut115.purchase.receipt-list": "1.0.0",
    "ut115.ref.cash-desk.enterprise.resolve": "1.0.0",
    "ut115.ref.cash-desk.pos.resolve": "1.0.0",
    "ut115.ref.customer.details": "1.0.0",
    "ut115.ref.customer.resolve-code-exact": "1.0.0",
    "ut115.ref.customer.resolve-inn-exact": "1.0.0",
    "ut115.ref.customer.resolve-name-contains": "1.0.0",
    "ut115.ref.inventory-purpose.resolve-name-contains": "1.0.0",
    "ut115.ref.item-characteristic.resolve-name-contains": "1.0.0",
    "ut115.ref.item-group.resolve-code-exact": "1.0.0",
    "ut115.ref.item-group.resolve-name-contains": "1.0.0",
    "ut115.ref.item-series.resolve-name-contains": "1.0.0",
    "ut115.ref.item-series.resolve-number-exact": "1.0.0",
    "ut115.ref.item.details": "1.0.0",
    "ut115.ref.item.group-members": "1.0.0",
    "ut115.ref.item.resolve-article-exact": "1.2.0",
    "ut115.ref.item.resolve-barcode-exact": "1.2.0",
    "ut115.ref.item.resolve-code-exact": "1.2.0",
    "ut115.ref.item.resolve-name-contains": "1.2.0",
    "ut115.ref.organization.resolve-inn-exact": "1.0.0",
    "ut115.ref.organization.resolve-kpp-exact": "1.0.0",
    "ut115.ref.organization.resolve-name-contains": "1.0.0",
    "ut115.ref.partner.details": "1.0.0",
    "ut115.ref.partner.resolve-code-exact": "1.0.0",
    "ut115.ref.partner.resolve-inn-exact": "1.0.0",
    "ut115.ref.partner.resolve-name-contains": "1.0.0",
    "ut115.ref.price-type.resolve": "1.0.0",
    "ut115.ref.supplier.details": "1.0.0",
    "ut115.ref.supplier.resolve-code-exact": "1.0.0",
    "ut115.ref.supplier.resolve-inn-exact": "1.0.0",
    "ut115.ref.supplier.resolve-name-contains": "1.0.0",
    "ut115.ref.warehouse.resolve": "1.1.0",
    "ut115.sales.order-header-status-by-number": "1.2.0",
    "ut115.sales.shipment-list": "1.1.0",
}

REFERENCE_PACKAGE_NAME = "ut115-reference-1.1.1.package.json"
UPGRADE_PACKAGE_NAME = "ut115-reference-existing-upgrade-1.1.1.package.json"
ADDITIONS_PACKAGE_NAME = "ut115-reference-slice3-additions-1.0.1.package.json"
STARTER_PACKAGE_NAME = "ut.starter.slice-three.package.json"


def _canonical_capability_ids() -> frozenset[str]:
    ids = re.findall(
        r"^\| `(CAP-[A-Z0-9]+(?:-[A-Z0-9]+)+)` \|",
        SKILL_CATALOG.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if len(ids) != 87 or len(set(ids)) != 87:
        raise RuntimeError("Canonical skill catalog must contain exactly 87 unique IDs")
    return frozenset(ids)


def _capabilities_for(skill_id: str) -> list[str]:
    try:
        capability_ids = CAPABILITY_IDS_BY_SKILL_ID[skill_id]
    except KeyError as error:
        raise RuntimeError(f"Missing capability mapping for {skill_id}") from error
    unknown = set(capability_ids) - _canonical_capability_ids()
    if unknown:
        raise RuntimeError(f"Noncanonical capability IDs for {skill_id}: {unknown}")
    return list(capability_ids)


def _capability_migration_version(skill_id: str, previous_version: str) -> str:
    expected = PREVIOUS_SKILL_VERSIONS.get(skill_id)
    if expected != previous_version:
        raise RuntimeError(
            f"Unexpected pre-migration version for {skill_id}: "
            f"{previous_version!r} != {expected!r}"
        )
    major, minor, patch = (int(part) for part in previous_version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _combined_hash(*keys: str) -> str:
    payload = ":".join(HASHES[key] for key in keys).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _verify_legacy_bytes() -> None:
    failures = {
        name: (_sha256(TARGET / name), expected)
        for name, expected in LEGACY_BYTES.items()
        if not (TARGET / name).is_file() or _sha256(TARGET / name) != expected
    }
    if failures:
        raise RuntimeError(f"Published slice-one/two bytes changed: {failures}")


def _encode(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_artifact(path: Path, document: dict[str, Any]) -> None:
    payload = _encode(document)
    if path.is_file():
        existing = cast(
            dict[str, Any], json.loads(path.read_text(encoding="utf-8"))
        )
        identity_key = (
            "skill_id" if document["document_type"] == "skill" else "package_id"
        )
        existing_tuple = (existing.get(identity_key), existing.get("version"))
        generated_tuple = (document[identity_key], document["version"])
        if existing_tuple == generated_tuple and path.read_bytes() != payload:
            raise RuntimeError(
                f"Refusing to rewrite immutable tuple {generated_tuple!r} at {path}"
            )
    path.write_bytes(payload)


def _ref(kind: str, ordinal: int, presentation: str) -> dict[str, Any]:
    return {
        "_objectRef": True,
        "УникальныйИдентификатор": f"00000000-0000-4000-8000-{ordinal:012d}",
        "ТипОбъекта": kind,
        "Представление": presentation,
    }


def _parameter(
    name: str,
    title: str,
    value_type: str,
    sources: Sequence[str],
    normalization: str,
    *,
    required: bool = True,
    semantic_type: str | None = None,
    slots: Sequence[str] = (),
    default: Any = None,
    allowed_values: Sequence[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "title_ru": title,
        "description_ru": f"Проверенный параметр: {title.lower()}.",
        "value_type": value_type,
        "required": required,
        "allowed_sources": list(sources),
        "normalization": normalization,
        "context_slot_keys": list(slots),
    }
    if semantic_type is not None:
        result["semantic_type"] = semantic_type
        result["entity_types"] = [semantic_type]
    if default is not None:
        result["default"] = default
    if allowed_values is not None:
        result["allowed_values"] = list(allowed_values)
    return result


def _fact(
    fact_id: str,
    semantic_type: str,
    value_type: str,
    role: str,
    title: str,
    *,
    required: bool = True,
    nullable: bool = False,
    allowed_values: Sequence[str] | None = None,
    unit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "fact_id": fact_id,
        "semantic_type": semantic_type,
        "value_type": value_type,
        "role": role,
        "required": required,
        "nullable": nullable,
        "title_ru": title,
        "unit_contract": unit or {"mode": "not_applicable"},
    }
    if allowed_values is not None:
        result["allowed_values"] = list(allowed_values)
    return result


def _column(alias: str, fact_id: str, mcp_type: str, converter: str) -> dict[str, Any]:
    return {
        "column": alias,
        "fact_id": fact_id,
        "accepted_mcp_types": [mcp_type],
        "converter": converter,
    }


def _metadata(object_name: str, attributes: Sequence[str]) -> dict[str, Any]:
    return {"object_name": object_name, "attributes": list(attributes)}


def _source(path: str, sha: str) -> dict[str, Any]:
    return {
        "kind": "configuration_metadata",
        "uri": f"ut-config://{RELEASE}/{path}",
        "sha256": sha,
    }


def _pagination(prefix: str, name_fact: str, ref_fact: str) -> dict[str, Any]:
    return {
        "strategy": "keyset",
        "has_cursor_query_parameter": "ЕстьКурсор",
        "sort": [
            {"fact_id": name_fact, "direction": "asc"},
            {"fact_id": ref_fact, "direction": "asc"},
        ],
        "cursor_bindings": [
            {
                "fact_id": name_fact,
                "query_parameter": f"{prefix}Курсора",
                "encoding": "string",
            },
            {
                "fact_id": ref_fact,
                "query_parameter": "СсылкаКурсора",
                "encoding": "object_ref",
            },
        ],
    }


def _cursor_predicate(name: str, ref: str, cursor: str) -> str:
    return (
        "  И (НЕ &ЕстьКурсор\n"
        f"    ИЛИ {name} > &{cursor}\n"
        f"    ИЛИ ({name} = &{cursor}\n"
        f"      И {ref} > &СсылкаКурсора))\n"
    )


def _mcp_test(
    test_id: str,
    bindings: Sequence[dict[str, Any]],
    row: dict[str, Any],
    schema: Sequence[dict[str, Any]],
    required: Sequence[str],
) -> list[dict[str, Any]]:
    base: dict[str, Any] = {
        "kind": "mcp_execute_query",
        "response": {
            "success": True,
            "data": [row],
            "schema": {"columns": list(schema)},
            "count": 1,
        },
    }
    empty = copy.deepcopy(base)
    empty["response"]["data"] = []
    empty["response"]["count"] = 0
    malformed = {
        "kind": "mcp_execute_query",
        "response": {
            "success": False,
            "error": "MCP_SCHEMA_MISMATCH: incompatible identity column type",
        },
    }
    return [
        {
            "test_id": f"{test_id}.positive",
            "case_kind": "positive",
            "bindings": list(bindings),
            "fixture": base,
            "expected": {
                "status": "success_with_rows",
                "required_fact_ids": list(required),
            },
        },
        {
            "test_id": f"{test_id}.empty",
            "case_kind": "negative",
            "bindings": list(bindings),
            "fixture": empty,
            "expected": {"status": "success_empty", "required_fact_ids": []},
        },
        {
            "test_id": f"{test_id}.malformed",
            "case_kind": "negative",
            "bindings": list(bindings),
            "fixture": malformed,
            "expected": {
                "status": "query_error",
                "required_fact_ids": [],
                "error_code": "MCP_SCHEMA_MISMATCH",
            },
        },
    ]


def _base_skill(
    *,
    skill_id: str,
    version: str,
    name: str,
    purpose: str,
    aliases: Sequence[str],
    parameters: Sequence[dict[str, Any]],
    facts: Sequence[dict[str, Any]],
    required_facts: Sequence[str],
    query: str,
    parameter_bindings: Sequence[dict[str, Any]],
    columns: Sequence[dict[str, Any]],
    pagination: dict[str, Any],
    metadata_hash: str,
    metadata_requirements: Sequence[dict[str, Any]],
    sources: Sequence[dict[str, Any]],
    tests: Sequence[dict[str, Any]],
    row_identity: Sequence[str],
    resolution: dict[str, Any] | None,
    context_policy: Sequence[dict[str, Any]],
    dependencies: Sequence[dict[str, Any]] = (),
    result_constraints: Sequence[dict[str, Any]] = (),
    cardinality: str = "many",
    renderer: str = "table",
    invariants: Sequence[dict[str, Any]] = (),
    default_limit: int = 20,
) -> dict[str, Any]:
    version = _capability_migration_version(skill_id, version)
    fact_types = sorted({fact["semantic_type"] for fact in facts})
    document: dict[str, Any] = {
        "schema_version": "1.1.0",
        "document_type": "skill",
        "skill_id": skill_id,
        "version": version,
        "display": {
            "name_ru": name,
            "purpose_ru": purpose,
            "limitations_ru": [
                "Не выбирает первый результат без доказанного SelectionProof."
            ],
        },
        "provides": {
            "capability_ids": _capabilities_for(skill_id),
            "fact_types": fact_types,
        },
        "compatibility": {
            "configuration_id": CONFIG_ID,
            "configuration_name": CONFIG_NAME,
            "release_range": {
                "minimum": RELEASE,
                "maximum": RELEASE,
                "include_minimum": True,
                "include_maximum": True,
            },
            "compatibility_modes": [MODE],
            "required_metadata": list(metadata_requirements),
            "metadata_snapshot_sha256": metadata_hash,
        },
        "selection": {
            "intent_kinds": ["data"],
            "aliases_ru": list(aliases),
            "anti_examples_ru": ["изменить или удалить найденный объект"],
            "required_context_fact_types": sorted(
                {
                    parameter["semantic_type"]
                    for parameter in parameters
                    if parameter.get("semantic_type")
                    and "session_context" in parameter["allowed_sources"]
                }
            ),
        },
        "parameters": list(parameters),
        "operation": {
            "kind": "data_query",
            "tool": "execute_query",
            "read_only": True,
            "query_template": {
                "template_id": f"{skill_id}.v{version.split('.')[0]}",
                "language": "1c-query",
                "text": query,
                "execution": {
                    "kind": "single_select",
                    "statement_count": 1,
                    "final_statement": 1,
                },
                "invariant_constants": list(invariants),
                "include_schema": True,
                "mcp_limit": {"default": default_limit, "maximum": 1000},
            },
            "parameter_bindings": list(parameter_bindings),
            "column_bindings": list(columns),
            "pagination": pagination,
        },
        "output_contract": {
            "contract_id": f"{skill_id}.v{version.split('.')[0]}",
            "contract_version": "1.1.0",
            "cardinality": cardinality,
            "facts": list(facts),
            "sufficiency": {
                "required_fact_sets": [list(required_facts)],
                "empty_semantics": "confirmed_not_found",
                "zero_fact_ids": [],
                "truncation_policy": "page_is_complete",
            },
            "renderer": {
                "kind": renderer,
                "primary_fact_ids": [required_facts[-1]],
                "column_fact_ids": [fact["fact_id"] for fact in facts],
            },
            "row_identity_fact_ids": list(row_identity),
            "resolution": resolution,
            "context_export_policy": list(context_policy),
        },
        "result_constraints": list(result_constraints),
        "dependencies": {
            "runtime_contracts": [
                {"contract": "skill-runtime", "version_range": "^1.0.0"},
                {"contract": "mcp.execute_query", "version_range": "^1.0.0"},
            ],
            "skills": list(dependencies),
        },
        "examples": [
            {
                "question_ru": aliases[0] + "?",
                "applicability": "applicable",
                "reason_ru": "Вопрос соответствует точному typed-контракту навыка.",
            },
            {
                "question_ru": "изменить найденный объект?",
                "applicability": "not_applicable",
                "reason_ru": "Навык выполняет только чтение данных.",
            },
        ],
        "tests": list(tests),
        "provenance": {
            "author": "ChatBot 1C slice 3B",
            "created_at": CREATED,
            "reviewed_by": "Metadata-proven resolver catalog",
            "reviewed_at": CREATED,
            "source_configuration": {
                "configuration_id": CONFIG_ID,
                "release": RELEASE,
                "compatibility_mode": MODE,
                "metadata_snapshot_sha256": metadata_hash,
            },
            "source_references": list(sources),
            "change_note_ru": (
                "Production typed resolver/consumer contract без object-specific core logic."
            ),
        },
    }
    return generate_integrity(document)


def _resolver_contract(
    identity: str,
    labels: Sequence[str],
    role_proofs: Sequence[str],
    slot: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return (
        {
            "protocol": "typed_entity_resolver_v1",
            "identity_fact_id": identity,
            "candidate_label_fact_ids": list(labels),
            "role_proof_fact_ids": list(role_proofs),
            "default_slot_key": slot,
        },
        [
            {
                "fact_id": identity,
                "slot_key": slot,
                "mode": "selected_only",
                "lifetime": {"mode": "session"},
                "max_members": 100,
            }
        ],
    )


def _dependency(skill_id: str, version: str, fact_type: str) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "version_range": f"={_capability_migration_version(skill_id, version)}",
        "required_fact_types": [fact_type],
    }


def _upgrade_item_resolver(path: Path) -> dict[str, Any]:
    original = json.loads(path.read_text(encoding="utf-8"))
    document = copy.deepcopy(original)
    document.pop("integrity", None)
    document["schema_version"] = "1.1.0"
    document["version"] = _capability_migration_version(
        document["skill_id"], "1.2.0"
    )
    document["provides"]["capability_ids"] = _capabilities_for(
        document["skill_id"]
    )
    for parameter in document["parameters"]:
        parameter["context_slot_keys"] = []
    query = document["operation"]["query_template"]["text"]
    barcode_variant = "barcode-exact" in document["skill_id"]
    if barcode_variant:
        marker = "  Штрихкоды.Номенклатура.Наименование КАК Наименование"
        query = query.replace(
            marker,
            marker
            + ",\n  Штрихкоды.Штрихкод КАК СовпавшийШтрихкод"
            + ",\n  НЕ Штрихкоды.Номенклатура.ЭтоГруппа КАК ЭтоТовар",
        )
        query = query.replace(
            "ГДЕ Штрихкоды.Штрихкод = &Штрихкод",
            "ГДЕ Штрихкоды.Штрихкод = &Штрихкод\n"
            "  И НЕ Штрихкоды.Номенклатура.ЭтоГруппа",
        )
    else:
        marker = "  Номенклатура.Наименование КАК Наименование"
        query = query.replace(
            marker, marker + ",\n  НЕ Номенклатура.ЭтоГруппа КАК ЭтоТовар"
        )
    document["operation"]["query_template"]["text"] = query
    document["operation"]["query_template"]["template_id"] = (
        document["skill_id"] + ".v2"
    )
    document["operation"]["column_bindings"].append(
        _column("ЭтоТовар", "item.is_item", "Булево", "boolean")
    )
    document["output_contract"]["contract_id"] = document["skill_id"] + ".v2"
    document["output_contract"]["contract_version"] = "1.1.0"
    document["output_contract"]["facts"].append(
        _fact(
            "item.is_item",
            "catalog.item.is_item",
            "boolean",
            "attribute",
            "Это номенклатура, не группа",
        )
    )
    required = document["output_contract"]["sufficiency"]["required_fact_sets"][0]
    required.append("item.is_item")
    document["output_contract"]["renderer"]["column_fact_ids"].append("item.is_item")
    resolution, policy = _resolver_contract(
        "item.ref", ["item.name", "item.code"], ["item.is_item"], "selection.item"
    )
    if "article-exact" in document["skill_id"]:
        article = next(
            fact
            for fact in document["output_contract"]["facts"]
            if fact["fact_id"] == "item.article"
        )
        article["required"] = True
        article["nullable"] = False
        required.append("item.article")
        resolution["candidate_label_fact_ids"].append("item.article")
    if barcode_variant:
        document["operation"]["column_bindings"].append(
            _column(
                "СовпавшийШтрихкод",
                "item.matched_barcode",
                "Строка",
                "string",
            )
        )
        document["output_contract"]["facts"].append(
            _fact(
                "item.matched_barcode",
                "catalog.item.barcode",
                "string",
                "attribute",
                "Совпавший штрихкод",
            )
        )
        document["output_contract"]["renderer"]["column_fact_ids"].append(
            "item.matched_barcode"
        )
        required.append("item.matched_barcode")
        resolution["candidate_label_fact_ids"].append("item.matched_barcode")
        document["provides"]["fact_types"].append("catalog.item.barcode")
        document["compatibility"]["required_metadata"].append(
            _metadata(
                "Справочник.Номенклатура",
                ["Ссылка", "Код", "Артикул", "Наименование", "ЭтоГруппа"],
            )
        )
        document["compatibility"]["metadata_snapshot_sha256"] = _combined_hash(
            "barcode", "item"
        )
        document["provenance"]["source_configuration"][
            "metadata_snapshot_sha256"
        ] = _combined_hash("barcode", "item")
        document["provenance"]["source_references"].append(
            _source("Catalogs/Номенклатура.xml", HASHES["item"])
        )
    document["output_contract"]["resolution"] = resolution
    document["output_contract"]["context_export_policy"] = policy
    document["provides"]["fact_types"].append("catalog.item.is_item")
    for test in document["tests"]:
        response = test["fixture"]["response"]
        response.get("schema", {}).get("columns", []).append(
            {"name": "ЭтоТовар", "types": ["Булево"]}
        )
        for row in response.get("data", []):
            row["ЭтоТовар"] = True
            if barcode_variant:
                row["СовпавшийШтрихкод"] = "460000000001"
        if barcode_variant:
            response.get("schema", {}).get("columns", []).append(
                {"name": "СовпавшийШтрихкод", "types": ["Строка"]}
            )
        if test["expected"]["status"] == "success_with_rows":
            test["expected"]["required_fact_ids"].append("item.is_item")
    document["tests"].append(
        {
            "test_id": document["skill_id"].replace("ut115.", "ut.") + ".malformed",
            "case_kind": "negative",
            "bindings": [],
            "fixture": {
                "kind": "mcp_execute_query",
                "response": {
                    "success": False,
                    "error": "MCP_SCHEMA_MISMATCH: bad object reference",
                },
            },
            "expected": {
                "status": "query_error",
                "required_fact_ids": [],
                "error_code": "MCP_SCHEMA_MISMATCH",
            },
        }
    )
    document["provenance"]["author"] = "ChatBot 1C slice 3B"
    document["provenance"]["created_at"] = CREATED
    document["provenance"]["reviewed_at"] = CREATED
    document["provenance"]["change_note_ru"] = (
        "Typed item resolver 1.2 с role proof и selected-only context export."
    )
    return generate_integrity(document)


def _simple_resolver(
    *,
    skill_id: str,
    version: str,
    name: str,
    aliases: Sequence[str],
    entity_alias: str,
    entity_expression: str,
    physical_type: str,
    identity_fact: str,
    identity_semantic: str,
    label_expression: str,
    label_alias: str,
    label_fact: str,
    label_semantic: str,
    slot: str,
    from_clause: str,
    where: str,
    parameters: Sequence[dict[str, Any]],
    parameter_bindings: Sequence[dict[str, Any]],
    metadata_hash: str,
    metadata_requirements: Sequence[dict[str, Any]],
    sources: Sequence[dict[str, Any]],
    extra_projection: Sequence[tuple[str, str, dict[str, Any], str, str]],
    bindings: Sequence[dict[str, Any]],
    fixture_values: dict[str, Any],
    fixture_bindings: Sequence[dict[str, Any]],
    labels: Sequence[str] | None = None,
    role_proofs: Sequence[str] = (),
    invariants: Sequence[dict[str, Any]] = (),
    constraints: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    projection = [
        f"  {entity_expression} КАК {entity_alias}",
        f"  {label_expression} КАК {label_alias}",
    ]
    facts = [
        _fact(identity_fact, identity_semantic, "entity_ref", "entity", name),
        _fact(label_fact, label_semantic, "string", "attribute", "Наименование"),
    ]
    columns = [
        _column(entity_alias, identity_fact, physical_type, "object_ref"),
        _column(label_alias, label_fact, "Строка", "string"),
    ]
    schema = [
        {"name": entity_alias, "types": [physical_type]},
        {"name": label_alias, "types": ["Строка"]},
    ]
    row = {entity_alias: fixture_values[entity_alias], label_alias: fixture_values[label_alias]}
    for expression, alias, fact, mcp_type, converter in extra_projection:
        projection.append(f"  {expression} КАК {alias}")
        facts.append(fact)
        columns.append(_column(alias, fact["fact_id"], mcp_type, converter))
        schema.append({"name": alias, "types": [mcp_type]})
        row[alias] = fixture_values[alias]
    query = (
        "ВЫБРАТЬ\n"
        + ",\n".join(projection)
        + f"\nИЗ {from_clause}\nГДЕ {where}\n"
        + _cursor_predicate(label_expression, entity_expression, "ИмяКурсора")
        + f"УПОРЯДОЧИТЬ ПО {label_expression}, {entity_expression}"
    )
    resolution, policy = _resolver_contract(
        identity_fact, labels or [label_fact], role_proofs, slot
    )
    required = [fact["fact_id"] for fact in facts if fact["required"]]
    test_id = skill_id.replace("ut115.", "ut.")
    return _base_skill(
        skill_id=skill_id,
        version=version,
        name=name,
        purpose=f"Разрешает exact сущность {name.lower()} по проверенным metadata.",
        aliases=aliases,
        parameters=parameters,
        facts=facts,
        required_facts=required,
        query=query,
        parameter_bindings=parameter_bindings,
        columns=columns,
        pagination=_pagination("Имя", label_fact, identity_fact),
        metadata_hash=metadata_hash,
        metadata_requirements=metadata_requirements,
        sources=sources,
        tests=_mcp_test(test_id, fixture_bindings, row, schema, required),
        row_identity=[identity_fact],
        resolution=resolution,
        context_policy=policy,
        dependencies=bindings,
        result_constraints=constraints,
        invariants=invariants,
    )


def _build_item_details() -> dict[str, Any]:
    item = _ref("СправочникСсылка.Номенклатура", 1001, "Куртка")
    characteristic = _ref("СправочникСсылка.ХарактеристикиНоменклатуры", 1002, "M")
    series = _ref("СправочникСсылка.СерииНоменклатуры", 1003, "S-1")
    facts = [
        _fact("item.ref", "catalog.item", "entity_ref", "entity", "Номенклатура"),
        _fact("item.name", "catalog.item.name", "string", "attribute", "Наименование"),
        _fact("item.code", "catalog.item.code", "string", "attribute", "Код"),
        _fact("item.article", "catalog.item.article", "string", "attribute", "Артикул", required=False, nullable=True),
        _fact("item.storage_unit", "catalog.item.unit", "string", "dimension", "Единица хранения"),
        _fact("item.barcode", "catalog.item.barcode", "string", "dimension", "Штрихкод"),
        _fact("item.barcode_characteristic", "catalog.item.characteristic", "entity_ref", "dimension", "Характеристика штрихкода"),
        _fact("item.barcode_series", "catalog.item.series", "entity_ref", "dimension", "Серия штрихкода"),
    ]
    columns = [
        _column("Номенклатура", "item.ref", "СправочникСсылка.Номенклатура", "object_ref"),
        _column("Наименование", "item.name", "Строка", "string"),
        _column("Код", "item.code", "Строка", "string"),
        _column("Артикул", "item.article", "Строка", "string"),
        _column("ЕдиницаХранения", "item.storage_unit", "Строка", "string"),
        _column("Штрихкод", "item.barcode", "Строка", "string"),
        _column("Характеристика", "item.barcode_characteristic", "СправочникСсылка.ХарактеристикиНоменклатуры", "object_ref"),
        _column("Серия", "item.barcode_series", "СправочникСсылка.СерииНоменклатуры", "object_ref"),
    ]
    schema = [{"name": item["column"], "types": item["accepted_mcp_types"]} for item in columns]
    row = {
        "Номенклатура": item,
        "Наименование": "Куртка",
        "Код": "0001",
        "Артикул": "K-1",
        "ЕдиницаХранения": "шт",
        "Штрихкод": "460000000001",
        "Характеристика": characteristic,
        "Серия": series,
    }
    query = (
        "ВЫБРАТЬ\n  Номенклатура.Ссылка КАК Номенклатура,\n"
        "  Номенклатура.Наименование КАК Наименование,\n"
        "  Номенклатура.Код КАК Код,\n  Номенклатура.Артикул КАК Артикул,\n"
        "  ПРЕДСТАВЛЕНИЕ(Номенклатура.ЕдиницаИзмерения) КАК ЕдиницаХранения,\n"
        '  ЕСТЬNULL(Штрихкоды.Штрихкод, "") КАК Штрихкод,\n'
        "  ЕСТЬNULL(Штрихкоды.Характеристика, ЗНАЧЕНИЕ(Справочник.ХарактеристикиНоменклатуры.ПустаяСсылка)) КАК Характеристика,\n"
        "  ЕСТЬNULL(Штрихкоды.Серия, ЗНАЧЕНИЕ(Справочник.СерииНоменклатуры.ПустаяСсылка)) КАК Серия\n"
        "ИЗ Справочник.Номенклатура КАК Номенклатура\n"
        "  ЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.ШтрихкодыНоменклатуры КАК Штрихкоды\n"
        "  ПО Штрихкоды.Номенклатура = Номенклатура.Ссылка\n"
        "ГДЕ Номенклатура.Ссылка = &Номенклатура\n"
        "УПОРЯДОЧИТЬ ПО Штрихкоды.Штрихкод, Штрихкоды.Характеристика, Штрихкоды.Серия"
    )
    required = ["item.ref", "item.name", "item.code", "item.storage_unit", "item.barcode", "item.barcode_characteristic", "item.barcode_series"]
    return _base_skill(
        skill_id="ut115.ref.item.details",
        version="1.0.0",
        name="Карточка выбранной номенклатуры",
        purpose="Возвращает реквизиты exact выбранной номенклатуры и ее штрихкоды.",
        aliases=["покажи реквизиты выбранного товара"],
        parameters=[_parameter("item", "Номенклатура", "entity_ref", ["previous_step", "session_context"], "object_ref", semantic_type="catalog.item", slots=["selection.item"])],
        facts=facts,
        required_facts=required,
        query=query,
        parameter_bindings=[{"parameter": "item", "query_parameter": "Номенклатура", "encoding": "object_ref"}],
        columns=columns,
        pagination={"strategy": "none"},
        metadata_hash=_combined_hash("item", "barcode"),
        metadata_requirements=[
            _metadata("Справочник.Номенклатура", ["Ссылка", "Код", "Артикул", "Наименование", "ЕдиницаИзмерения"]),
            _metadata("РегистрСведений.ШтрихкодыНоменклатуры", ["Штрихкод", "Номенклатура", "Характеристика", "Серия"]),
        ],
        sources=[_source("Catalogs/Номенклатура.xml", HASHES["item"]), _source("InformationRegisters/ШтрихкодыНоменклатуры.xml", HASHES["barcode"])],
        tests=_mcp_test("ut.item.details", [{"parameter": "item", "value": item}], row, schema, required),
        row_identity=["item.ref", "item.barcode", "item.barcode_characteristic", "item.barcode_series"],
        resolution=None,
        context_policy=[],
        dependencies=[
            _dependency(skill_id, "1.2.0", "catalog.item")
            for skill_id in sorted(R01_IDS)
        ],
        result_constraints=[{"kind": "fact_equals_parameter", "fact_id": "item.ref", "parameter": "item"}],
        invariants=[{"kind": "empty_literal", "statement": 1, "value": "", "role": "null_substitution", "occurrences": 1}, {"kind": "metadata_constant", "statement": 1, "constant_kind": "empty_reference", "symbol": "Справочник.ХарактеристикиНоменклатуры.ПустаяСсылка", "role": "computed_value", "occurrences": 1}, {"kind": "metadata_constant", "statement": 1, "constant_kind": "empty_reference", "symbol": "Справочник.СерииНоменклатуры.ПустаяСсылка", "role": "computed_value", "occurrences": 1}],
    )


def _build_item_group_resolvers() -> list[dict[str, Any]]:
    result = []
    group = _ref("СправочникСсылка.Номенклатура", 1101, "Верхняя одежда")
    for criterion, parameter_name, query_parameter, predicate, normalization in (
        ("name-contains", "name_fragment", "Шаблон", 'Группы.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"', "like_contains"),
        ("code-exact", "catalog_code", "Код", "Группы.Код = &Код", "trim"),
    ):
        result.append(
            _simple_resolver(
                skill_id=f"ut115.ref.item-group.resolve-{criterion}",
                version="1.0.0",
                name="Поиск группы номенклатуры",
                aliases=["найди группу номенклатуры"],
                entity_alias="Группа",
                entity_expression="Группы.Ссылка",
                physical_type="СправочникСсылка.Номенклатура",
                identity_fact="group.ref",
                identity_semantic="catalog.item.group",
                label_expression="Группы.Наименование",
                label_alias="Наименование",
                label_fact="group.name",
                label_semantic="catalog.item.group.name",
                slot="selection.item_group",
                from_clause="Справочник.Номенклатура КАК Группы",
                where=f"Группы.ЭтоГруппа\n  И {predicate}",
                parameters=[_parameter(parameter_name, "Критерий группы", "normalized_text" if criterion == "name-contains" else "string", ["user_slot"], normalization)],
                parameter_bindings=[{"parameter": parameter_name, "query_parameter": query_parameter, "encoding": "like_contains" if criterion == "name-contains" else "string"}],
                metadata_hash=HASHES["item"],
                metadata_requirements=[_metadata("Справочник.Номенклатура", ["Ссылка", "Код", "Наименование", "ЭтоГруппа"])],
                sources=[_source("Catalogs/Номенклатура.xml", HASHES["item"])],
                extra_projection=[
                    ("Группы.Код", "Код", _fact("group.code", "catalog.item.group.code", "string", "attribute", "Код"), "Строка", "string"),
                    ("Группы.ЭтоГруппа", "ЭтоГруппа", _fact("group.is_group", "catalog.item.is_group", "boolean", "attribute", "Это группа"), "Булево", "boolean"),
                ],
                bindings=[],
                fixture_values={"Группа": group, "Наименование": "Верхняя одежда", "Код": "G-1", "ЭтоГруппа": True},
                fixture_bindings=[{"parameter": parameter_name, "value": "Верх" if criterion == "name-contains" else "G-1"}],
                labels=["group.name", "group.code"],
                role_proofs=["group.is_group"],
            )
        )
    return result


def _build_group_members() -> dict[str, Any]:
    group = _ref("СправочникСсылка.Номенклатура", 1101, "Верхняя одежда")
    item = _ref("СправочникСсылка.Номенклатура", 1102, "Куртка")
    parent = _ref("СправочникСсылка.Номенклатура", 1103, "Куртки")
    facts = [
        _fact("item.ref", "catalog.item", "entity_ref", "entity", "Номенклатура"),
        _fact("item.name", "catalog.item.name", "string", "attribute", "Наименование"),
        _fact("item.code", "catalog.item.code", "string", "attribute", "Код"),
        _fact("item.parent_group_ref", "catalog.item.group", "entity_ref", "dimension", "Родительская группа"),
        _fact("selected_group.ref", "catalog.item.group", "entity_ref", "dimension", "Выбранная группа"),
    ]
    columns = [
        _column("Номенклатура", "item.ref", "СправочникСсылка.Номенклатура", "object_ref"),
        _column("Наименование", "item.name", "Строка", "string"),
        _column("Код", "item.code", "Строка", "string"),
        _column("Родитель", "item.parent_group_ref", "СправочникСсылка.Номенклатура", "object_ref"),
        _column("ВыбраннаяГруппа", "selected_group.ref", "СправочникСсылка.Номенклатура", "object_ref"),
    ]
    row = {"Номенклатура": item, "Наименование": "Куртка", "Код": "I-1", "Родитель": parent, "ВыбраннаяГруппа": group}
    schema = [{"name": col["column"], "types": col["accepted_mcp_types"]} for col in columns]
    query = (
        "ВЫБРАТЬ\n  Номенклатура.Ссылка КАК Номенклатура,\n"
        "  Номенклатура.Наименование КАК Наименование,\n"
        "  Номенклатура.Код КАК Код,\n  Номенклатура.Родитель КАК Родитель,\n"
        "  &Группа КАК ВыбраннаяГруппа\n"
        "ИЗ Справочник.Номенклатура КАК Номенклатура\n"
        "ГДЕ НЕ Номенклатура.ЭтоГруппа\n"
        "  И ((&ВключаяПодгруппы И Номенклатура.Родитель В ИЕРАРХИИ (&Группа))\n"
        "    ИЛИ (НЕ &ВключаяПодгруппы И Номенклатура.Родитель = &Группа))\n"
        + _cursor_predicate("Номенклатура.Наименование", "Номенклатура.Ссылка", "ИмяКурсора")
        + "УПОРЯДОЧИТЬ ПО Номенклатура.Наименование, Номенклатура.Ссылка"
    )
    required = [fact["fact_id"] for fact in facts]
    return _base_skill(
        skill_id="ut115.ref.item.group-members", version="1.0.0", name="Состав выбранной группы", purpose="Возвращает товары exact выбранной группы с явно заданным правилом потомков.", aliases=["покажи товары выбранной группы"],
        parameters=[
            _parameter("group", "Группа", "entity_ref", ["previous_step", "session_context"], "object_ref", semantic_type="catalog.item.group", slots=["selection.item_group"]),
            _parameter("include_descendants", "Включать подгруппы", "boolean", ["user_slot"], "none", required=False, default=True),
        ],
        facts=facts, required_facts=required, query=query,
        parameter_bindings=[{"parameter": "group", "query_parameter": "Группа", "encoding": "object_ref"}, {"parameter": "include_descendants", "query_parameter": "ВключаяПодгруппы", "encoding": "boolean"}],
        columns=columns, pagination=_pagination("Имя", "item.name", "item.ref"), metadata_hash=HASHES["item"], metadata_requirements=[_metadata("Справочник.Номенклатура", ["Ссылка", "Код", "Наименование", "ЭтоГруппа", "Родитель"])], sources=[_source("Catalogs/Номенклатура.xml", HASHES["item"])],
        tests=_mcp_test("ut.item.group-members", [{"parameter": "group", "value": group}, {"parameter": "include_descendants", "value": True}], row, schema, required), row_identity=["item.ref"], resolution=None, context_policy=[], dependencies=[_dependency("ut115.ref.item-group.resolve-name-contains", "1.0.0", "catalog.item.group")], result_constraints=[{"kind": "fact_equals_parameter", "fact_id": "selected_group.ref", "parameter": "group"}],
    )


def _party_facts(role: str, criterion: str) -> tuple[list[dict[str, Any]], str, str]:
    identity = f"{role}.ref"
    semantic = f"party.{role}"
    contractor_inn_required = criterion == "inn-exact"
    facts = [
        _fact(identity, semantic, "entity_ref", "entity", role.capitalize()),
        _fact("partner.name", "party.partner.name", "string", "attribute", "Наименование"),
        _fact("partner.code", "party.partner.code", "string", "attribute", "Код"),
        _fact("partner.is_customer", "party.role.customer", "boolean", "attribute", "Клиент"),
        _fact("partner.is_supplier", "party.role.supplier", "boolean", "attribute", "Поставщик"),
        _fact("contractor.inn", "party.contractor.inn", "string", "attribute", "ИНН", required=contractor_inn_required, nullable=not contractor_inn_required),
    ]
    return facts, identity, semantic


def _build_party_resolvers() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    role_settings = {
        "partner": ("selection.partner", [], "Партнер"),
        "customer": ("selection.customer", ["partner.is_customer"], "Клиент"),
        "supplier": ("selection.supplier", ["partner.is_supplier"], "Поставщик"),
    }
    for role, (slot, role_proofs, role_title) in role_settings.items():
        for criterion in ("name-contains", "code-exact", "inn-exact"):
            facts, identity, semantic = _party_facts(role, criterion)
            partner = _ref("СправочникСсылка.Партнеры", 1200 + len(result), role_title)
            parameter_name = {"name-contains": "name_fragment", "code-exact": "partner_code", "inn-exact": "inn"}[criterion]
            query_parameter = {"name-contains": "Шаблон", "code-exact": "Код", "inn-exact": "ИНН"}[criterion]
            value_type = "normalized_text" if criterion == "name-contains" else "string"
            normalization = "like_contains" if criterion == "name-contains" else "trim"
            predicate = {
                "name-contains": 'Партнеры.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"',
                "code-exact": "Партнеры.Код = &Код",
                "inn-exact": "Партнеры.Ссылка В (ВЫБРАТЬ Контрагенты.Партнер ИЗ Справочник.Контрагенты КАК Контрагенты ГДЕ Контрагенты.ИНН = &ИНН)",
            }[criterion]
            role_filter = {"partner": "ИСТИНА", "customer": "Партнеры.Клиент", "supplier": "Партнеры.Поставщик"}[role]
            projection = [
                f"  Партнеры.Ссылка КАК {role_title}",
                "  Партнеры.Наименование КАК Наименование",
                "  Партнеры.Код КАК Код",
                "  Партнеры.Клиент КАК ЭтоКлиент",
                "  Партнеры.Поставщик КАК ЭтоПоставщик",
                ("  &ИНН КАК ИНН" if criterion == "inn-exact" else '  "" КАК ИНН'),
            ]
            query = (
                "ВЫБРАТЬ\n" + ",\n".join(projection)
                + "\nИЗ Справочник.Партнеры КАК Партнеры\n"
                + f"ГДЕ {role_filter}\n  И {predicate}\n"
                + _cursor_predicate("Партнеры.Наименование", "Партнеры.Ссылка", "ИмяКурсора")
                + "УПОРЯДОЧИТЬ ПО Партнеры.Наименование, Партнеры.Ссылка"
            )
            columns = [
                _column(role_title, identity, "СправочникСсылка.Партнеры", "object_ref"),
                _column("Наименование", "partner.name", "Строка", "string"),
                _column("Код", "partner.code", "Строка", "string"),
                _column("ЭтоКлиент", "partner.is_customer", "Булево", "boolean"),
                _column("ЭтоПоставщик", "partner.is_supplier", "Булево", "boolean"),
                _column("ИНН", "contractor.inn", "Строка", "string"),
            ]
            row = {role_title: partner, "Наименование": role_title, "Код": f"P-{len(result)}", "ЭтоКлиент": role != "supplier", "ЭтоПоставщик": role != "customer", "ИНН": "7701000000" if criterion == "inn-exact" else ""}
            schema = [{"name": col["column"], "types": col["accepted_mcp_types"]} for col in columns]
            required = [fact["fact_id"] for fact in facts if fact["required"]]
            resolution, policy = _resolver_contract(identity, ["partner.name", "partner.code"] + (["contractor.inn"] if criterion == "inn-exact" else []), role_proofs, slot)
            metadata_keys = ("partner", "contractor") if criterion == "inn-exact" else ("partner",)
            requirements = [_metadata("Справочник.Партнеры", ["Ссылка", "Код", "Наименование", "Клиент", "Поставщик", "НаименованиеПолное"])]
            sources = [_source("Catalogs/Партнеры.xml", HASHES["partner"])]
            if criterion == "inn-exact":
                requirements.append(_metadata("Справочник.Контрагенты", ["Партнер", "ИНН"]))
                sources.append(_source("Catalogs/Контрагенты.xml", HASHES["contractor"]))
            result.append(_base_skill(
                skill_id=f"ut115.ref.{role}.resolve-{criterion}", version="1.0.0", name=f"Поиск: {role_title.lower()}", purpose=f"Разрешает semantic role party.{role} без core-retagging.", aliases=[f"найди {role_title.lower()}"],
                parameters=[_parameter(parameter_name, "Критерий партнера", value_type, ["user_slot"], normalization)], facts=facts, required_facts=required, query=query,
                parameter_bindings=[{"parameter": parameter_name, "query_parameter": query_parameter, "encoding": "like_contains" if criterion == "name-contains" else "string"}], columns=columns,
                pagination=_pagination("Имя", "partner.name", identity), metadata_hash=_combined_hash(*metadata_keys) if len(metadata_keys) > 1 else HASHES[metadata_keys[0]], metadata_requirements=requirements, sources=sources,
                tests=_mcp_test(f"ut.{role}.{criterion}", [{"parameter": parameter_name, "value": "Парт" if criterion == "name-contains" else ("7701000000" if criterion == "inn-exact" else "P-1")}], row, schema, required), row_identity=[identity], resolution=resolution, context_policy=policy,
                invariants=([{"kind": "boolean_literal", "statement": 1, "value": True, "role": "state_filter", "occurrences": 1}] if role == "partner" else []) + ([{"kind": "empty_literal", "statement": 1, "value": "", "role": "computed_value", "occurrences": 1}] if criterion != "inn-exact" else []),
            ))
    return result


def _build_party_details(role: str) -> dict[str, Any]:
    role_title = {"partner": "Партнер", "customer": "Клиент", "supplier": "Поставщик"}[role]
    identity = f"{role}.ref"
    semantic = f"party.{role}"
    slot = f"selection.{role}"
    entity = _ref("СправочникСсылка.Партнеры", 1300, role_title)
    contractor = _ref("СправочникСсылка.Контрагенты", 1301, f"ООО {role_title}")
    facts = [
        _fact(identity, semantic, "entity_ref", "entity", role_title),
        _fact("partner.name", "party.partner.name", "string", "attribute", "Наименование"),
        _fact("partner.code", "party.partner.code", "string", "attribute", "Код"),
        _fact("contractor.ref", "party.contractor", "entity_ref", "dimension", "Контрагент"),
        _fact("contractor.name", "party.contractor.name", "string", "attribute", "Контрагент", required=False, nullable=True),
        _fact("contractor.inn", "party.contractor.inn", "string", "attribute", "ИНН", required=False, nullable=True),
        _fact("contractor.kpp", "party.contractor.kpp", "string", "attribute", "КПП", required=False, nullable=True),
        _fact("contact.kind", "party.contact.kind", "string", "dimension", "Вид контакта"),
        _fact("contact.presentation", "party.contact.presentation", "string", "dimension", "Контакт"),
    ]
    columns = [
        _column(role_title, identity, "СправочникСсылка.Партнеры", "object_ref"),
        _column("Наименование", "partner.name", "Строка", "string"),
        _column("Код", "partner.code", "Строка", "string"),
        _column("Контрагент", "contractor.ref", "СправочникСсылка.Контрагенты", "object_ref"),
        _column("КонтрагентНаименование", "contractor.name", "Строка", "string"),
        _column("ИНН", "contractor.inn", "Строка", "string"),
        _column("КПП", "contractor.kpp", "Строка", "string"),
        _column("ВидКонтакта", "contact.kind", "Строка", "string"),
        _column("Контакт", "contact.presentation", "Строка", "string"),
    ]
    row = {role_title: entity, "Наименование": role_title, "Код": "P-1", "Контрагент": contractor, "КонтрагентНаименование": f"ООО {role_title}", "ИНН": "7701000000", "КПП": "770101001", "ВидКонтакта": "Юридический адрес", "Контакт": "Москва"}
    schema = [{"name": col["column"], "types": col["accepted_mcp_types"]} for col in columns]
    query = (
        f"ВЫБРАТЬ\n  Партнеры.Ссылка КАК {role_title},\n  Партнеры.Наименование КАК Наименование,\n"
        "  Партнеры.Код КАК Код,\n  ЕСТЬNULL(Контрагенты.Ссылка, ЗНАЧЕНИЕ(Справочник.Контрагенты.ПустаяСсылка)) КАК Контрагент,\n"
        "  Контрагенты.Наименование КАК КонтрагентНаименование,\n"
        "  Контрагенты.ИНН КАК ИНН,\n  Контрагенты.КПП КАК КПП,\n"
        '  ЕСТЬNULL(ПРЕДСТАВЛЕНИЕ(Контакты.Вид), "") КАК ВидКонтакта,\n'
        '  ЕСТЬNULL(Контакты.Представление, "") КАК Контакт\n'
        "ИЗ Справочник.Партнеры КАК Партнеры\n"
        "  ЛЕВОЕ СОЕДИНЕНИЕ Справочник.Контрагенты КАК Контрагенты\n"
        "  ПО Контрагенты.Партнер = Партнеры.Ссылка\n"
        "  ЛЕВОЕ СОЕДИНЕНИЕ Справочник.Партнеры.КонтактнаяИнформация КАК Контакты\n"
        "  ПО Контакты.Ссылка = Партнеры.Ссылка\n"
        f"ГДЕ Партнеры.Ссылка = &{role_title}"
    )
    required = [identity, "partner.name", "partner.code"]
    return _base_skill(
        skill_id=f"ut115.ref.{role}.details", version="1.0.0", name=f"Реквизиты: {role_title.lower()}", purpose="Возвращает exact реквизиты выбранной semantic role без subtype inference.", aliases=[f"покажи реквизиты этого {role_title.lower()}а"],
        parameters=[_parameter(role, role_title, "entity_ref", ["previous_step", "session_context"], "object_ref", semantic_type=semantic, slots=[slot])], facts=facts, required_facts=required, query=query,
        parameter_bindings=[{"parameter": role, "query_parameter": role_title, "encoding": "object_ref"}], columns=columns, pagination={"strategy": "none"}, metadata_hash=_combined_hash("partner", "contractor"),
        metadata_requirements=[_metadata("Справочник.Партнеры", ["Ссылка", "Код", "Наименование", "КонтактнаяИнформация.Вид", "КонтактнаяИнформация.Представление"]), _metadata("Справочник.Контрагенты", ["Ссылка", "Наименование", "Партнер", "ИНН", "КПП"])],
        sources=[_source("Catalogs/Партнеры.xml", HASHES["partner"]), _source("Catalogs/Контрагенты.xml", HASHES["contractor"])], tests=_mcp_test(f"ut.{role}.details", [{"parameter": role, "value": entity}], row, schema, required), row_identity=[identity, "contractor.ref", "contact.kind", "contact.presentation"], resolution=None, context_policy=[],
        dependencies=[_dependency(f"ut115.ref.{role}.resolve-name-contains", "1.0.0", semantic)], result_constraints=[{"kind": "fact_equals_parameter", "fact_id": identity, "parameter": role}],
        invariants=[{"kind": "metadata_constant", "statement": 1, "constant_kind": "empty_reference", "symbol": "Справочник.Контрагенты.ПустаяСсылка", "role": "computed_value", "occurrences": 1}, {"kind": "empty_literal", "statement": 1, "value": "", "role": "null_substitution", "occurrences": 2}],
    )


def _build_warehouse() -> dict[str, Any]:
    warehouse = _ref("СправочникСсылка.Склады", 1401, "Розничный склад")
    department = _ref("СправочникСсылка.СтруктураПредприятия", 1402, "Розница")
    params = [
        _parameter("name_fragment", "Наименование склада", "normalized_text", ["user_slot", "system"], "like_contains", required=False, default=""),
        _parameter("retail_only", "Только розничные", "boolean", ["user_slot", "system"], "none", required=False, default=False),
        _parameter("department", "Подразделение", "entity_ref", ["previous_step"], "object_ref", required=False, semantic_type="catalog.department"),
    ]
    return _simple_resolver(
        skill_id="ut115.ref.warehouse.resolve", version="1.1.0", name="Поиск склада", aliases=["найди склад"], entity_alias="Склад", entity_expression="Склады.Ссылка", physical_type="СправочникСсылка.Склады", identity_fact="warehouse.ref", identity_semantic="catalog.warehouse", label_expression="Склады.Наименование", label_alias="Наименование", label_fact="warehouse.name", label_semantic="catalog.warehouse.name", slot="selection.warehouse", from_clause="Справочник.Склады КАК Склады",
        where='Склады.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"\n  И (НЕ &ТолькоРозничные ИЛИ Склады.ТипСклада = ЗНАЧЕНИЕ(Перечисление.ТипыСкладов.РозничныйМагазин))\n  И (&Подразделение ЕСТЬ NULL ИЛИ Склады.Подразделение = &Подразделение)',
        parameters=params, parameter_bindings=[{"parameter": "name_fragment", "query_parameter": "Шаблон", "encoding": "like_contains"}, {"parameter": "retail_only", "query_parameter": "ТолькоРозничные", "encoding": "boolean"}, {"parameter": "department", "query_parameter": "Подразделение", "encoding": "object_ref"}], metadata_hash=HASHES["warehouse"], metadata_requirements=[_metadata("Справочник.Склады", ["Ссылка", "Наименование", "ТипСклада", "Подразделение"])], sources=[_source("Catalogs/Склады.xml", HASHES["warehouse"])],
        extra_projection=[
            ("ПРЕДСТАВЛЕНИЕ(Склады.ТипСклада)", "ТипСклада", _fact("warehouse.type", "catalog.warehouse.type", "string", "attribute", "Тип склада"), "Строка", "string"),
            ("Склады.ТипСклада = ЗНАЧЕНИЕ(Перечисление.ТипыСкладов.РозничныйМагазин)", "ЭтоРозничный", _fact("warehouse.is_retail", "catalog.warehouse.is_retail", "boolean", "attribute", "Розничный склад"), "Булево", "boolean"),
            ("Склады.Подразделение", "Подразделение", _fact("warehouse.department", "catalog.department", "entity_ref", "dimension", "Подразделение", required=False, nullable=True), "СправочникСсылка.СтруктураПредприятия", "object_ref"),
            ("ПРЕДСТАВЛЕНИЕ(Склады.Подразделение)", "ПодразделениеНаименование", _fact("warehouse.department_name", "catalog.department.name", "string", "dimension", "Подразделение", required=False, nullable=True), "Строка", "string"),
        ], bindings=[], fixture_values={"Склад": warehouse, "Наименование": "Розничный склад", "ТипСклада": "Розничный магазин", "ЭтоРозничный": True, "Подразделение": department, "ПодразделениеНаименование": "Розница"}, fixture_bindings=[{"parameter": "name_fragment", "value": ""}, {"parameter": "retail_only", "value": True}, {"parameter": "department", "value": department}], labels=["warehouse.name", "warehouse.type"], role_proofs=[],
        invariants=[{"kind": "metadata_constant", "statement": 1, "constant_kind": "enum_member", "symbol": "Перечисление.ТипыСкладов.РозничныйМагазин", "role": "state_filter", "occurrences": 2}, {"kind": "null_literal", "statement": 1, "value": "NULL", "role": "absence_filter", "occurrences": 1}],
    )


def _build_organizations() -> list[dict[str, Any]]:
    result = []
    organization = _ref("СправочникСсылка.Организации", 1501, "Торговый дом")
    for criterion, parameter_name, query_parameter, predicate, normalization in (
        ("name-contains", "name_fragment", "Шаблон", 'Организации.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"', "like_contains"),
        ("inn-exact", "inn", "ИНН", "Организации.ИНН = &ИНН", "trim"),
        ("kpp-exact", "kpp", "КПП", "Организации.КПП = &КПП", "trim"),
    ):
        exact_fact = {"inn-exact": "organization.inn", "kpp-exact": "organization.kpp"}.get(criterion)
        labels = ["organization.name"] + ([exact_fact] if exact_fact else [])
        result.append(_simple_resolver(
            skill_id=f"ut115.ref.organization.resolve-{criterion}", version="1.0.0", name="Поиск собственной организации", aliases=["найди нашу организацию"], entity_alias="Организация", entity_expression="Организации.Ссылка", physical_type="СправочникСсылка.Организации", identity_fact="organization.ref", identity_semantic="party.organization", label_expression="Организации.Наименование", label_alias="Наименование", label_fact="organization.name", label_semantic="party.organization.name", slot="selection.organization", from_clause="Справочник.Организации КАК Организации", where=predicate,
            parameters=[_parameter(parameter_name, "Критерий организации", "normalized_text" if criterion == "name-contains" else "string", ["user_slot"], normalization)], parameter_bindings=[{"parameter": parameter_name, "query_parameter": query_parameter, "encoding": "like_contains" if criterion == "name-contains" else "string"}], metadata_hash=HASHES["organization"], metadata_requirements=[_metadata("Справочник.Организации", ["Ссылка", "Наименование", "НаименованиеПолное", "ИНН", "КПП"])], sources=[_source("Catalogs/Организации.xml", HASHES["organization"])],
            extra_projection=[
                ("Организации.НаименованиеПолное", "НаименованиеПолное", _fact("organization.full_name", "party.organization.full_name", "string", "attribute", "Полное наименование", required=False, nullable=True), "Строка", "string"),
                ("Организации.ИНН", "ИНН", _fact("organization.inn", "party.organization.inn", "string", "attribute", "ИНН", required=criterion == "inn-exact", nullable=criterion != "inn-exact"), "Строка", "string"),
                ("Организации.КПП", "КПП", _fact("organization.kpp", "party.organization.kpp", "string", "attribute", "КПП", required=criterion == "kpp-exact", nullable=criterion != "kpp-exact"), "Строка", "string"),
                ("ИСТИНА", "Собственная", _fact("organization.is_own", "party.organization.is_own", "boolean", "attribute", "Собственная организация"), "Булево", "boolean"),
            ], bindings=[], fixture_values={"Организация": organization, "Наименование": "Торговый дом", "НаименованиеПолное": "ООО Торговый дом", "ИНН": "7701000000", "КПП": "770101001", "Собственная": True}, fixture_bindings=[{"parameter": parameter_name, "value": "Торг" if criterion == "name-contains" else ("7701000000" if criterion == "inn-exact" else "770101001")}], labels=labels, role_proofs=["organization.is_own"], invariants=[{"kind": "boolean_literal", "statement": 1, "value": True, "role": "state_filter", "occurrences": 1}],
        ))
    return result


def _build_cash_desk(kind: str) -> dict[str, Any]:
    is_pos = kind == "pos"
    source_name = "КассыККМ" if is_pos else "Кассы"
    source_key = "cash_pos" if is_pos else "cash_enterprise"
    semantic = f"finance.cash_desk.{kind}"
    slot = f"selection.cash_desk.{kind}"
    physical = f"СправочникСсылка.{source_name}"
    cash = _ref(physical, 1601 if is_pos else 1602, "Касса")
    organization = _ref("СправочникСсылка.Организации", 1603, "Торговый дом")
    currency = _ref("СправочникСсылка.Валюты", 1604, "RUB")
    extra = [
        ("&ВидКассы", "ВидКассы", _fact("cash_desk.kind", "finance.cash_desk.kind", "enum", "attribute", "Вид кассы", allowed_values=[kind]), "Строка", "string"),
        ("Кассы.Владелец", "Организация", _fact("cash_desk.organization", "party.organization", "entity_ref", "dimension", "Организация"), "СправочникСсылка.Организации", "object_ref"),
        ("ПРЕДСТАВЛЕНИЕ(Кассы.Владелец)", "ОрганизацияНаименование", _fact("cash_desk.organization_name", "party.organization.name", "string", "dimension", "Организация"), "Строка", "string"),
        ("Кассы.ВалютаДенежныхСредств", "Валюта", _fact("cash_desk.currency", "currency.ref", "entity_ref", "dimension", "Валюта"), "СправочникСсылка.Валюты", "object_ref"),
        ("Кассы.ВалютаДенежныхСредств.Код", "КодВалюты", _fact("cash_desk.currency_code", "currency.code", "string", "dimension", "Код валюты"), "Строка", "string"),
    ]
    fixture = {"Касса": cash, "Наименование": "Касса", "ВидКассы": kind, "Организация": organization, "ОрганизацияНаименование": "Торговый дом", "Валюта": currency, "КодВалюты": "RUB"}
    attrs = ["Ссылка", "Наименование", "Владелец", "ВалютаДенежныхСредств"]
    if is_pos:
        warehouse = _ref("СправочникСсылка.Склады", 1605, "Торговый зал")
        extra.extend([
            ("ПРЕДСТАВЛЕНИЕ(Кассы.ТипКассы)", "ТипККМ", _fact("cash_desk.pos_type", "finance.cash_desk.pos_type", "string", "attribute", "Тип ККМ", required=False, nullable=True), "Строка", "string"),
            ("Кассы.Склад", "Склад", _fact("cash_desk.warehouse", "catalog.warehouse", "entity_ref", "dimension", "Склад", required=False, nullable=True), "СправочникСсылка.Склады", "object_ref"),
        ])
        fixture.update({"ТипККМ": "Автономная", "Склад": warehouse})
        attrs.extend(["ТипКассы", "Склад"])
    return _simple_resolver(
        skill_id=f"ut115.ref.cash-desk.{kind}.resolve", version="1.0.0", name=f"Поиск кассы ({kind})", aliases=["найди кассу организации"], entity_alias="Касса", entity_expression="Кассы.Ссылка", physical_type=physical, identity_fact="cash_desk.ref", identity_semantic=semantic, label_expression="Кассы.Наименование", label_alias="Наименование", label_fact="cash_desk.name", label_semantic="finance.cash_desk.name", slot=slot, from_clause=f"Справочник.{source_name} КАК Кассы", where='Кассы.Владелец = &Организация\n  И Кассы.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"',
        parameters=[
            _parameter("organization", "Организация", "entity_ref", ["previous_step", "session_context"], "object_ref", semantic_type="party.organization", slots=["selection.organization"]),
            _parameter("name_fragment", "Наименование кассы", "normalized_text", ["user_slot", "system"], "like_contains", required=False, default=""),
            _parameter("cash_kind", "Вид кассы", "enum", ["system"], "none", required=False, default=kind, allowed_values=[kind]),
        ], parameter_bindings=[{"parameter": "organization", "query_parameter": "Организация", "encoding": "object_ref"}, {"parameter": "name_fragment", "query_parameter": "Шаблон", "encoding": "like_contains"}, {"parameter": "cash_kind", "query_parameter": "ВидКассы", "encoding": "string"}], metadata_hash=_combined_hash(source_key, "organization"), metadata_requirements=[_metadata(f"Справочник.{source_name}", attrs), _metadata("Справочник.Организации", ["Ссылка", "Наименование"])], sources=[_source(f"Catalogs/{source_name}.xml", HASHES[source_key]), _source("Catalogs/Организации.xml", HASHES["organization"])], extra_projection=extra, bindings=[_dependency("ut115.ref.organization.resolve-name-contains", "1.0.0", "party.organization")], fixture_values=fixture, fixture_bindings=[{"parameter": "organization", "value": organization}, {"parameter": "name_fragment", "value": ""}, {"parameter": "cash_kind", "value": kind}], labels=["cash_desk.kind", "cash_desk.name", "cash_desk.organization_name", "cash_desk.currency_code"], role_proofs=["cash_desk.kind"], constraints=[{"kind": "fact_equals_parameter", "fact_id": "cash_desk.organization", "parameter": "organization"}],
    )


def _build_price_type() -> dict[str, Any]:
    price_type = _ref("СправочникСсылка.ВидыЦен", 1701, "Розничная")
    currency = _ref("СправочникСсылка.Валюты", 1702, "RUB")
    return _simple_resolver(
        skill_id="ut115.ref.price-type.resolve", version="1.0.0", name="Поиск вида цены", aliases=["найди вид цены"], entity_alias="ВидЦены", entity_expression="ВидыЦен.Ссылка", physical_type="СправочникСсылка.ВидыЦен", identity_fact="price_type.ref", identity_semantic="catalog.price_type", label_expression="ВидыЦен.Наименование", label_alias="Наименование", label_fact="price_type.name", label_semantic="catalog.price_type.name", slot="selection.price_type", from_clause="Справочник.ВидыЦен КАК ВидыЦен",
        where='ВидыЦен.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"\n  И (НЕ &ТолькоРозничные ИЛИ ВидыЦен.ИспользоватьПриРозничнойПродаже)\n  И (НЕ &ТолькоОптовые ИЛИ ВидыЦен.ИспользоватьПриОптовойПродаже)',
        parameters=[_parameter("name_fragment", "Наименование вида цены", "normalized_text", ["user_slot", "system"], "like_contains", required=False, default=""), _parameter("retail_use_only", "Для розницы", "boolean", ["user_slot", "system"], "none", required=False, default=False), _parameter("wholesale_use_only", "Для опта", "boolean", ["user_slot", "system"], "none", required=False, default=False)],
        parameter_bindings=[{"parameter": "name_fragment", "query_parameter": "Шаблон", "encoding": "like_contains"}, {"parameter": "retail_use_only", "query_parameter": "ТолькоРозничные", "encoding": "boolean"}, {"parameter": "wholesale_use_only", "query_parameter": "ТолькоОптовые", "encoding": "boolean"}], metadata_hash=HASHES["price_type"], metadata_requirements=[_metadata("Справочник.ВидыЦен", ["Ссылка", "Наименование", "Назначение", "ВалютаЦены", "ЦенаВключаетНДС", "ИспользоватьПриРозничнойПродаже", "ИспользоватьПриОптовойПродаже"])], sources=[_source("Catalogs/ВидыЦен.xml", HASHES["price_type"])],
        extra_projection=[
            ("ПРЕДСТАВЛЕНИЕ(ВидыЦен.Назначение)", "Назначение", _fact("price_type.purpose", "catalog.price_type.purpose", "string", "attribute", "Назначение"), "Строка", "string"),
            ("ВидыЦен.ВалютаЦены", "Валюта", _fact("price_type.currency", "currency.ref", "entity_ref", "dimension", "Валюта"), "СправочникСсылка.Валюты", "object_ref"),
            ("ВидыЦен.ВалютаЦены.Код", "КодВалюты", _fact("price_type.currency_code", "currency.code", "string", "dimension", "Код валюты"), "Строка", "string"),
            ("ВидыЦен.ЦенаВключаетНДС", "ЦенаВключаетНДС", _fact("price_type.includes_vat", "catalog.price_type.includes_vat", "boolean", "attribute", "Цена включает НДС"), "Булево", "boolean"),
            ("ВидыЦен.ИспользоватьПриРозничнойПродаже", "ДляРозницы", _fact("price_type.for_retail", "catalog.price_type.for_retail", "boolean", "attribute", "Для розницы"), "Булево", "boolean"),
            ("ВидыЦен.ИспользоватьПриОптовойПродаже", "ДляОпта", _fact("price_type.for_wholesale", "catalog.price_type.for_wholesale", "boolean", "attribute", "Для опта"), "Булево", "boolean"),
        ], bindings=[], fixture_values={"ВидЦены": price_type, "Наименование": "Розничная", "Назначение": "Продажа", "Валюта": currency, "КодВалюты": "RUB", "ЦенаВключаетНДС": True, "ДляРозницы": True, "ДляОпта": False}, fixture_bindings=[{"parameter": "name_fragment", "value": ""}, {"parameter": "retail_use_only", "value": True}, {"parameter": "wholesale_use_only", "value": False}], labels=["price_type.name", "price_type.purpose", "price_type.currency_code"], role_proofs=[],
    )


def _build_characteristic() -> dict[str, Any]:
    item = _ref("СправочникСсылка.Номенклатура", 1801, "Куртка")
    characteristic = _ref("СправочникСсылка.ХарактеристикиНоменклатуры", 1802, "M")
    return _simple_resolver(
        skill_id="ut115.ref.item-characteristic.resolve-name-contains", version="1.0.0", name="Поиск характеристики товара", aliases=["найди характеристику выбранного товара"], entity_alias="Характеристика", entity_expression="Характеристики.Ссылка", physical_type="СправочникСсылка.ХарактеристикиНоменклатуры", identity_fact="characteristic.ref", identity_semantic="catalog.item.characteristic", label_expression="Характеристики.Наименование", label_alias="Наименование", label_fact="characteristic.name", label_semantic="catalog.item.characteristic.name", slot="selection.item_characteristic", from_clause="Справочник.ХарактеристикиНоменклатуры КАК Характеристики",
        where='Характеристики.Владелец = &Номенклатура\n  И Характеристики.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"', parameters=[_parameter("item", "Номенклатура", "entity_ref", ["previous_step", "session_context"], "object_ref", semantic_type="catalog.item", slots=["selection.item"]), _parameter("characteristic_text", "Характеристика", "normalized_text", ["user_slot"], "like_contains")], parameter_bindings=[{"parameter": "item", "query_parameter": "Номенклатура", "encoding": "object_ref"}, {"parameter": "characteristic_text", "query_parameter": "Шаблон", "encoding": "like_contains"}], metadata_hash=_combined_hash("characteristic", "item"), metadata_requirements=[_metadata("Справочник.ХарактеристикиНоменклатуры", ["Ссылка", "Наименование", "Владелец"]), _metadata("Справочник.Номенклатура", ["Ссылка", "Наименование"])], sources=[_source("Catalogs/ХарактеристикиНоменклатуры.xml", HASHES["characteristic"]), _source("Catalogs/Номенклатура.xml", HASHES["item"])],
        extra_projection=[("&Номенклатура", "Номенклатура", _fact("characteristic.item", "catalog.item", "entity_ref", "dimension", "Номенклатура"), "СправочникСсылка.Номенклатура", "object_ref"), ("ПРЕДСТАВЛЕНИЕ(&Номенклатура)", "НоменклатураНаименование", _fact("characteristic.item_name", "catalog.item.name", "string", "dimension", "Номенклатура"), "Строка", "string"), ("ИСТИНА", "Применима", _fact("characteristic.applies_to_item", "catalog.item.characteristic.applies_to_item", "boolean", "attribute", "Применима к товару"), "Булево", "boolean")], bindings=[_dependency("ut115.ref.item.resolve-name-contains", "1.2.0", "catalog.item")], fixture_values={"Характеристика": characteristic, "Наименование": "M", "Номенклатура": item, "НоменклатураНаименование": "Куртка", "Применима": True}, fixture_bindings=[{"parameter": "item", "value": item}, {"parameter": "characteristic_text", "value": "M"}], labels=["characteristic.name", "characteristic.item_name"], role_proofs=["characteristic.applies_to_item"], constraints=[{"kind": "fact_equals_parameter", "fact_id": "characteristic.item", "parameter": "item"}], invariants=[{"kind": "boolean_literal", "statement": 1, "value": True, "role": "state_filter", "occurrences": 1}],
    )


def _build_series(criterion: str) -> dict[str, Any]:
    item = _ref("СправочникСсылка.Номенклатура", 1901, "Куртка")
    characteristic = _ref("СправочникСсылка.ХарактеристикиНоменклатуры", 1902, "M")
    warehouse = _ref("СправочникСсылка.Склады", 1903, "Основной")
    purpose = _ref("СправочникСсылка.Назначения", 1904, "Заказ клиента")
    series = _ref("СправочникСсылка.СерииНоменклатуры", 1905, "S-001")
    parameter_name = "series_text" if criterion == "name-contains" else "series_number"
    query_parameter = "Шаблон" if criterion == "name-contains" else "НомерСерии"
    criterion_predicate = 'Серии.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"' if criterion == "name-contains" else "Серии.Номер = &НомерСерии"
    facts = [
        _fact("series.ref", "catalog.item.series", "entity_ref", "entity", "Серия"),
        _fact("series.name", "catalog.item.series.name", "string", "attribute", "Наименование"),
        _fact("series.number", "catalog.item.series.number", "string", "attribute", "Номер", required=criterion == "number-exact", nullable=criterion != "number-exact"),
        _fact("series.expiration_date", "catalog.item.series.expiration_date", "date", "attribute", "Годен до", required=False, nullable=True),
        _fact("series.production_date", "catalog.item.series.production_date", "date", "attribute", "Дата производства", required=False, nullable=True),
        _fact("series.item", "catalog.item", "entity_ref", "dimension", "Номенклатура"),
        _fact("series.item_name", "catalog.item.name", "string", "dimension", "Номенклатура"),
        _fact("series.characteristic", "catalog.item.characteristic", "entity_ref", "dimension", "Характеристика", required=False, nullable=True),
        _fact("series.characteristic_name", "catalog.item.characteristic.name", "string", "dimension", "Характеристика", required=False, nullable=True),
        _fact("series.storage_place_presentation", "inventory.storage_place.presentation", "string", "dimension", "Место хранения", required=False, nullable=True),
        _fact("series.inventory_purpose", "inventory.purpose", "entity_ref", "dimension", "Назначение", required=False, nullable=True),
        _fact("series.analytics_match", "catalog.item.series.analytics_match", "boolean", "attribute", "Связь подтверждена"),
    ]
    projections = [
        ("Аналитика.Серия", "Серия", "series.ref", "СправочникСсылка.СерииНоменклатуры", "object_ref"), ("Серии.Наименование", "Наименование", "series.name", "Строка", "string"), ("Серии.Номер", "Номер", "series.number", "Строка", "string"), ("Серии.ГоденДо", "ГоденДо", "series.expiration_date", "Дата", "date"), ("Серии.ДатаПроизводства", "ДатаПроизводства", "series.production_date", "Дата", "date"), ("&Номенклатура", "Номенклатура", "series.item", "СправочникСсылка.Номенклатура", "object_ref"), ("ПРЕДСТАВЛЕНИЕ(&Номенклатура)", "НоменклатураНаименование", "series.item_name", "Строка", "string"), ("&Характеристика", "Характеристика", "series.characteristic", "СправочникСсылка.ХарактеристикиНоменклатуры", "object_ref"), ("ПРЕДСТАВЛЕНИЕ(&Характеристика)", "ХарактеристикаНаименование", "series.characteristic_name", "Строка", "string"), ("ПРЕДСТАВЛЕНИЕ(&МестоХранения)", "МестоХранения", "series.storage_place_presentation", "Строка", "string"), ("&Назначение", "Назначение", "series.inventory_purpose", "СправочникСсылка.Назначения", "object_ref"), ("ИСТИНА", "СвязьПодтверждена", "series.analytics_match", "Булево", "boolean"),
    ]
    query = "ВЫБРАТЬ РАЗЛИЧНЫЕ\n" + ",\n".join(f"  {expr} КАК {alias}" for expr, alias, *_ in projections) + "\nИЗ РегистрСведений.АналитикаУчетаНоменклатуры КАК Аналитика\n  ВНУТРЕННЕЕ СОЕДИНЕНИЕ Справочник.СерииНоменклатуры КАК Серии\n  ПО Серии.Ссылка = Аналитика.Серия\nГДЕ Аналитика.Номенклатура = &Номенклатура\n  И Аналитика.Серия <> ЗНАЧЕНИЕ(Справочник.СерииНоменклатуры.ПустаяСсылка)\n  И (&Характеристика ЕСТЬ NULL ИЛИ Аналитика.Характеристика = &Характеристика)\n  И (&МестоХранения ЕСТЬ NULL ИЛИ Аналитика.МестоХранения = &МестоХранения)\n  И (&Назначение ЕСТЬ NULL ИЛИ Аналитика.Назначение = &Назначение)\n  И " + criterion_predicate + "\n" + _cursor_predicate("Серии.Наименование", "Аналитика.Серия", "ИмяКурсора") + "УПОРЯДОЧИТЬ ПО Серии.Наименование, Аналитика.Серия"
    columns = [_column(alias, fact_id, mcp_type, converter) for _, alias, fact_id, mcp_type, converter in projections]
    schema = [{"name": col["column"], "types": col["accepted_mcp_types"]} for col in columns]
    row = {"Серия": series, "Наименование": "S-001", "Номер": "S-001", "ГоденДо": "2027-12-31", "ДатаПроизводства": "2026-01-01", "Номенклатура": item, "НоменклатураНаименование": "Куртка", "Характеристика": characteristic, "ХарактеристикаНаименование": "M", "МестоХранения": "Основной", "Назначение": purpose, "СвязьПодтверждена": True}
    required = [fact["fact_id"] for fact in facts if fact["required"]]
    resolution, policy = _resolver_contract("series.ref", ["series.name", "series.item_name"] + (["series.number"] if criterion == "number-exact" else []), ["series.analytics_match"], "selection.item_series")
    parameters = [
        _parameter("item", "Номенклатура", "entity_ref", ["previous_step", "session_context"], "object_ref", semantic_type="catalog.item", slots=["selection.item"]),
        _parameter(parameter_name, "Критерий серии", "normalized_text" if criterion == "name-contains" else "string", ["user_slot"], "like_contains" if criterion == "name-contains" else "trim"),
        _parameter("characteristic", "Характеристика", "entity_ref", ["previous_step", "session_context"], "object_ref", required=False, semantic_type="catalog.item.characteristic", slots=["selection.item_characteristic"]),
        _parameter("warehouse", "Склад", "entity_ref", ["previous_step", "session_context"], "object_ref", required=False, semantic_type="catalog.warehouse", slots=["selection.warehouse"]),
        _parameter("inventory_purpose", "Назначение", "entity_ref", ["previous_step", "session_context"], "object_ref", required=False, semantic_type="inventory.purpose", slots=["selection.inventory_purpose"]),
    ]
    return _base_skill(
        skill_id=f"ut115.ref.item-series.resolve-{criterion}", version="1.0.0", name="Поиск серии выбранного товара", purpose="Разрешает серию только через exact строку АналитикаУчетаНоменклатуры.", aliases=["найди серию выбранного товара"], parameters=parameters, facts=facts, required_facts=required, query=query,
        parameter_bindings=[{"parameter": "item", "query_parameter": "Номенклатура", "encoding": "object_ref"}, {"parameter": parameter_name, "query_parameter": query_parameter, "encoding": "like_contains" if criterion == "name-contains" else "string"}, {"parameter": "characteristic", "query_parameter": "Характеристика", "encoding": "object_ref"}, {"parameter": "warehouse", "query_parameter": "МестоХранения", "encoding": "object_ref"}, {"parameter": "inventory_purpose", "query_parameter": "Назначение", "encoding": "object_ref"}], columns=columns, pagination=_pagination("Имя", "series.name", "series.ref"), metadata_hash=_combined_hash("series", "analytics", "item"),
        metadata_requirements=[_metadata("РегистрСведений.АналитикаУчетаНоменклатуры", ["Номенклатура", "Характеристика", "Серия", "МестоХранения", "Назначение"]), _metadata("Справочник.СерииНоменклатуры", ["Ссылка", "Наименование", "Номер", "ГоденДо", "ДатаПроизводства"])],
        sources=[_source("InformationRegisters/АналитикаУчетаНоменклатуры.xml", HASHES["analytics"]), _source("Catalogs/СерииНоменклатуры.xml", HASHES["series"])], tests=_mcp_test(f"ut.series.{criterion}", [{"parameter": "item", "value": item}, {"parameter": parameter_name, "value": "S-001"}, {"parameter": "characteristic", "value": characteristic}, {"parameter": "warehouse", "value": warehouse}, {"parameter": "inventory_purpose", "value": purpose}], row, schema, required), row_identity=["series.ref"], resolution=resolution, context_policy=policy,
        dependencies=[_dependency("ut115.ref.item.resolve-name-contains", "1.2.0", "catalog.item"), _dependency("ut115.ref.item-characteristic.resolve-name-contains", "1.0.0", "catalog.item.characteristic"), _dependency("ut115.ref.warehouse.resolve", "1.1.0", "catalog.warehouse"), _dependency("ut115.ref.inventory-purpose.resolve-name-contains", "1.0.0", "inventory.purpose")],
        result_constraints=[{"kind": "fact_equals_parameter", "fact_id": "series.item", "parameter": "item"}, {"kind": "fact_equals_parameter", "fact_id": "series.characteristic", "parameter": "characteristic"}, {"kind": "fact_equals_parameter", "fact_id": "series.inventory_purpose", "parameter": "inventory_purpose"}], invariants=[{"kind": "metadata_constant", "statement": 1, "constant_kind": "empty_reference", "symbol": "Справочник.СерииНоменклатуры.ПустаяСсылка", "role": "absence_sentinel", "occurrences": 1}, {"kind": "null_literal", "statement": 1, "value": "NULL", "role": "absence_filter", "occurrences": 3}, {"kind": "boolean_literal", "statement": 1, "value": True, "role": "state_filter", "occurrences": 1}],
    )


def _build_purpose() -> dict[str, Any]:
    purpose = _ref("СправочникСсылка.Назначения", 2001, "Заказ клиента")
    return _simple_resolver(
        skill_id="ut115.ref.inventory-purpose.resolve-name-contains", version="1.0.0", name="Поиск назначения запасов", aliases=["найди назначение запасов"], entity_alias="Назначение", entity_expression="Назначения.Ссылка", physical_type="СправочникСсылка.Назначения", identity_fact="purpose.ref", identity_semantic="inventory.purpose", label_expression="Назначения.Наименование", label_alias="Наименование", label_fact="purpose.name", label_semantic="inventory.purpose.name", slot="selection.inventory_purpose", from_clause="Справочник.Назначения КАК Назначения", where='Назначения.Наименование ПОДОБНО &Шаблон СПЕЦСИМВОЛ "~"', parameters=[_parameter("purpose_text", "Назначение", "normalized_text", ["user_slot"], "like_contains")], parameter_bindings=[{"parameter": "purpose_text", "query_parameter": "Шаблон", "encoding": "like_contains"}], metadata_hash=HASHES["purpose"], metadata_requirements=[_metadata("Справочник.Назначения", ["Ссылка", "Наименование", "ТипНазначения", "Партнер", "Договор", "Заказ", "НаправлениеДеятельности"])], sources=[_source("Catalogs/Назначения.xml", HASHES["purpose"])],
        extra_projection=[
            ("ПРЕДСТАВЛЕНИЕ(Назначения.ТипНазначения)", "ТипНазначения", _fact("purpose.type", "inventory.purpose.type", "string", "attribute", "Тип назначения"), "Строка", "string"),
            ("ПРЕДСТАВЛЕНИЕ(Назначения.Партнер)", "Партнер", _fact("purpose.partner_name", "party.partner.name", "string", "dimension", "Партнер", required=False, nullable=True), "Строка", "string"),
            ("ПРЕДСТАВЛЕНИЕ(Назначения.Договор)", "Договор", _fact("purpose.contract_presentation", "party.contract.presentation", "string", "dimension", "Договор", required=False, nullable=True), "Строка", "string"),
            ("ПРЕДСТАВЛЕНИЕ(Назначения.Заказ)", "Заказ", _fact("purpose.order_presentation", "document.order.presentation", "string", "dimension", "Заказ", required=False, nullable=True), "Строка", "string"),
            ("ПРЕДСТАВЛЕНИЕ(Назначения.НаправлениеДеятельности)", "Направление", _fact("purpose.business_direction_name", "business.direction.name", "string", "dimension", "Направление", required=False, nullable=True), "Строка", "string"),
        ], bindings=[], fixture_values={"Назначение": purpose, "Наименование": "Заказ клиента", "ТипНазначения": "Заказ", "Партнер": "Клиент", "Договор": "Договор", "Заказ": "Заказ 1", "Направление": "Розница"}, fixture_bindings=[{"parameter": "purpose_text", "value": "Заказ"}], labels=["purpose.name", "purpose.type"], role_proofs=[],
    )


def _document_producer(spec: dict[str, Any]) -> dict[str, Any]:
    prefix = spec["prefix"]
    document = _ref(spec["physical_type"], spec["ordinal"], spec["name"])
    entity_alias = spec["entity_alias"]
    facts = [
        _fact(f"{prefix}.ref", spec["semantic"], "entity_ref", "entity", spec["name"]),
        _fact(f"{prefix}.number", "document.number", "string", "attribute", "Номер"),
        _fact(f"{prefix}.date", "time.document_moment", "datetime", "time", "Дата"),
    ]
    projections = [("Документ.Ссылка", entity_alias, f"{prefix}.ref", spec["physical_type"], "object_ref"), ("Документ.Номер", "Номер", f"{prefix}.number", "Строка", "string"), ("Документ.Дата", "Дата", f"{prefix}.date", "Дата", "datetime")]
    row: dict[str, Any] = {entity_alias: document, "Номер": "0000-000001", "Дата": "2026-01-15T12:00:00Z"}
    for field in spec["fields"]:
        facts.append(field["fact"])
        projections.append((field["expression"], field["alias"], field["fact"]["fact_id"], field["mcp_type"], field["converter"]))
        row[field["alias"]] = field["fixture"]
    query = (
        "ВЫБРАТЬ\n"
        + ",\n".join(
            f"  {expr} КАК {alias}" for expr, alias, *_ in projections
        )
        + f"\nИЗ Документ.{spec['metadata_object']} КАК Документ\n"
        + f"ГДЕ {spec['where']}\n"
        + "  И (НЕ &ЕстьКурсор\n"
        + "    ИЛИ Документ.Дата < &ДатаКурсора\n"
        + "    ИЛИ (Документ.Дата = &ДатаКурсора\n"
        + "      И Документ.Ссылка > &СсылкаКурсора))\n"
        + "УПОРЯДОЧИТЬ ПО Документ.Дата УБЫВ, Документ.Ссылка"
    )
    columns = [_column(alias, fact_id, mcp_type, converter) for _, alias, fact_id, mcp_type, converter in projections]
    schema = [{"name": col["column"], "types": col["accepted_mcp_types"]} for col in columns]
    required = [fact["fact_id"] for fact in facts if fact["required"]]
    resolution, policy = _resolver_contract(f"{prefix}.ref", [f"{prefix}.number", f"{prefix}.date"], [], spec["slot"])
    return _base_skill(
        skill_id=spec["skill_id"], version=spec["version"], name=spec["name"], purpose="Возвращает resolver-capable список документов с typed filters.", aliases=spec["aliases"], parameters=spec["parameters"], facts=facts, required_facts=required, query=query, parameter_bindings=spec["parameter_bindings"], columns=columns,
        pagination={"strategy": "keyset", "has_cursor_query_parameter": "ЕстьКурсор", "sort": [{"fact_id": f"{prefix}.date", "direction": "desc"}, {"fact_id": f"{prefix}.ref", "direction": "asc"}], "cursor_bindings": [{"fact_id": f"{prefix}.date", "query_parameter": "ДатаКурсора", "encoding": "datetime"}, {"fact_id": f"{prefix}.ref", "query_parameter": "СсылкаКурсора", "encoding": "object_ref"}]}, metadata_hash=HASHES[spec["hash_key"]], metadata_requirements=[_metadata(f"Документ.{spec['metadata_object']}", spec["metadata_attributes"])], sources=[_source(f"Documents/{spec['metadata_object']}.xml", HASHES[spec["hash_key"]])], tests=_mcp_test(spec["skill_id"].replace("ut115.", "ut."), spec["fixture_bindings"], row, schema, required), row_identity=[f"{prefix}.ref"], resolution=resolution, context_policy=policy, dependencies=spec["dependencies"], invariants=spec.get("invariants", []),
    )


def _document_producers() -> list[dict[str, Any]]:
    org = _ref("СправочникСсылка.Организации", 3001, "Торговый дом")
    customer = _ref("СправочникСсылка.Партнеры", 3002, "Клиент")
    supplier = _ref("СправочникСсылка.Партнеры", 3003, "Поставщик")
    warehouse = _ref("СправочникСсылка.Склады", 3004, "Основной склад")
    period = {"start": "2026-01-01", "end_exclusive": "2027-01-01"}
    def entity_param(name: str, title: str, semantic: str, slot: str) -> dict[str, Any]:
        return _parameter(name, title, "entity_ref", ["previous_step", "session_context"], "object_ref", required=False, semantic_type=semantic, slots=[slot])
    period_param = _parameter("period", "Период", "period", ["user_slot", "previous_step"], "normalize_period")
    number_param = _parameter("document_number", "Номер документа", "string", ["user_slot", "system"], "trim", required=False, default="")
    def common_fields(prefix: str, party_kind: str, party_value: dict[str, Any], include_warehouse: bool, include_amount: bool, status_semantic: str) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = [
            {"expression": "Документ.Партнер", "alias": "Партнер", "fact": _fact(f"{prefix}.{party_kind}", f"party.{party_kind}", "entity_ref", "dimension", party_kind.capitalize()), "mcp_type": "СправочникСсылка.Партнеры", "converter": "object_ref", "fixture": party_value},
            {"expression": "ПРЕДСТАВЛЕНИЕ(Документ.Партнер)", "alias": "ПартнерНаименование", "fact": _fact(f"{prefix}.{party_kind}_name", "party.partner.name", "string", "dimension", "Партнер"), "mcp_type": "Строка", "converter": "string", "fixture": party_kind.capitalize()},
            {"expression": "Документ.Организация", "alias": "Организация", "fact": _fact(f"{prefix}.organization", "party.organization", "entity_ref", "dimension", "Организация"), "mcp_type": "СправочникСсылка.Организации", "converter": "object_ref", "fixture": org},
            {"expression": "ПРЕДСТАВЛЕНИЕ(Документ.Организация)", "alias": "ОрганизацияНаименование", "fact": _fact(f"{prefix}.organization_name", "party.organization.name", "string", "dimension", "Организация"), "mcp_type": "Строка", "converter": "string", "fixture": "Торговый дом"},
        ]
        if include_warehouse:
            fields += [
                {"expression": "Документ.Склад", "alias": "Склад", "fact": _fact(f"{prefix}.warehouse", "catalog.warehouse", "entity_ref", "dimension", "Склад"), "mcp_type": "СправочникСсылка.Склады", "converter": "object_ref", "fixture": warehouse},
                {"expression": "ПРЕДСТАВЛЕНИЕ(Документ.Склад)", "alias": "СкладНаименование", "fact": _fact(f"{prefix}.warehouse_name", "catalog.warehouse.name", "string", "dimension", "Склад"), "mcp_type": "Строка", "converter": "string", "fixture": "Основной склад"},
            ]
        if status_semantic:
            fields.append({"expression": "ПРЕДСТАВЛЕНИЕ(Документ.Статус)", "alias": "Статус", "fact": _fact(f"{prefix}.status", status_semantic, "string", "attribute", "Статус"), "mcp_type": "Строка", "converter": "string", "fixture": "Согласован"})
        if include_amount:
            fields += [
                {"expression": "Документ.СуммаДокумента", "alias": "СуммаДокумента", "fact": _fact(f"{prefix}.amount", "measure.document_amount", "money", "measure", "Сумма", unit={"mode": "from_fact", "fact_id": f"{prefix}.currency"}), "mcp_type": "Число", "converter": "decimal", "fixture": 1000.0},
                {"expression": "Документ.Валюта.Код", "alias": "Валюта", "fact": _fact(f"{prefix}.currency", "currency.code", "string", "dimension", "Валюта"), "mcp_type": "Строка", "converter": "string", "fixture": "RUB"},
            ]
        return fields
    specs: list[dict[str, Any]] = []
    sales_params = [_parameter("document_number", "Номер заказа", "string", ["user_slot"], "trim"), entity_param("customer", "Клиент", "party.customer", "selection.customer"), entity_param("organization", "Организация", "party.organization", "selection.organization")]
    sales_where = "Документ.Номер = &Номер И (&Клиент ЕСТЬ NULL ИЛИ Документ.Партнер = &Клиент) И (&Организация ЕСТЬ NULL ИЛИ Документ.Организация = &Организация)"
    specs.append({"skill_id": "ut115.sales.order-header-status-by-number", "version": "1.2.0", "prefix": "order", "semantic": "document.sales_order", "slot": "selection.sales_order", "physical_type": "ДокументСсылка.ЗаказКлиента", "entity_alias": "Заказ", "ordinal": 3100, "name": "Заказы клиентов по точному номеру", "party_title": "Клиент", "metadata_object": "ЗаказКлиента", "hash_key": "sales_order", "aliases": ["найди заказ клиента по номеру"], "parameters": sales_params, "parameter_bindings": [{"parameter": "document_number", "query_parameter": "Номер", "encoding": "string"}, {"parameter": "customer", "query_parameter": "Клиент", "encoding": "object_ref"}, {"parameter": "organization", "query_parameter": "Организация", "encoding": "object_ref"}], "where": sales_where, "fields": common_fields("order", "customer", customer, True, True, "document.sales_order.status"), "metadata_attributes": ["Ссылка", "Номер", "Дата", "Партнер", "Организация", "Склад", "Статус", "СуммаДокумента", "Валюта"], "fixture_bindings": [{"parameter": "document_number", "value": "0000-000001"}, {"parameter": "customer", "value": customer}, {"parameter": "organization", "value": org}], "dependencies": [], "invariants": [{"kind": "null_literal", "statement": 1, "value": "NULL", "role": "absence_filter", "occurrences": 2}]})
    def list_spec(skill_id: str, version: str, prefix: str, semantic: str, slot: str, physical: str, ordinal: int, name: str, party_kind: str, party_value: dict[str, Any], metadata_object: str, hash_key: str, include_warehouse: bool, include_amount: bool, status_semantic: str) -> dict[str, Any]:
        party_slot = f"selection.{party_kind}"
        params = [period_param, number_param, entity_param(party_kind, party_kind.capitalize(), f"party.{party_kind}", party_slot), entity_param("organization", "Организация", "party.organization", "selection.organization")]
        filters = ["Документ.Дата >= &НачалоПериода", "Документ.Дата < &КонецПериода", '(&Номер = "" ИЛИ Документ.Номер = &Номер)', "(&Партнер ЕСТЬ NULL ИЛИ Документ.Партнер = &Партнер)", "(&Организация ЕСТЬ NULL ИЛИ Документ.Организация = &Организация)"]
        bindings = [{"parameter": "period", "query_parameter": "НачалоПериода", "encoding": "period_start"}, {"parameter": "period", "query_parameter": "КонецПериода", "encoding": "period_end_exclusive"}, {"parameter": "document_number", "query_parameter": "Номер", "encoding": "string"}, {"parameter": party_kind, "query_parameter": "Партнер", "encoding": "object_ref"}, {"parameter": "organization", "query_parameter": "Организация", "encoding": "object_ref"}]
        fixture_bindings = [{"parameter": "period", "value": period}, {"parameter": "document_number", "value": ""}, {"parameter": party_kind, "value": party_value}, {"parameter": "organization", "value": org}]
        metadata_attrs = ["Ссылка", "Номер", "Дата", "Партнер", "Организация"]
        if status_semantic:
            metadata_attrs.append("Статус")
        if include_warehouse:
            params.append(entity_param("warehouse", "Склад", "catalog.warehouse", "selection.warehouse"))
            filters.append("(&Склад ЕСТЬ NULL ИЛИ Документ.Склад = &Склад)")
            bindings.append({"parameter": "warehouse", "query_parameter": "Склад", "encoding": "object_ref"})
            fixture_bindings.append({"parameter": "warehouse", "value": warehouse})
            metadata_attrs.append("Склад")
        if include_amount:
            metadata_attrs.extend(["СуммаДокумента", "Валюта"])
        entity_alias = {
            "shipment": "Реализация",
            "receipt": "Поступление",
            "purchase_order": "ЗаказПоставщику",
        }[prefix]
        return {"skill_id": skill_id, "version": version, "prefix": prefix, "semantic": semantic, "slot": slot, "physical_type": physical, "entity_alias": entity_alias, "ordinal": ordinal, "name": name, "party_title": party_kind.capitalize(), "metadata_object": metadata_object, "hash_key": hash_key, "aliases": [name.lower()], "parameters": params, "parameter_bindings": bindings, "where": "\n  И ".join(filters), "fields": common_fields(prefix, party_kind, party_value, include_warehouse, include_amount, status_semantic), "metadata_attributes": metadata_attrs, "fixture_bindings": fixture_bindings, "dependencies": [_dependency(f"ut115.ref.{party_kind}.resolve-name-contains", "1.0.0", f"party.{party_kind}"), _dependency("ut115.ref.organization.resolve-name-contains", "1.0.0", "party.organization")] + ([_dependency("ut115.ref.warehouse.resolve", "1.1.0", "catalog.warehouse")] if include_warehouse else []), "invariants": [{"kind": "empty_literal", "statement": 1, "value": "", "role": "absence_filter", "occurrences": 1}, {"kind": "null_literal", "statement": 1, "value": "NULL", "role": "absence_filter", "occurrences": 3 if include_warehouse else 2}]}
    specs += [
        list_spec("ut115.sales.shipment-list", "1.1.0", "shipment", "document.sales_shipment", "selection.sales_shipment", "ДокументСсылка.РеализацияТоваровУслуг", 3200, "Список реализаций", "customer", customer, "РеализацияТоваровУслуг", "shipment", True, True, "document.sales_shipment.status"),
        list_spec("ut115.purchase.receipt-list", "1.0.0", "receipt", "document.purchase_receipt", "selection.purchase_receipt", "ДокументСсылка.ПриобретениеТоваровУслуг", 3300, "Список поступлений", "supplier", supplier, "ПриобретениеТоваровУслуг", "receipt", False, True, ""),
        list_spec("ut115.purchase.order-list", "1.0.0", "purchase_order", "document.purchase_order", "selection.purchase_order", "ДокументСсылка.ЗаказПоставщику", 3400, "Список заказов поставщикам", "supplier", supplier, "ЗаказПоставщику", "purchase_order", True, False, "document.purchase_order.status"),
    ]
    # Transfer has two direction-specific warehouse inputs and no partner dimension.
    from_wh = _ref("СправочникСсылка.Склады", 3501, "Склад-отправитель")
    to_wh = _ref("СправочникСсылка.Склады", 3502, "Склад-получатель")
    transfer_fields = [
        {"expression": "Документ.СкладОтправитель", "alias": "СкладОтправитель", "fact": _fact("transfer.from_warehouse", "catalog.warehouse", "entity_ref", "dimension", "Склад-отправитель"), "mcp_type": "СправочникСсылка.Склады", "converter": "object_ref", "fixture": from_wh},
        {"expression": "ПРЕДСТАВЛЕНИЕ(Документ.СкладОтправитель)", "alias": "СкладОтправительНаименование", "fact": _fact("transfer.from_warehouse_name", "catalog.warehouse.name", "string", "dimension", "Склад-отправитель"), "mcp_type": "Строка", "converter": "string", "fixture": "Склад-отправитель"},
        {"expression": "Документ.СкладПолучатель", "alias": "СкладПолучатель", "fact": _fact("transfer.to_warehouse", "catalog.warehouse", "entity_ref", "dimension", "Склад-получатель"), "mcp_type": "СправочникСсылка.Склады", "converter": "object_ref", "fixture": to_wh},
        {"expression": "ПРЕДСТАВЛЕНИЕ(Документ.СкладПолучатель)", "alias": "СкладПолучательНаименование", "fact": _fact("transfer.to_warehouse_name", "catalog.warehouse.name", "string", "dimension", "Склад-получатель"), "mcp_type": "Строка", "converter": "string", "fixture": "Склад-получатель"},
        {"expression": "ПРЕДСТАВЛЕНИЕ(Документ.Статус)", "alias": "Статус", "fact": _fact("transfer.status", "document.stock_transfer.status", "string", "attribute", "Статус"), "mcp_type": "Строка", "converter": "string", "fixture": "Принято"},
    ]
    specs.append({"skill_id": "ut115.logistics.transfer-list", "version": "1.0.0", "prefix": "transfer", "semantic": "document.stock_transfer", "slot": "selection.stock_transfer", "physical_type": "ДокументСсылка.ПеремещениеТоваров", "entity_alias": "Перемещение", "ordinal": 3500, "name": "Список перемещений", "party_title": "", "metadata_object": "ПеремещениеТоваров", "hash_key": "transfer", "aliases": ["покажи перемещения товаров"], "parameters": [period_param, number_param, entity_param("from_warehouse", "Склад-отправитель", "catalog.warehouse", "selection.warehouse"), entity_param("to_warehouse", "Склад-получатель", "catalog.warehouse", "selection.warehouse")], "parameter_bindings": [{"parameter": "period", "query_parameter": "НачалоПериода", "encoding": "period_start"}, {"parameter": "period", "query_parameter": "КонецПериода", "encoding": "period_end_exclusive"}, {"parameter": "document_number", "query_parameter": "Номер", "encoding": "string"}, {"parameter": "from_warehouse", "query_parameter": "СкладОтправитель", "encoding": "object_ref"}, {"parameter": "to_warehouse", "query_parameter": "СкладПолучатель", "encoding": "object_ref"}], "where": 'Документ.Дата >= &НачалоПериода\n  И Документ.Дата < &КонецПериода\n  И (&Номер = "" ИЛИ Документ.Номер = &Номер)\n  И (&СкладОтправитель ЕСТЬ NULL ИЛИ Документ.СкладОтправитель = &СкладОтправитель)\n  И (&СкладПолучатель ЕСТЬ NULL ИЛИ Документ.СкладПолучатель = &СкладПолучатель)', "fields": transfer_fields, "metadata_attributes": ["Ссылка", "Номер", "Дата", "СкладОтправитель", "СкладПолучатель", "Статус"], "fixture_bindings": [{"parameter": "period", "value": period}, {"parameter": "document_number", "value": ""}, {"parameter": "from_warehouse", "value": from_wh}, {"parameter": "to_warehouse", "value": to_wh}], "dependencies": [_dependency("ut115.ref.warehouse.resolve", "1.1.0", "catalog.warehouse")], "invariants": [{"kind": "empty_literal", "statement": 1, "value": "", "role": "absence_filter", "occurrences": 1}, {"kind": "null_literal", "statement": 1, "value": "NULL", "role": "absence_filter", "occurrences": 2}]})
    return [_document_producer(spec) for spec in specs]


def build_skills() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for name in sorted(LEGACY_BYTES):
        if name.startswith("ut115.ref.item.resolve-"):
            skills.append(_upgrade_item_resolver(TARGET / name))
    skills.append(_build_item_details())
    skills.extend(_build_item_group_resolvers())
    skills.append(_build_group_members())
    skills.extend(_build_party_resolvers())
    skills.extend(_build_party_details(role) for role in ("partner", "customer", "supplier"))
    skills.append(_build_warehouse())
    skills.extend([_build_cash_desk("enterprise"), _build_cash_desk("pos")])
    skills.append(_build_price_type())
    skills.extend(_build_organizations())
    skills.append(_build_characteristic())
    skills.extend([_build_series("name-contains"), _build_series("number-exact")])
    skills.append(_build_purpose())
    skills.extend(_document_producers())
    ids = [skill["skill_id"] for skill in skills]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Generated skill_id inventory contains duplicates")
    expected = R01_IDS | R02_R12_IDS | DOCUMENT_PRODUCER_IDS
    if set(ids) != expected:
        raise RuntimeError(f"Generated inventory mismatch: missing={expected-set(ids)}, extra={set(ids)-expected}")
    if set(ids) != set(CAPABILITY_IDS_BY_SKILL_ID):
        raise RuntimeError("Capability mapping must exactly cover generated skills")
    if set(ids) != set(PREVIOUS_SKILL_VERSIONS):
        raise RuntimeError("Version migration must exactly cover generated skills")
    if len(R02_R12_IDS) != 27 or len(R02_R12_IDS - CONSUMER_IDS) != 22:
        raise RuntimeError("Frozen R02-R12 inventory is not 27/22/5")
    return skills


def _package(package_id: str, version: str, skills: Sequence[dict[str, Any]], note: str, *, external: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    lock_skills = {skill["skill_id"]: skill for skill in [*skills, *external]}
    package: dict[str, Any] = {
        "schema_version": "1.1.0",
        "document_type": "skill_package",
        "package_id": package_id,
        "version": version,
        "display": {"name_ru": "UT 11.5.27.56 resolver catalog", "description_ru": note},
        "target": {"configuration_id": CONFIG_ID, "configuration_name": CONFIG_NAME, "release": RELEASE, "compatibility_mode": MODE},
        "skills": list(skills),
        "dependency_lock": [{"skill_id": skill["skill_id"], "version": skill["version"], "digest": skill["integrity"]["digest"]} for skill in sorted(lock_skills.values(), key=lambda item: (item["skill_id"], item["version"]))],
        "provenance": {"author": "ChatBot 1C slice 3B", "created_at": CREATED, "release_note_ru": note, "source_references": [_source("Catalogs/Номенклатура.xml", HASHES["item"]), _source("InformationRegisters/АналитикаУчетаНоменклатуры.xml", HASHES["analytics"])]},
    }
    return generate_integrity(package)


def _load_legacy_skill(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((TARGET / name).read_text(encoding="utf-8"))
    )


def _filename(skill: dict[str, Any]) -> str:
    return f"{skill['skill_id']}-{skill['version']}.skill.json"


def build_packages(skills: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id = {skill["skill_id"]: skill for skill in skills}
    reference_ids = R01_IDS | R02_R12_IDS
    reference = [by_id[skill_id] for skill_id in sorted(reference_ids)]
    upgrade_ids = R01_IDS | {"ut115.ref.warehouse.resolve"}
    upgrade = [by_id[skill_id] for skill_id in sorted(upgrade_ids)]
    addition_ids = R02_R12_IDS - {"ut115.ref.warehouse.resolve"}
    additions = [by_id[skill_id] for skill_id in sorted(addition_ids)]
    legacy_roots = [
        _load_legacy_skill("ut115.doc.term.skill.json"),
        _load_legacy_skill("ut115.sales.order-lines.skill.json"),
        _load_legacy_skill("ut115.stock.balance.skill.json"),
    ]
    starter = [*reference, *[by_id[item] for item in sorted(DOCUMENT_PRODUCER_IDS)], *legacy_roots]
    if len({item["skill_id"] for item in starter}) != len(starter):
        raise RuntimeError("Starter package contains duplicate skill_id")
    return {
        REFERENCE_PACKAGE_NAME: _package("ut115.reference", "1.1.1", reference, "Self-contained production R01-R12 typed reference catalog."),
        UPGRADE_PACKAGE_NAME: _package("ut115.reference.existing-upgrade", "1.1.1", upgrade, "Replace-only typed upgrade for existing R01A-D and R06 skills."),
        ADDITIONS_PACKAGE_NAME: _package("ut115.reference.slice3-additions", "1.0.1", additions, "Create-only 26-document R02-R05/R07-R12 additions.", external=upgrade),
        STARTER_PACKAGE_NAME: _package("ut.starter.slice-three", "1.0.1", starter, "Built-in slice-three starter with typed reference catalog, five document producers and preserved legacy consumers."),
    }


def _validate(skills: Sequence[dict[str, Any]], packages: dict[str, dict[str, Any]]) -> None:
    harness = ContractHarness.discover(ROOT)
    typed_skills: list[Skill] = []
    for skill in skills:
        validated = harness.validate_document(skill)
        if not isinstance(validated, Skill):
            raise TypeError(skill["skill_id"])
        typed_skills.append(validated)
    by_name = {item.skill_id: item for item in typed_skills}
    for name, package in packages.items():
        available: list[Skill] = []
        if name == ADDITIONS_PACKAGE_NAME:
            available = [by_name[skill_id] for skill_id in sorted(R01_IDS | {"ut115.ref.warehouse.resolve"})]
        harness.validate_document(package, available_skills=available)


def main() -> None:
    _verify_legacy_bytes()
    skills = build_skills()
    packages = build_packages(skills)
    _validate(skills, packages)
    for skill in skills:
        _write_artifact(TARGET / _filename(skill), skill)
    for name, package in packages.items():
        _write_artifact(TARGET / name, package)
    _verify_legacy_bytes()
    print(f"generated {len(skills)} skills and {len(packages)} packages")
    print(f"R02-R12: {len(R02_R12_IDS)} ({len(R02_R12_IDS-CONSUMER_IDS)} resolvers, {len(CONSUMER_IDS)} consumers)")


if __name__ == "__main__":
    main()
