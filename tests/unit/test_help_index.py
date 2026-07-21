from __future__ import annotations

import asyncio
from pathlib import Path

from chatbot1c.adapters.help_index import HelpIndexBuilder, SQLiteHelpIndex
from chatbot1c.adapters.persistence import SQLiteStore
from chatbot1c.application.models import HelpSearchRequest


def _write_help(root: Path, kind: str, name: str, html: str) -> Path:
    extension = root / kind / name / "Ext"
    help_dir = extension / "Help"
    help_dir.mkdir(parents=True)
    (extension / "Help.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><Help><Page>ru</Page></Help>',
        encoding="utf-8",
    )
    path = help_dir / "ru.html"
    path.write_bytes(b"\xef\xbb\xbf" + html.encode("utf-8"))
    return path


def test_help_index_search_filters_stable_uri_and_revision_rebuild(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    order_help = _write_help(
        config,
        "Documents",
        "CustomerOrder",
        """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
        <html><head><style>.hidden {display:none}</style></head><body>
        <h1>Заказ клиента</h1><a name="purpose"></a><h2>Назначение</h2>
        <p>Заказ клиента фиксирует потребность покупателя и условия продажи;
        <a href="http://example.invalid/external">внешняя ссылка</a> приведена один раз.</p>
        <ul><li>Документ используется для планирования отгрузки.</li></ul>
        <script>секретный скрипт не индексировать</script></body></html>""",
    )
    item_help = _write_help(
        config,
        "Catalogs",
        "Items",
        """<html><body><h1>Номенклатура</h1><a name="main"></a>
        <h2>Описание</h2><p>Карточка товара содержит артикул и наименование.</p>
        </body></html>""",
    )

    store = SQLiteStore(f"sqlite:///{tmp_path / 'help.sqlite3'}")
    store.initialize()
    builder = HelpIndexBuilder(store.engine)
    index = SQLiteHelpIndex(store.engine)

    first = builder.build(config)
    request = HelpSearchRequest(
        query="Что такое заказ клиента в УТ",
        metadata_kinds=("document",),
        path_prefixes=("Documents/",),
        top_k=4,
    )
    found = asyncio.run(index.search(request))
    assert found
    assert all(chunk.metadata_kind == "document" for chunk in found)
    assert all(chunk.relative_path.startswith("Documents/") for chunk in found)
    assert found[0].source_uri.startswith(
        "ut-help://11.5.27.56/Documents/CustomerOrder/Ext/Help/ru.html#"
    )
    assert found[0].text.count("внешняя ссылка") == 1
    assert "секретный скрипт" not in found[0].text
    stable_uri = found[0].source_uri

    item_help.write_bytes(
        b"\xef\xbb\xbf"
        + "<html><body><h1>Номенклатура</h1><h2>Описание</h2>"
        "<p>Карточка товара теперь содержит штрихкод.</p></body></html>".encode()
    )
    second = builder.build(config)
    assert second.revision != first.revision
    assert second.source_count == 2
    assert asyncio.run(index.search(request))[0].source_uri == stable_uri

    repeated = builder.build(config)
    assert repeated == second
    assert index.active_revision() == (second.revision, second.manifest_digest)
    assert order_help.read_bytes().startswith(b"\xef\xbb\xbf")
    store.engine.dispose()
