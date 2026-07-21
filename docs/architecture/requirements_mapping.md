# Архитектурная трассируемость требований

Ссылки `RL`, `SC`, `IC`, `PO`, `IS` означают соответственно
`request_lifecycle.md`, `skill_contract.md`, `integration_contracts.md`,
`persistence_and_observability.md`, `implementation_slices.md`.

## 1. Функциональные требования

| ID | Архитектурный механизм | Проверка |
| --- | --- | --- |
| FR-001 | FastAPI/Jinja chat boundary, UTF-8 Russian DTO | Browser submit Russian text |
| FR-002 | `sessions/messages/context_facts`, context version | Reload + follow-up corpus |
| FR-003 | DeepSeek typed interpretation + aliases/examples shortlist | Paraphrase/typo corpus repeats |
| FR-004 | `intent_kind` and source boundary | Data/doc/mixed/out-of-scope tests |
| FR-005 | User API contains no metadata/query/MCP concepts | Browser usability review |
| FR-006 | Planner `clarify` closed contract, one question | Q029/Q056 etc. |
| FR-007 | Missing ambiguous metric fails coverage | Q057/Q077/Q098 |
| FR-008 | Confirmed context ledger and slot replacement | 11 follow-up scenarios; acceptance threshold 10 |
| FR-009 | Message timestamps + SSE progress | Browser slow-call test |
| FR-010 | Deterministic product-meta response from active catalog summary | Q107 |
| FR-011 | `ReadOnly1CPort` only | MCP contract trace |
| FR-012 | Tool allowlist + no write port/BSL | Q101 and forbidden-tool test |
| FR-013 | Catalog shortlist + exact version skill calls | Plan trace/coverage proof |
| FR-014 | Validated DAG and generic operators | Composite corpus scenarios |
| FR-015 | Typed bindings; values absent from skill identity | Q091-Q093 + lint |
| FR-016 | `normalize_period`, half-open period, turn clock/timezone | Relative/date scenarios |
| FR-017 | count/aggregate/rank/group operators | Numeric/list scenarios |
| FR-018 | Different semantic types for row/document/item/unit count | Q016/Q032/Q055 etc. |
| FR-019 | Unit contract `currency` resolved/unresolved | Money evidence validation |
| FR-020 | Fact `moment` versus `period` | Coverage time semantics |
| FR-021 | Outcome state machine after successful envelope | Q102/Q103/Q105 |
| FR-022 | Final required-fact coverage, no intermediate final | Q042/Q076/Q094-Q095 |
| FR-023 | Evidence gate and no answer on missing facts | Q102-Q106 |
| FR-024 | Page policy, default 20, random opaque handle with server-side continuation state | List/continue/full tests |
| FR-025 | Renderer manifest requires metric/object/time/unit/filters | Golden response DTO tests |
| FR-026 | Doc term skill + built-in help citation | Q001/Q002/Q010 |
| FR-027 | Procedure role chunks + citation | Q003-Q006/Q008/Q009 |
| FR-028 | Error cause/verification roles | Q007 |
| FR-029 | Citation title + `ut-help://` location | Q001-Q010 |
| FR-030 | Evidence `source_boundary` | Mixed-boundary negative test |
| FR-031 | v1 accepts only built-in help and surfaces disagreements between its cited fragments | Built-in disagreement retrieval/renderer fixture |
| FR-032 | Skill/evidence schemas and adapter hard-reject external `source_kind` | Schema/import/retrieval negative tests |
| FR-033 | Answer claims require evidence/citation IDs | Ungrounded draft rejection |
| FR-034 | Versioned shipped package and active catalog | Catalog completeness gate |
| FR-035 | Atomic operation + typed parameters | Skill semantic lint |
| FR-036 | Portable URI/value lint and secret scan | AC-005 scan |
| FR-037 | Closed skill schema | Draft 2020-12 + semantic validation |
| FR-038 | Single skill export from canonical document | Web/CLI export test |
| FR-039 | Shared import use case, no app build | Two-data-dir portability |
| FR-040 | New immutable revision + atomic snapshot swap | Next-turn hot reload test |
| FR-041 | Schema/digest/compatibility/dependency/conflict pipeline | Import negative suite |
| FR-042 | Structured validation errors with JSON pointer | API/CLI error golden tests |
| FR-043 | Public skill card projection | Browser catalog test |
| FR-044 | Replace/delete with `If-Match`, next revision | Concurrent catalog tests |
| FR-045 | Computed capability/skill use persisted per trace | Diagnostic inspection |
| FR-046 | Chat-first Jinja layout, sticky composer | Responsive browser test |
| FR-047 | Send button/Enter, Shift+Enter newline | Browser keyboard test |
| FR-048 | SQLite sessions/messages | Reload/restart test |
| FR-049 | Versioned skill admin routes | Web import/export/view tests |
| FR-050 | Separate dependency diagnostics | Failure injection Q105/Q106 |
| FR-051 | Trace events, calls, raw payload refs, evidence | Offline replay test |
| FR-052 | Central redaction + canary export gate | Secret canary test |
| FR-053 | Deterministic ZIP bundle | Manifest/checksum test |
| FR-054 | Version and dependency badges | Browser health test |
| FR-055 | Public error code/message + trace ID | Dependency/skill failure tests |

