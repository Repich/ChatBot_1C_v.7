# Вертикальные срезы реализации

Каждый срез должен проходить через web/API, application, domain, persistence и
реальный/fixture adapter. Нельзя сначала реализовать 87 отдельных handlers, а
затем пытаться объединить их.

## 0. Contract harness

Цель: сделать архитектурные границы исполняемо проверяемыми до UI.

Реализовать:

- Python project/lock, configuration loader и composition root;
- Pydantic models для plan/evidence/skill, Draft 2020-12 validation;
- RFC 8785/SHA-256 utility;
- domain outcomes, facts/entity refs/periods и coverage validator;
- unit tests всех схемных примеров и отрицательных fixtures;
- CI на macOS/Linux runner, подготовить Windows job.

Готово, когда поврежденный skill/package/plan/evidence стабильно отклоняется с
JSON pointer и ни одна model DTO не допускает extra fields.

## 1. Минимальный вертикальный срез

Этот срез первым доказывает основную гипотезу end-to-end.

### Scope

1. FastAPI, server-rendered chat, session persistence, SSE progress.
2. DeepSeek planner adapter и `planner-output.schema.json`.
3. MCP adapter с allowlist `execute_query/get_metadata` и fixture transport.
4. Help index для `Ext/Help/ru.html` и citation renderer.
5. Atomic catalog import/hot reload через web и CLI.
6. Четыре atomic skills в одном package:
   - определение термина/назначения заказа клиента из справки;
   - поиск номенклатуры по артикулу/названию;
   - фактический остаток подтвержденного товара;
   - строки подтвержденного заказа клиента.
7. Operators `normalize_period` и generic table renderer.
8. Один composite/follow-up flow:
   - найти заказ по номеру;
   - сохранить exact order ref;
   - «Какие товары входят в этот заказ?».
9. Полный trace и diagnostic ZIP.

### Обязательные демонстрации

- documentation answer с `ut-help://...` citation;
- simple data query;
- parameterized `_objectRef` из шага в шаг;
- DeepSeek не видит query text;
- `success_empty` и `query_error` имеют разные UI messages;
- импорт того же package во второй чистый app data dir через web и CLI;
- следующий вопрос видит skill без restart;
- turn, начатый до replace, заканчивается на старом pinned snapshot.

### Exit criteria

- schema/semantic/import tests зеленые;
- Q001, Q011, Q036-Q037 и Q102 на fixtures;
- live MCP smoke и два read-only queries после запуска сервера;
- DeepSeek structured output smoke;
- no secret canary в log/bundle;
- базовый turn укладывается в 30 секунд в контрольной среде.

## 2. Outcome, pagination и failure slice

Реализовать:

- все outcomes `success_with_rows`, `success_empty`, `zero_aggregate`, `partial`,
  `query_error`, `mcp_unavailable`, `llm_unavailable`, `contract_error`;
- keyset/prefix page policies, default 20, continuation handles;
- deterministic answer fallback;
- dependency diagnostics и timeout/retry policies;
- manual session/trace clear.

Тесты: Q012/Q015/Q031/Q054/Q099/Q102-Q106, malformed MCP envelope, one-row
zero/null aggregate, truncated required result, retry deadline exhaustion.

## 3. Entity/context slice

Расширить typed resolvers и context ledger:

- item, item group, partner/customer/supplier, warehouse, cash desk, price type;
- sales order, shipment, purchase receipt/order, stock transfer;
- characteristic, series, inventory purpose как optional entity parameters;
- pending clarification и context replacement semantics.

Тесты: Q013-Q020, Q029, Q037, Q042, Q056-Q057, Q062-Q064, Q073, Q081-Q082,
Q091-Q097, Q108. Property tests проверяют, что presentation collision не меняет UUID
identity и wrong semantic type отклоняется.

## 4. Generic operators and composition slice

Реализовать allowlist operators полностью:

- count distinct semantics;
- aggregate с unit/currency;
- rank с stable tie policy;
- filter zero/positive/negative/null;
- equijoin по typed identities/dimensions;
- calculate с unit checks;
- grouped/timeline renderer.

