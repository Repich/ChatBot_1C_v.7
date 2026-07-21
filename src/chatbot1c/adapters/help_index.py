"""Built-in UT help index and stable ``ut-help://`` citations."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import cast
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag
from sqlalchemy import Engine, text

from chatbot1c.application.errors import ApplicationError
from chatbot1c.application.models import HelpChunk, HelpSearchRequest
from chatbot1c.application.ports import DocumentationPort
from chatbot1c.contracts.digest import canonicalize

PARSER_VERSION = "html4-bs4-v1"
TOKENIZER_VERSION = "ru-unicode-stem-v1"
CORPUS_ID = "ut_11_5_27_56_built_in_help"
RELEASE = "11.5.27.56"
_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
_SUFFIXES = (
    "иями",
    "ями",
    "ами",
    "ого",
    "ему",
    "ыми",
    "ий",
    "ый",
    "ая",
    "ое",
    "ые",
    "ов",
    "ам",
    "ах",
    "ом",
    "ы",
    "и",
    "а",
    "я",
)


@dataclass(frozen=True, slots=True)
class IndexBuildResult:
    revision: str
    manifest_digest: str
    source_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class _Source:
    source_id: str
    relative_path: str
    metadata_kind: str
    metadata_object: str
    title: str
    source_sha256: str
    chunks: tuple["_Chunk", ...]


@dataclass(frozen=True, slots=True)
class _Chunk:
    chunk_id: str
    ordinal: int
    heading: str
    heading_path: str
    anchor: str
    role: str
    plain_text: str
    normalized_text: str
    chunk_sha256: str


class HelpIndexBuilder:
    def __init__(self, engine: Engine, *, chunk_chars: int = 2400) -> None:
        self._engine = engine
        self._chunk_chars = chunk_chars

    def build(self, config_root: Path) -> IndexBuildResult:
        root = config_root.expanduser().resolve()
        sources = tuple(
            source
            for path in sorted(root.glob("**/Ext/Help/ru.html"))
            for source in (self._parse_source(root, path),)
            if source is not None
        )
        manifest = [
            {
                "relative_path": source.relative_path,
                "source_sha256": source.source_sha256,
                "parser_version": PARSER_VERSION,
                "tokenizer_version": TOKENIZER_VERSION,
            }
            for source in sources
        ]
        digest = hashlib.sha256(canonicalize(manifest)).hexdigest()
        revision = f"ut-help-{RELEASE}-{digest[:20]}"
        self._activate(revision, digest, sources)
        return IndexBuildResult(
            revision=revision,
            manifest_digest=digest,
            source_count=len(sources),
            chunk_count=sum(len(source.chunks) for source in sources),
        )

    def _parse_source(self, root: Path, html_path: Path) -> _Source | None:
        help_xml = html_path.parent.parent / "Help.xml"
        if not help_xml.is_file() or not _is_russian_help(help_xml):
            return None
        raw = html_path.read_bytes()
        try:
            html = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ApplicationError(
                "HELP_ENCODING_INVALID",
                f"Справка {html_path.name} не является UTF-8/UTF-8 BOM.",
                422,
            ) from error
        relative = html_path.relative_to(root).as_posix()
        source_sha = hashlib.sha256(raw).hexdigest()
        kind, metadata_object = _metadata_identity(relative)
        chunks, title = _extract_chunks(html, relative, self._chunk_chars)
        source_id = hashlib.sha256(
            f"{relative}\0{source_sha}".encode()
        ).hexdigest()
        return _Source(
            source_id=source_id,
            relative_path=relative,
            metadata_kind=kind,
            metadata_object=metadata_object,
            title=title,
            source_sha256=source_sha,
            chunks=chunks,
        )

    def _activate(
        self, revision: str, manifest_digest: str, sources: tuple[_Source, ...]
    ) -> None:
        connection = self._engine.connect()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            exists = connection.execute(
                text("SELECT 1 FROM help_corpora WHERE revision=:revision"),
                {"revision": revision},
            ).scalar_one_or_none()
            if exists is None:
                connection.execute(
                    text(
                        "INSERT INTO help_corpora (revision, corpus_id, release, "
                        "manifest_digest, created_at, active) VALUES (:revision, "
                        ":corpus_id, :release, :digest, :created_at, 0)"
                    ),
                    {
                        "revision": revision,
                        "corpus_id": CORPUS_ID,
                        "release": RELEASE,
                        "digest": manifest_digest,
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                )
                for source in sources:
                    persisted_source_id = _revision_scoped_id(
                        revision, source.source_id
                    )
                    connection.execute(
                        text(
                            "INSERT INTO help_sources (source_id, revision, "
                            "relative_path, metadata_kind, metadata_object, title, "
                            "source_sha256) VALUES (:source_id, :revision, :path, "
                            ":kind, :object, :title, :sha)"
                        ),
                        {
                            "source_id": persisted_source_id,
                            "revision": revision,
                            "path": source.relative_path,
                            "kind": source.metadata_kind,
                            "object": source.metadata_object,
                            "title": source.title,
                            "sha": source.source_sha256,
                        },
                    )
                    for chunk in source.chunks:
                        persisted_chunk_id = _revision_scoped_id(
                            revision, chunk.chunk_id
                        )
                        connection.execute(
                            text(
                                "INSERT INTO help_chunks (chunk_id, source_id, "
                                "revision, ordinal, heading, heading_path, anchor, "
                                "role, plain_text, normalized_text, chunk_sha256) "
                                "VALUES (:chunk_id, :source_id, :revision, :ordinal, "
                                ":heading, :heading_path, :anchor, :role, :plain, "
                                ":normalized, :sha)"
                            ),
                            {
                                "chunk_id": persisted_chunk_id,
                                "source_id": persisted_source_id,
                                "revision": revision,
                                "ordinal": chunk.ordinal,
                                "heading": chunk.heading,
                                "heading_path": chunk.heading_path,
                                "anchor": chunk.anchor,
                                "role": chunk.role,
                                "plain": chunk.plain_text,
                                "normalized": chunk.normalized_text,
                                "sha": chunk.chunk_sha256,
                            },
                        )
                        connection.execute(
                            text(
                                "INSERT INTO help_chunks_fts "
                                "(chunk_id, title, heading, normalized_text) VALUES "
                                "(:chunk_id, :title, :heading, :normalized)"
                            ),
                            {
                                "chunk_id": persisted_chunk_id,
                                "title": normalize_russian(source.title),
                                "heading": normalize_russian(chunk.heading),
                                "normalized": chunk.normalized_text,
                            },
                        )
            connection.execute(text("UPDATE help_corpora SET active=0"))
            connection.execute(
                text("UPDATE help_corpora SET active=1 WHERE revision=:revision"),
                {"revision": revision},
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class SQLiteHelpIndex(DocumentationPort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def search(self, request: HelpSearchRequest) -> tuple[HelpChunk, ...]:
        if request.source_kind != "built_in_help" or request.release != RELEASE:
            raise ApplicationError(
                "DOCUMENTATION_SOURCE_FORBIDDEN",
                "Разрешена только встроенная справка закрепленного релиза УТ.",
                403,
            )
        normalized_query = normalize_russian(request.query)
        if not normalized_query:
            return ()
        query = _fts_or_query(normalized_query)
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT h.chunk_id, h.heading, h.anchor, h.role, h.plain_text, "
                    "h.chunk_sha256, s.title, s.relative_path, s.metadata_kind, "
                    "s.metadata_object, "
                    "bm25(help_chunks_fts, 0.0, 5.0, 3.0, 1.0) AS rank "
                    "FROM help_chunks_fts JOIN help_chunks h "
                    "ON h.chunk_id=help_chunks_fts.chunk_id "
                    "JOIN help_sources s ON s.source_id=h.source_id "
                    "JOIN help_corpora c ON c.revision=h.revision "
                    "WHERE help_chunks_fts MATCH :query AND c.active=1 "
                    "AND c.release=:release ORDER BY rank LIMIT 200"
                ),
                {"query": query, "release": RELEASE},
            ).mappings()
            candidates = list(rows)
        result: list[HelpChunk] = []
        per_source: defaultdict[str, int] = defaultdict(int)
        normalized_raw = request.query.casefold().replace("ё", "е")
        for row in candidates:
            relative = cast(str, row["relative_path"])
            kind = cast(str, row["metadata_kind"])
            role = cast(str, row["role"])
            if request.metadata_kinds and kind not in request.metadata_kinds:
                continue
            if request.path_prefixes and not any(
                relative.startswith(prefix) for prefix in request.path_prefixes
            ):
                continue
            if request.roles and role not in request.roles:
                continue
            if per_source[relative] >= request.max_chunks_per_source:
                continue
            title = cast(str, row["title"])
            heading = cast(str, row["heading"])
            rank = float(row["rank"])
            boost = 0.0
            if normalized_raw in title.casefold().replace("ё", "е"):
                boost += 3.0
            if normalized_raw in heading.casefold().replace("ё", "е"):
                boost += 2.0
            anchor = cast(str, row["anchor"])
            result.append(
                HelpChunk(
                    chunk_id=cast(str, row["chunk_id"]),
                    title=title,
                    heading=heading,
                    text=cast(str, row["plain_text"]),
                    role=role,
                    source_uri=f"ut-help://{RELEASE}/{relative}#{quote(anchor, safe='')}",
                    relative_path=relative,
                    metadata_kind=kind,
                    metadata_object=cast(str, row["metadata_object"]),
                    anchor=anchor,
                    chunk_sha256=cast(str, row["chunk_sha256"]),
                    score=-rank + boost,
                )
            )
            per_source[relative] += 1
        result.sort(key=lambda item: (-item.score, item.relative_path, item.chunk_id))
        return tuple(result[: request.top_k])

    def active_revision(self) -> tuple[str, str] | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT revision, manifest_digest FROM help_corpora "
                    "WHERE active=1 LIMIT 1"
                )
            ).one_or_none()
        if row is None:
            return None
        return cast(str, row[0]), cast(str, row[1])


def render_help_citation(chunk: HelpChunk) -> str:
    label = f"{chunk.metadata_object}: {chunk.heading or chunk.title}"
    return f"[{escape(label)}]({chunk.source_uri})"


def normalize_russian(text_value: str) -> str:
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(text_value.casefold().replace("ё", "е")):
        stem = token
        for suffix in _SUFFIXES:
            if len(stem) - len(suffix) >= 4 and stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        tokens.append(stem)
    return " ".join(tokens)


def _fts_or_query(normalized_query: str) -> str:
    tokens = sorted(set(normalized_query.split()))[:32]
    return " OR ".join(f'"{token}"' for token in tokens)


def _revision_scoped_id(revision: str, logical_id: str) -> str:
    return hashlib.sha256(f"{revision}\0{logical_id}".encode()).hexdigest()


def _is_russian_help(path: Path) -> bool:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        root = ET.fromstring(raw)
    except (OSError, UnicodeDecodeError, ET.ParseError):
        return False
    return any(
        element.tag.rsplit("}", 1)[-1] == "Page" and element.text == "ru"
        for element in root
    )


def _metadata_identity(relative_path: str) -> tuple[str, str]:
    parts = relative_path.split("/")
    mapping = {
        "Catalogs": "catalog",
        "Documents": "document",
        "Reports": "report",
        "DataProcessors": "data_processor",
        "CommonForms": "common_form",
        "Subsystems": "subsystem",
    }
    kind = mapping.get(parts[0], "other")
    metadata_object = parts[1] if len(parts) > 1 else parts[0]
    if "Forms" in parts:
        form_index = parts.index("Forms")
        if len(parts) > form_index + 1:
            kind = "form"
            metadata_object = f"{metadata_object}.{parts[form_index + 1]}"
    return kind, metadata_object


def _extract_chunks(
    html: str, relative_path: str, chunk_chars: int
) -> tuple[tuple[_Chunk, ...], str]:
    soup = BeautifulSoup(html, "html.parser")
    for ignored in soup.find_all(["script", "style"]):
        ignored.decompose()
    first_h1 = soup.find("h1")
    title = (
        first_h1.get_text(" ", strip=True)
        if isinstance(first_h1, Tag)
        else Path(relative_path).parts[-4]
    )
    heading_stack: dict[int, str] = {}
    current_heading = title
    current_anchor = "top"
    blocks: list[str] = []
    sections: list[tuple[str, str, str, tuple[str, ...]]] = []

    def flush() -> None:
        nonlocal blocks
        if blocks:
            heading_path = " / ".join(
                heading_stack[level] for level in sorted(heading_stack)
            ) or title
            sections.append(
                (current_heading, heading_path, current_anchor, tuple(blocks))
            )
            blocks = []

    for element in soup.find_all(
        ["a", "h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]
    ):
        if element.name == "a":
            anchor_name = element.get("name") or element.get("id")
            if anchor_name:
                flush()
                current_anchor = str(anchor_name)
            continue
        if element.name and element.name.startswith("h"):
            flush()
            level = int(element.name[1])
            current_heading = element.get_text(" ", strip=True) or title
            heading_stack[level] = current_heading
            for stale in tuple(heading_stack):
                if stale > level:
                    del heading_stack[stale]
            previous = element.find_previous_sibling("a")
            if isinstance(previous, Tag) and (
                previous.get("name") or previous.get("id")
            ):
                current_anchor = str(previous.get("name") or previous.get("id"))
            elif level == 1:
                current_anchor = "top"
            continue
        if element.name == "p" and element.find_parent("li") is not None:
            continue
        if element.name == "p" and element.find(["ul", "ol"]):
            value = " ".join(element.find_all(string=True, recursive=False)).strip()
        else:
            value = element.get_text(" ", strip=True)
        value = re.sub(r"\s+", " ", value)
        if value:
            blocks.append(value)
    flush()

    chunks: list[_Chunk] = []
    ordinal = 0
    for heading, heading_path, anchor, section_blocks in sections:
        grouped = _group_blocks(section_blocks, chunk_chars)
        for text_value in grouped:
            ordinal += 1
            sha = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
            chunk_id = hashlib.sha256(
                f"{relative_path}\0{anchor}\0{ordinal}\0{sha}".encode()
            ).hexdigest()[:40]
            chunks.append(
                _Chunk(
                    chunk_id=chunk_id,
                    ordinal=ordinal,
                    heading=heading,
                    heading_path=heading_path,
                    anchor=anchor,
                    role=_infer_role(heading, text_value),
                    plain_text=text_value,
                    normalized_text=normalize_russian(text_value),
                    chunk_sha256=sha,
                )
            )
    return tuple(chunks), title


def _group_blocks(blocks: tuple[str, ...], limit: int) -> tuple[str, ...]:
    grouped: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        parts = [block[index : index + limit] for index in range(0, len(block), limit)]
        for part in parts:
            if current and size + len(part) + 1 > limit:
                grouped.append("\n".join(current))
                current = []
                size = 0
            current.append(part)
            size += len(part) + 1
    if current:
        grouped.append("\n".join(current))
    return tuple(grouped)


def _infer_role(heading: str, body: str) -> str:
    value = f"{heading} {body[:300]}".casefold()
    if any(word in value for word in ("огранич", "не допуска", "важно")):
        return "restriction"
    if any(word in value for word in ("порядок", "для того", "выполните")):
        return "procedure"
    if any(word in value for word in ("переход", "открыть", "раздел")):
        return "navigation"
    return "definition"