## 2. Нефункциональные требования

| ID | Архитектурный механизм | Проверка |
| --- | --- | --- |
| NFR-001 | Russian display contracts and UI text | Corpus/browser locale scan |
| NFR-002 | Central formatter over typed facts/units/time | Golden formatting tests |
| NFR-003 | Fixed templates, deterministic operators, pinned state | Repeat corpus at same marker |
| NFR-004 | Per-turn error state and short transactions | Failure injection then next turn |
| NFR-005 | Ordered trace + raw payload + snapshots | Offline diagnostic replay |
| NFR-006 | Package schema/semantic/lint/fixture validation | Catalog CI gate |
| NFR-007 | Tests embedded per skill + composition suite | Completeness audit |
| NFR-008 | Cross-platform paths/SQLite/wheel | macOS acceptance + Windows smoke |
| NFR-009 | Catalog DB revision and atomic reference swap | Hot reload test |
| NFR-010 | Stage budgets, SSE, 30/90-second SLO metrics | Timed corpus run |
| NFR-011 | Explicit timeout outcomes and public dependency name | Q105/Q106 |
| NFR-012 | Versioned repo/lock/migrations/package manifests | Release checklist |
| NFR-013 | New module/schema design, no v5 import | Source/provenance scan |
| NFR-014 | Env-only key, redaction, localhost default | Secret/bind/config tests |

## 3. Capability IDs на устойчивые механизмы

Product capability ID остается requirement/test label. В колонке ниже указан
механизм реализации, а не обязательное число JSON skills.