Тесты: Q016, Q023-Q024, Q032-Q040, Q043-Q050, Q053-Q055, Q061, Q067,
Q071-Q077, Q084-Q090. Отдельные negative tests различают rows/documents/items,
revenue/profit/debt и несопоставимые валюты.

## 5. Business data packages

Навыки поставляются группами, но исполняются теми же механизмами:

1. Reference data и pricing.
2. Sales и purchases.
3. Stock, movement, inventory и delivery.
4. Settlements.
5. Cash и finance.

Для каждого skill обязательны metadata proof, fixture tests, live positive и
negative test, exact output mapping, unit/time semantics, list policy. После
каждой группы запускаются относящиеся сценарии корпуса и общие regression tests.
Запрещено добавлять условие по Q-ID или конкретному товару в application code.

## 6. Documentation package

Реализовать отдельные declarative skills поверх одного index engine для
term/procedure/error/status/source. Провести ручную проверку citations и порядка
procedure steps для Q001-Q010. Source text не смешивается с data query; внешние
источники в первом release не индексируются. Negative schema/retrieval tests
проверяют hard-reject внешнего `source_kind`. Fixture с расходящимися фрагментами
одного встроенного корпуса обязан создать typed disagreement и показать обе
позиции с отдельными citations без молчаливого выбора по rank.

## 7. Full catalog and acceptance preparation

Довести active catalog до всех 87 capability IDs. Принятый acceptance corpus
содержит `Q001-Q116`; десять новых end-to-end сценариев зарезервированы так:

| ID | Обязательное покрытие |
| --- | --- |
| Q107 | FR-010, детерминированный ответ «кто ты/что умеешь» из active catalog summary |
| Q108 | `CAP-COMMON-DETAIL` |
| Q109 | `CAP-CUSTOMER-SALES-HISTORY` |
| Q110 | `CAP-PURCHASE-SUPPLIER-RANK` |
| Q111 | `CAP-SALES-ORDER-HEADER` |
| Q112 | `CAP-SALES-PROFIT` |
| Q113 | `CAP-SALES-RETURN` |
| Q114 | `CAP-SALES-SHIPMENT-LINES` |
| Q115 | `CAP-STOCK-CONSUMPTION` |
| Q116 | `CAP-STOCK-EXPECTED` |

Помимо них добавить web/browser tests AC-033..040, package portability, secret
scan и independent `acceptance_observable_state` marker/baseline tooling.

## 8. Stabilization and Windows

1. Запустить все 116 сценариев, недетерминированные повторить не менее трех раз.
2. Сверить data evidence с независимыми control queries при том же marker.
3. Проверить 30/90-second SLO и failure injection.
4. Выполнить package replace/delete при активных turns.
5. Проверить crash recovery catalog swap/turn persistence.
6. После macOS acceptance выполнить Windows smoke: startup, SQLite/FTS5,
   DeepSeek, MCP, chat, diagnostics, web+CLI portability.
7. Собрать application release, отдельный skill package, schemas, checksums,
   instructions и acceptance report.

## 9. Порядок разработки внутри каждого среза

1. Зафиксировать contract/fixture и acceptance expectation.
2. Реализовать domain rule и unit tests.
3. Реализовать port/use case.
4. Реализовать adapter contract test.
5. Подключить API/UI/CLI.
6. Прогнать relevant corpus group и diagnostic replay.
7. Исправлять общую причину в contract/mechanism, не частный Q-ID.

## 10. Definition of Done любого skill package

- schemas, digest и semantic lint пройдены;
- нет secrets/local paths/session IDs/concrete demo values в identity/template;
- compatibility и metadata assertions проверены;
- positive/negative fixtures и live test пройдены;
- required facts имеют exact bindings, units/time/cardinality;
- dependency DAG замкнут и без cycles;
- web/CLI import/export дают одинаковые digests;
- package доступен следующему turn без restart;
- trace позволяет offline replay normalizer/coverage/renderer.
