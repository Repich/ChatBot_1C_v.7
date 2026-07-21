# Вертикальные срезы реализации

Каждый срез должен проходить через web/API, application, domain, persistence и
реальный/fixture adapter. Нельзя сначала реализовать 87 отдельных handlers, а
затем пытаться объединить их.

## 0. Contract harness

Цель: сделать архитектурные границы исполняемо проверяемыми до UI.

Реализовать:

- Python project/lock, configuration loader и composition root;
- bounded JSON loader с byte/depth/node/array limits для всех четырех contracts;
- Pydantic models для plan/evidence/skill, Draft 2020-12 validation;
- RFC 8785/SHA-256 utility;
- domain outcomes, facts/entity refs/periods и coverage validator;
- lexer/shallow parser query package: `single_select`, связанный
  `linked_temp_batch`, producer/consumer graph и typed invariant constants;
- semantic checks exact `provides/output`, final fact coverage по
  type/cardinality/unit/time, closed package lock/digest conflict и evidence
  disagreement;
- unit tests всех схемных примеров и отрицательных fixtures;
- CI на macOS/Linux runner, подготовить Windows job.

Готово, когда поврежденный/oversized skill/package/plan/evidence стабильно
отклоняется с JSON pointer и ни одна model DTO не допускает extra fields.
Положительные tests обязаны принять один SELECT и связанный temp-table batch;
отрицательные - orphan/independent/write statements, mismatch execution graph,
undeclared/business literals, orphan `provides`, uncovered final fact, лишний
lock entry/digest conflict и молчаливый выбор disagreement position.

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
- импорт ровно того же exported JSON во второй чистый app data dir через web и
  CLI: dependency-free bare skill и selected-root self-contained package;
- следующий вопрос видит skill без restart;
- turn, начатый до replace, заканчивается на старом pinned snapshot.

### Exit criteria

- schema/semantic/import tests зеленые;
- Q001, Q011, Q036-Q037 и Q102 на fixtures;
- live MCP smoke, single SELECT и один linked temp-table read-only package после
  запуска сервера;
- DeepSeek structured output smoke;
- no secret canary в log/bundle;
- базовый turn укладывается в 30 секунд в контрольной среде.

## 2. Outcome, pagination и failure slice

Реализовать:

- все outcomes `success_with_rows`, `success_empty`, `zero_aggregate`, `partial`,
  `query_error`, `mcp_unavailable`, `llm_unavailable`, `contract_error`;
- exact null/empty matrix and strict `partial` versus `contract_error` boundary;
- reverse-closure derivation of required/optional steps from typed final
  requirements, required-first scheduling and branch-aware failure reduction;
- evidence coverage exact-copies `requirement_id/required`; optional missing is
  reported but excluded from `coverage.sufficient`; required collection
  completeness is checked separately from fact status;
- emit Evidence 1.1 with explicit `collection_scope`/`required`, while reading
  frozen 1.0 through the version-specific compatibility branch only;
- keyset для unbounded producers и prefix только по cited/digest-pinned
  cardinality proof, exact behavior на `maximum_total`, default 20,
  30-minute single-use continuation handles и public continuation DTO/errors;
- reject paged aggregate/exact skills; typed zero aggregate uses a dedicated
  non-paginated one-row complete-set producer;
- reject transferable keyset skills unless sort/cursor ordered bijection,
  unique parameters, typed non-null coordinates, full identity suffix and
  parsed query ORDER BY/after-predicate are all proved at import;
- deterministic answer fallback;
- dependency diagnostics и timeout/retry policies;
- two-phase manual session/trace/raw-payload clear с preview и confirmation
  token;
- R06 warehouse resolver только по metadata-proven name/`ТипСклада`/
  `Подразделение`, без direct `Организация`/`Назначение`.
- migration оставшихся R01A/R01B/R01C/R01D, SP03 и SL01 с недоказанного
  `prefix:1000` на metadata-proven stable keyset; R06 и SP04 остаются keyset.