| Семейство | Capability IDs | Реализация |
| --- | --- | --- |
| Dialogue | `CAP-CHAT-CONTEXT`, `CAP-CHAT-CLARIFY`, `CAP-CHAT-NOT-FOUND`, `CAP-CHAT-OUT-OF-SCOPE`, `CAP-CHAT-READ-ONLY`, `CAP-CHAT-DEPENDENCY-ERROR` | Context ledger, planner decisions, outcome renderer; не query skills |
| Common | `CAP-COMMON-PERIOD`, `CAP-COMMON-COUNT`, `CAP-COMMON-AGGREGATE`, `CAP-COMMON-RANK`, `CAP-COMMON-GROUP` | Deterministic operators |
| Common entity/detail | `CAP-COMMON-ENTITY`, `CAP-COMMON-DETAIL` | Typed resolver/detail data skills + `EntityRef` |
| Documentation | `CAP-DOC-SEARCH`, `CAP-DOC-TERM`, `CAP-DOC-PROCEDURE`, `CAP-DOC-ERROR`, `CAP-DOC-STATUS`, `CAP-DOC-SOURCE` | One help-index engine, declarative retrieval skills by chunk role |
| Reference | `CAP-REF-ITEM-FIND`, `CAP-REF-ITEM-DETAILS`, `CAP-REF-ITEM-GROUP`, `CAP-REF-PARTNER-FIND`, `CAP-REF-PARTNER-DETAILS`, `CAP-REF-WAREHOUSE-FIND`, `CAP-REF-CASH-DESK-FIND`, `CAP-REF-PRICE-TYPE-FIND` | Parameterized resolver/detail data skills |
| Sales documents | `CAP-SALES-ORDER-LIST`, `CAP-SALES-ORDER-HEADER`, `CAP-SALES-ORDER-LINES`, `CAP-SALES-ORDER-STATUS`, `CAP-SALES-SHIPMENT-LIST`, `CAP-SALES-SHIPMENT-LINES` | List/header/lines/status contracts with exact refs |
| Sales measures | `CAP-SALES-TURNOVER`, `CAP-SALES-PROFIT`, `CAP-SALES-RETURN`, `CAP-SALES-AVERAGE` | Fact query skills + generic operators |
| Purchase documents | `CAP-PURCHASE-ORDER-LIST`, `CAP-PURCHASE-ORDER-STATUS`, `CAP-PURCHASE-RECEIPT-LIST`, `CAP-PURCHASE-RECEIPT-HEADER`, `CAP-PURCHASE-RECEIPT-LINES`, `CAP-PURCHASE-RETURN` | Typed document skills |
| Purchase measures | `CAP-PURCHASE-TURNOVER`, `CAP-PURCHASE-EXPECTED`, `CAP-PURCHASE-SUPPLIER-RANK` | Fact query skills + rank/group |
| Prices | `CAP-PRICE-CURRENT`, `CAP-PRICE-HISTORY`, `CAP-PRICE-LAST-PURCHASE`, `CAP-PRICE-VAT`, `CAP-PRICE-COMPARE`, `CAP-PRICE-MISSING` | Price evidence skills; VAT/compare via deterministic calculation/unit guards |
| Stock | `CAP-STOCK-BALANCE`, `CAP-STOCK-AVAILABLE`, `CAP-STOCK-RESERVED`, `CAP-STOCK-BY-WAREHOUSE`, `CAP-STOCK-BY-ITEM`, `CAP-STOCK-MOVEMENT`, `CAP-STOCK-CONSUMPTION`, `CAP-STOCK-DEFICIT`, `CAP-STOCK-RANK`, `CAP-STOCK-EXPECTED` | Stock fact skills + dimensions/filter/rank; characteristic/series/purpose typed params |
| Logistics | `CAP-MOVE-LIST`, `CAP-MOVE-LINES`, `CAP-MOVE-STATUS`, `CAP-MOVE-DIRECTION`, `CAP-INVENTORY-RESULT`, `CAP-INTERNAL-CONSUMPTION`, `CAP-DELIVERY-STATUS`, `CAP-DELIVERY-DATE` | Document/register fact skills with document refs |
| Settlements | `CAP-SETTLEMENT-AR`, `CAP-SETTLEMENT-AP`, `CAP-SETTLEMENT-DETAIL`, `CAP-SETTLEMENT-OVERDUE`, `CAP-SETTLEMENT-RANK`, `CAP-SETTLEMENT-BY-DOCUMENT`, `CAP-CUSTOMER-SALES-HISTORY`, `CAP-CUSTOMER-NO-ACTIVITY` | Settlement fact skills + group/rank/join/filter |
| Cash | `CAP-CASH-BALANCE`, `CAP-CASH-BANK-BALANCE`, `CAP-CASH-RECEIPTS`, `CAP-CASH-EXPENSES`, `CAP-CASH-FLOW` | Cash fact skills + aggregate/group/calculate |
| Finance | `CAP-FIN-REVENUE`, `CAP-FIN-COST`, `CAP-FIN-PROFIT`, `CAP-FIN-TREND` | Comparable measure skills + join/subtract/group |

