# Release gates ChatBot 1C v7

## 1. Правило решения

Release разрешен только при статусе `pass` у всех обязательных gates. `blocked`,
`not_run`, `waived` без решения руководителя и synthetic-only evidence не
считаются прохождением.

## 2. Матрица gates

| Gate | Покрытие | Требование к доказательству | Текущий статус |
| --- | --- | --- | --- |
| RG-00 Contract integrity | schemas, requirements counts | Standalone suite: 116 Q, 55 FR, 14 NFR, 59 AC, 87 capabilities; valid/invalid fixtures | `pass` |
| RG-00A ADR-0003 static contracts | query/package/evidence/loader contracts | RFC8785 chains; single/linked graph; typed literals; P1/P2 probes; stable semantic codes | `pass` |
| RG-01 Catalog completeness | AC-001..006 | Все 87 baseline IDs реализованы без исключений; positive/negative и composition tests; portable lint | `not_run` |
| RG-02 Package portability | AC-007..013 | Export/import во второй clean data dir, next-turn hot reload, explicit conflict rejection | `not_run` |
| RG-03 Corpus facts | AC-014..027 | Все 116 outcomes; Q001-Q090 >=81 correct; follow-up >=10/11; negatives 9/9 | `blocked_external` |
| RG-04 Documentation | AC-028..032 | Только built-in help 11.5.27.56, citations, external hard reject, disagreement surfaces both positions | `not_run` |
| RG-05 Browser | AC-033..040 | Chat-first responsive UI, keyboard, reload, timestamps, progress, health, catalog operations | `not_run` |
| RG-06 Diagnostics/security | AC-041..046 | Unique trace, complete replay data, ZIP checksums, secret canary clean, dependency-specific errors | `not_run` |
| RG-07 Performance/resilience | AC-047..051 | p100 <=30 s для `Q001,Q011,Q031,Q041,Q051,Q062,Q071,Q081,Q102,Q107`; p95 <=90 s для `Q001-Q116`; timeout recovery; same-marker facts stable | `blocked_external` |
| RG-08 macOS delivery | AC-052, AC-054..057 | Clean install/start, release manifest, checksums, no v5 code/provenance | `not_run` |
| RG-09 Windows smoke | AC-053 | Same artifact: startup, FTS5, DeepSeek, MCP, chat, logs, portability | `not_run` |
| RG-10 Direct product/capability | AC-058..059 | Q107 pass; every capability has corpus mention and end-to-end result | `blocked_external` |
| RG-11 Linked batch compatibility | ADR-0003 | Live profile has `supports_linked_temp_batch=true`; producer and final consumer execute in one MCP request and only final projection is returned | `blocked_external` |

`RG-00` и `RG-00A` означают готовность независимого pre-implementation test
harness, а не готовность production validator или продукта. Production red
baseline по новым probes не понижает эти два статуса, но блокирует соответствующий
product gate до реализации. Остальные gates нельзя наследовать из них.

DeepSeek JSON-mode prerequisite имеет статус `pass`: PM получил HTTP 200 и
валидный JSON при `response_format={"type":"json_object"}`. Evidence:
[source inventory](../source_inventory.md#deepseek). Секреты в evidence не
записаны. Этот smoke не заменяет planner schema, corpus или latency gates.

## 3. Hard blockers

Выпуск блокируется немедленно, если наблюдается хотя бы одно условие:

- ассистент отправил write/BSL/non-allowlisted MCP call;
- fabricated fact или instruction без evidence/citation;
- `query_error`, malformed envelope или unavailable dependency показаны как
  отсутствие данных;
- zero aggregate показан как empty либо промежуточный result как полный;
- неверные entity identity, measure, period/moment, unit или currency;
- package import частично изменил catalog либо текущий turn сменил snapshot;
- секрет, auth header или абсолютный локальный путь попал в Git/UI/log/bundle;
- использован внешний documentation source;
- portability, Q107, capability completeness или Windows smoke не пройдены;
- marker отсутствует/изменился, а прежние numeric/list baselines используются.
- linked-batch skill активирован без подтвержденного
  `supports_linked_temp_batch=true` либо batch разбит на несколько MCP calls.

## 4. Метрики приемки

| Метрика | Порог |
| --- | --- |
| Corpus result records | 116/116 |
| Correct first attempt Q001-Q090 | >=81/90 |
| Correct follow-up | >=10/11 |
| Negative expected outcomes | 9/9 |
| Capability coverage | 87/87 с end-to-end result |
| Documentation citation | 100% documentation answers |
| Fabricated facts | 0 |
| Write operations | 0 |
| Basic latency | p100 <=30 s для `Q001`, `Q011`, `Q031`, `Q041`, `Q051`, `Q062`, `Q071`, `Q081`, `Q102`, `Q107` |
| Supported latency | p95 <=90 s для `Q001-Q116` |
| Secret/local-path leaks | 0 |

## 5. Gate evidence package

Итоговый отчет связывает commit/tag, app version, package digest, schema
version, corpus version, marker, help index digest, macOS/Windows environment,
test command, result files и известные defects. Отсутствующий artifact делает
соответствующий gate `not_run`, даже если ручная демонстрация выглядела успешно.

## 6. Закрытые дефекты контракта

| ID | Статус | Решение requirements | Regression evidence |
| --- | --- | --- | --- |
| CD-001 | `closed` | `AC-047` фиксирует exact basic set: `Q001`, `Q011`, `Q031`, `Q041`, `Q051`, `Q062`, `Q071`, `Q081`, `Q102`, `Q107`; `AC-048` применяется к `Q001-Q116` | `test_performance_scenario_sets_match_acceptance_requirements` |
| CD-002 | `closed` | Текущий baseline равен 87/87; исключение во время приемки запрещено и требует отдельной версии requirements/corpus | `test_catalog_definitions_and_corpus_capability_references_match` |

Закрытие устраняет неоднозначность контракта, но не меняет текущий статус RG-07:
фактические latency measurements остаются `blocked_external` до запуска MCP.