Тесты: Q012/Q054/Q099/Q102-Q106 и `Q015.list`/`Q031.list`; total/full Q015 и
Q031 остаются `not_run` до отдельных aggregate producers. Дополнительно:
malformed MCP envelope, one-row zero/null aggregate, 22-row Q031 page that must
not be labeled total, truncated required result, keyset no-duplicate/no-skip,
proved-prefix `M-1/M/M+1`, continuation state matrix, clear preview/confirm,
retry deadline exhaustion. Нормативный black-box contract:
`docs/testing/slice2_acceptance_contract.md`.

Срез закрывает только list component `AC-024.list`. Rank component и глобальный
`AC-024` остаются `not_run` до отдельного прогона M07 с проверкой состава,
порядка, направления и показателя ранжирования.

## 3. Entity/context slice

Расширить typed resolvers и context ledger единым protocol, без
object-specific branches в core:

- item, item group, partner/customer/supplier, warehouse, cash desk, price type;
- sales order, shipment, purchase receipt/order, stock transfer;
- characteristic, series, inventory purpose как optional entity parameters;
- core-derived resolver outcomes `0/1/N` и selection proof: resolver candidates,
  display lists и line rows не становятся context автоматически;
- persisted one-use pending clarification, связанный с исходным turn/plan и
  exact typed choices, вместо replanning recent messages как нового вопроса;
- opaque server-side context handles и semantic slot replacement/expiry/
  invalidation с exact origin provenance;
- generic typed scalar/filter slots с отдельным safe `confirmed_filter` policy:
  retained moment/period/enum/detail preference сохраняют exact value type,
  origin/allowed-source proof и consumer compatibility без recomputation;
- outbound DeepSeek regression gate: только handle, semantic type,
  presentation и origin turn; полный ref остается server-side/в diagnostics.

Тесты: Q013-Q020, Q029, Q037, Q042, Q056-Q057, Q062-Q064, Q073, Q081-Q082,
Q091-Q097, Q108. Property tests проверяют, что presentation collision не меняет UUID
identity и wrong semantic/physical type отклоняется до MCP. Для Q015 здесь
проверяется resolver/list/context component; `Q015.total` и full Q015 остаются
`not_run` до отдельного aggregate producer согласно slice 2.
Q091 acceptance отдельно доказывает byte-for-byte сохранение balance moment в
Q092/Q093 без refresh от нового turn time или MCP.

Нормативный black-box, migration и exit-matrix contract:
`docs/testing/slice3_acceptance_contract.md`.

## 4. Generic operators and composition slice

Реализовать allowlist operators полностью:

- count distinct semantics с обязательным `visible_page|complete_set` scope;
  total count разрешен только для complete-set/aggregate producer и никогда не
  запускает скрытый drain paged input;
- aggregate с unit/currency;
- rank с stable tie policy;
- filter zero/positive/negative/null;
- equijoin по typed identities/dimensions;
- calculate с unit checks;
- grouped/timeline renderer.

Тесты: Q016, Q023-Q024, Q032-Q040, Q043-Q050, Q053-Q055, Q061, Q067,
Q071-Q077, Q084-Q090. Отдельные negative tests различают rows/documents/items,
revenue/profit/debt и несопоставимые валюты.

После появления SP06 этот срез дополнительно активирует full Q031 composite:
SP04 дает только visible keyset page, SP06 - distinct total на том же
period/filter fingerprint. До этого full Q031 не повышается из `not_run`.
Full Q015 аналогично активируется только после отдельного item aggregate
producer с тем же normalized empty-article filter; его skill contract должен
быть добавлен в business package до full-catalog gate.

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
- execution graph и все query literals точно соответствуют ADR-0003;
- compatibility и metadata assertions проверены;
- positive/negative fixtures и live test пройдены;
- `provides.fact_types` точно равно
  `output_contract.facts[*].semantic_type` как множество;
- required/final facts имеют exact bindings, semantic types, units/time/cardinality;
- dependency lock точно равен замыканию DAG, digests совпадают, cycles отсутствуют;
- web/CLI import/export дают одинаковые digests;
- package доступен следующему turn без restart;
- trace позволяет offline replay normalizer/coverage/renderer.