Все 87 IDs представлены в таблице. Каталог completeness audit проверяет, что
каждый ID покрыт active skill/mechanism manifest либо явно исключен решением.

## 4. Acceptance criteria на gates

| ID | Проверяемый gate |
| --- | --- |
| AC-001 | Completeness manifest покрывает все 87 IDs или явные exclusions |
| AC-002 | Public skill card содержит purpose/input/output/compatibility/limits/examples |
| AC-003 | Semantic lint требует positive и negative test каждого active skill |
| AC-004 | Composition manifest требует test для участвующих capabilities |
| AC-005 | Secret/local path/session/concrete-value scanner блокирует package |
| AC-006 | Explicit replace/delete создает новую revision без app update |
| AC-007 | Single-skill web и CLI export валидируется `skill.schema.json` |
| AC-008 | Import в чистый `APP_DATA_DIR` с compatible profile |
| AC-009 | Следующий turn pin-ит новую revision без restart |
| AC-010 | До/после переноса сравниваются normalized facts/contracts |
| AC-011 | Parse/schema/checksum failure оставляет revision неизменной |
| AC-012 | Compatibility errors имеют codes и JSON pointers |
| AC-013 | Same ID/version/different digest и implicit upgrade запрещены |
| AC-014 | Corpus runner сохраняет outcome/evidence по всем 116 IDs |
| AC-015 | Acceptance calculator требует не менее 81 правильного ответа из Q001-Q090 |
| AC-016 | Context acceptance требует не менее 10 правильных из 11 follow-up сценариев |
| AC-017 | Negative outcome assertions покрывают Q098-Q106 |
| AC-018 | State machine запрещает преобразовать error в `success_empty` |
| AC-019 | Final coverage запрещает intermediate fact как полный ответ |
| AC-020 | Missing requirement блокирует factual renderer/LLM claim |
| AC-021 | Fact requirements проверяют metric/object/time/unit |
| AC-022 | Decimal money сравнивается по currency minor unit, не float |
| AC-023 | Integer/decimal quantity и distinct document counts сравниваются точно |
| AC-024 | Canonical row sets и rank order/direction/measure сравниваются отдельно |
| AC-025 | Semantic count types различают units/documents/rows/distinct entities |
| AC-026 | Evidence требует moment для balance и half-open period для turnover |
| AC-027 | Money unit resolved либо explicit unresolved reason |
| AC-028 | Каждый documentation evidence bundle содержит citation |
| AC-029 | Schema v1 hard-filters built-in help 11.5.27.56 |
| AC-030 | Invalid skill/evidence и direct retrieval с внешним `source_kind` отклоняются до чтения index |
| AC-031 | Fixture с двумя расходящимися built-in chunks создает typed disagreement; UI показывает обе позиции и citations |
| AC-032 | Ungrounded procedure/claim rejected, renderer использует cited chunks |
| AC-033 | Responsive Playwright test: composer доступен при длинной истории |
| AC-034 | Playwright test Enter/Shift+Enter и send button |
| AC-035 | Restart/reload test SQLite session/messages/context |
| AC-036 | Browser проверяет sent/received timestamps |
| AC-037 | SSE slow-call test показывает stage progress |
| AC-038 | UI status badges используют version/dependency diagnostics DTO |
| AC-039 | Skill list/detail browser test проверяет public projection |
| AC-040 | Admin actions показывают accepted/rejected/conflict и revision |
| AC-041 | UUID request/trace создаются при приеме до внешних calls |
| AC-042 | Trace audit проверяет question/context/capabilities/params/MCP/raw/final/errors |
| AC-043 | Offline replay восстанавливает normalizer/coverage/renderer |
| AC-044 | ZIP export manifest/checksums тестируется на один trace |
| AC-045 | Secret canary блокирует log/UI/bundle leakage |
| AC-046 | Dependency error renderer показывает DeepSeek/MCP и trace ID |
| AC-047 | Timed basic scenario gate p100 <= 30 s в контрольных условиях |
| AC-048 | Timed supported corpus gate p95 <= 90 s |
| AC-049 | Deadline exhaustion завершает turn и следующий turn выполняется |
| AC-050 | Skill/contract failure не меняет session/catalog revision |
| AC-051 | Same marker повторяет normalized facts, text variation ignored |
| AC-052 | Clean macOS wheel/venv/startup procedure smoke |
| AC-053 | Windows startup/DeepSeek/MCP/chat/log/portability smoke |
| AC-054 | Release checklist проверяет private repo artifacts/history |
| AC-055 | Git history проверяется руководителем при публикации, не runtime |
| AC-056 | Source/provenance scan запрещает imports/copies архитектуры v5 |
| AC-057 | Release manifest связывает app/package/schemas/corpus/checksums/report |
| AC-058 | Q107 проверяет назначение, только фактические возможности и read-only mode |
| AC-059 | Coverage audit на Q001-Q116 требует упоминания и end-to-end результата всех 87 capability IDs |

## 5. 116 сценариев на механизмы

Полная поштучная проверка находится в `self_review.md`. Групповая связь:

| Сценарии | Основной механизм |
| --- | --- |
| Q001-Q010 | Help retrieval roles, citations, grounded renderer |
| Q011-Q020 | Typed entity resolution/detail/list/count |
| Q021-Q030 | Entity/price facts, unit/date, compare/calculate, clarification |
| Q031-Q040 | Period, document list, count/aggregate/rank/group, finance facts |
| Q041-Q050 | Receipt/order refs, lines, expected/return/turnover, rank |
| Q051-Q060 | Stock facts/dimensions/moment, clarify/filter/rank/movement |
| Q061-Q070 | Movement/inventory/delivery refs, status/date and period |
| Q071-Q080 | Settlement measure versus document sum, rank/detail/join/no-activity |
| Q081-Q090 | Cash/finance units, period/group/join/calculate/rank |
| Q091-Q097 | Context retention/replacement and exact `_objectRef` |
| Q098-Q104 | Clarify, scoped refusal, read-only, confirmed empty |
| Q105-Q106 | Separate MCP/DeepSeek unavailable outcomes and session survival |
| Q107 | Deterministic product-meta response from active catalog summary |
| Q108-Q116 | End-to-end paths для ранее неиспользованных девяти capability IDs |

## 6. Принятые уточнения трассируемости

1. `Q107` закреплен за `FR-010`.
2. `Q108-Q116` закреплены за девятью capability IDs, ранее не имевшими прямого
   end-to-end сценария:
   `CAP-COMMON-DETAIL`, `CAP-CUSTOMER-SALES-HISTORY`,
   `CAP-PURCHASE-SUPPLIER-RANK`, `CAP-SALES-ORDER-HEADER`, `CAP-SALES-PROFIT`,
   `CAP-SALES-RETURN`, `CAP-SALES-SHIPMENT-LINES`, `CAP-STOCK-CONSUMPTION`,
   `CAP-STOCK-EXPECTED`.
3. `FR-031/032` и `AC-030/031` являются активными v1 gates: только встроенная
   справка, hard-reject внешнего `source_kind`, typed presentation расхождений
   между встроенными фрагментами с citation каждой позиции.
4. Принят marker `acceptance_observable_state`: контрольные проекции/агрегаты
   `Q001-Q116` плюс configuration/profile/catalog/docs revisions. Его ограничение
   описано в PO; token MCP и полный snapshot не входят в MVP.
