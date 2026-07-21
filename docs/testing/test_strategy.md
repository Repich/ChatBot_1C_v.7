# Стратегия тестирования ChatBot 1C v7

## 1. Назначение и границы

Стратегия независимо выводится из `docs/requirements`, `docs/architecture`,
`docs/adr`, четырех JSON Schema и корпуса `Q001-Q116`. До появления production
package исполняемый contract harness импортирует только стандартную библиотеку
и явно переданные test dependencies.

Тестировщик не изменяет требования, архитектуру и schemas. Несогласованность
контракта регистрируется как дефект и не компенсируется ослаблением oracle или
частным ожиданием под один вопрос.

Текущий статус live-проверок: **`blocked_external`**. MCP не запущен, а adapter
profile `supports_linked_temp_batch=true` не подтвержден. Поэтому числовые и
списочные baselines, marker контрольной базы, linked-batch smoke и live
acceptance не сформированы и не считаются пройденными.

DeepSeek JSON-mode smoke имеет статус **`passed`**: PM подтвердил HTTP 200,
валидный JSON и echo `request_id` для
`response_format={"type":"json_object"}`. Evidence:
[source inventory](../source_inventory.md#deepseek). Значение ключа не
записывалось. Полный planner schema остается отдельным contract gate.

## 2. Принципы

1. Факты проверяются независимо от текста ответа модели.
2. JSON Schema и semantic validation являются разными gates.
3. `success_empty`, `zero_aggregate`, `query_error`, `partial` и dependency
   errors проверяются отдельными сценариями.
4. Полный ответ допускается только при достаточном evidence; промежуточный факт
   не заменяет требуемый результат.
5. Control queries принадлежат независимому тестовому контуру. Запрещено
   копировать, импортировать, параметризовать или вызывать query templates из
   поставляемых skills.
6. Значения live oracle фиксируются только вместе с
   `acceptance_observable_state` marker. При изменении любого component digest
   baseline устаревает.
7. Fixture values явно синтетические. Реальные секреты, абсолютные локальные
   пути и значения демонстрационной базы в fixtures запрещены.
8. Required/optional criticality переносится из planner requirement в evidence
   без потерь. Missing optional requirement обязан быть виден, но не влияет на
   `coverage.sufficient`.
9. Page count, MCP envelope row count и business total являются разными
   semantics; total проверяется только по complete-set/aggregate evidence.
10. Frozen Evidence 1.0 проверяется без изменения fixtures; legacy omissions
    получают defaults только в 1.0 reader. Все новые outputs имеют strict 1.1.

## 3. Уровни испытаний

| Уровень | Объект | Основной результат | Когда запускается |
| --- | --- | --- | --- |
| Unit/schema | Draft 2020-12 schemas, relative `$ref`, closed objects | Валидные документы приняты, schema-invalid отклонены | Каждый change |
| Semantic | ADR-0003 query graph/literals, bindings, required facts, refs, coverage, exact provides/output и package lock | Schema-valid нарушение получает стабильный semantic error | Каждый change после реализации validator |
| MCP contract | request DTO, allowlist, envelope normalization, outcome | Приняты только две документированные wrapper-формы | Каждый adapter change |
| DeepSeek contract | JSON extraction, schema, repair boundary, forbidden fields | Текст модели не расширяет полномочия planner | Каждый planner change |
| Integration live UT | metadata assertions, fixed templates и linked temp batch на целевой базе | Один read-only MCP request возвращает только final projection; profile подтверждает `supports_linked_temp_batch=true` | При доступном MCP |
| Corpus | `Q001-Q116`, context, composition, negatives | Outcome и normalized evidence сверены с oracle | Relevant group / full acceptance |
| Browser | chat, keyboard, SSE, reload, catalog admin, health | Наблюдаемое поведение AC-033..040 | Каждый UI slice |
| Portability | web/CLI export-import между двумя чистыми data dirs | Совпадают revisions, skill IDs и digests | Каждый catalog change |
| Performance | p100 для exact basic set и p95 для полного supported corpus | AC-047: `Q001,Q011,Q031,Q041,Q051,Q062,Q071,Q081,Q102,Q107`; AC-048: `Q001-Q116` | Stabilization |
| Failure injection | timeout, transport, malformed response, crash points | Ошибка локализована, session/catalog сохранены | Каждый resilience change |
| Windows | startup, SQLite/FTS5, DeepSeek, MCP, chat, diagnostics, package | Тот же release и package проходят smoke | После macOS acceptance |

### 3.1. Независимая модель ADR-0003

Standalone oracle не импортирует production package. Он проверяет:

1. `single_select` и `linked_temp_batch` с последовательным и разветвленным
   графом временных таблиц, единственным final statement и одним trailing `;`;
2. comments/strings как lexer regions, не как ложные statement, DML/DDL или
   `ПОМЕСТИТЬ` tokens;
3. create-before-read, unique producer, отсутствие self/forward/orphan edges и
   транзитивный путь каждого producer к final;
4. package-scoped параметры, aliases только финальной проекции и exact typed
   declarations всех семи видов `invariant_constants`;
5. запрет бизнес-литералов, включая article, GUID, document number и date, а
   также обходы через `IN`, `BETWEEN` и `CASE`;
6. typed FactRequirement coverage, exact `provides/output`, closed package lock,
   catalog digest conflict и grounded disagreement positions;
7. pre-schema ceilings: bytes, depth 32, nodes 500000, array 100000 и отдельный
   canonical embedded-skill limit 1 MiB.

Отдельный semantic набор строит required/all reverse closures, отвергает unused
steps и затем проверяет exact one-to-one перенос `requirement_id/required` в
evidence. Cross-artifact oracle независимо пересчитывает
`sufficient=all(covered && collection_complete for required=true)` из pinned
plan, CoverageProof, catalog и evidence. Missing/incomplete optional row должен
оставаться в coverage и не менять true. Covered facts required
`partial_until_all_pages` page сохраняются, но sufficient остается false.
Terminal keyset continuation page с `has_more=false` по-прежнему имеет
`collection_scope=visible_page`: без materialized previous pages она
insufficient для required `complete_set`. Один EvidenceBundle без plan/proof не
используется для проверки criticality drift.

Version matrix отдельно доказывает: frozen 1.0 без обоих новых fields проходит
и нормализуется только in-memory; 1.1 с каждым omission отклоняется; новый
runtime output равен 1.1 и содержит оба fields; unknown version отклоняется;
legacy payload/digest не переписывается.

Pagination-набор проверяет proof, а не только JSON shape. Любой unbounded
producer обязан пройти keyset tests с duplicate sort values, 1001+ rows и
terminal continuation без duplicate/skip. Proved-prefix отдельно проходит
`M-1`, exact `M` и `M+1`; exact M complete, M+1 дает
`RESULT_PREFIX_BOUND_EXCEEDED`. Query/MCP limit не принимается как bound proof.
Generic import mutations duplicate/reorder sort or cursor IDs, collide
case-insensitive parameters, change encoding, make a coordinate nullable, drop
an identity suffix component, reverse ORDER BY and alter strict comparator.
Dedicated AST validation must reject each before activation; built-in-only
package assertions are not accepted as coverage of transferable JSON.
Separate mutation assigns `aggregate|exactly_one|zero_or_one` to a paged skill
and expects `PAGINATION_CARDINALITY_MISMATCH`. The positive zero-aggregate
fixture is a real `strategy=none`, one-row complete-set producer, not a
cardinality-only mutation of a keyset list.

Schema-valid semantic-negative fixtures всегда имеют
`expected_semantic_error`; от Draft 2020-12 не ожидаются проверки query text,
междокументных связей или bounded JSON loader.

## 4. Oracle model

Для каждого вопроса в `test_case_catalog.md` указан минимум один из методов:

- `exact_scalar`: точное число, дата, статус или денежная сумма с unit/currency;
- `exact_set`: канонический набор строк независимо от порядка отображения;
- `exact_order`: канонический набор плюс порядок, направление и ключ sort;
- `semantic`: entity identity, measure, period/moment, unit, filters и outcome;
- `citation`: built-in source, release, URI, chunk и grounding;
- `clarification`: один конкретный вопрос и допустимые варианты;
- `refusal`: правильная граница `read_only_request` или `out_of_scope`;
- `dependency_error`: точная dependency, public outcome и trace ID.

Текстовое сходство не является oracle. Для денег сравнение идет по минимальной
единице валюты, для quantity/document counts расхождение не допускается, для
rank отдельно проверяются состав, порядок, направление и measure. Для count
отдельно проверяются distinct identity и collection scope: `visible_page` может
дать только `Показано N`, `complete_set`/aggregate - `Всего N`.

## 5. Независимые data baselines

Skeleton находится в
`tests/oracles/acceptance_observable_state.yaml`. Он покрывает все сценарии с
типом `data`/`follow_up` и отрицательные confirmed-empty `Q102/Q103`, но пока не
содержит ожидаемых значений.

Для каждого control query обязательно:

1. Вывести бизнес-смысл из requirements и corpus, а физический источник
   подтвердить независимо по metadata/configuration.
2. Не читать query text поставляемого skill при создании контрольной выборки.
3. Использовать независимые aliases, joins, filters и aggregation path.
4. Зафиксировать query digest, canonical result digest и reviewer.
5. Сопоставлять normalized facts, не raw MCP order и не prose DeepSeek.
6. Повторно сформировать baseline при изменении marker.

## 6. Быстрый и полный контуры

Текущий standalone-контур:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-project --python 3.12 \
  --with pytest==8.4.1 \
  --with PyYAML==6.0.2 \
  --with jsonschema==4.25.1 \
  --with rfc8785==0.1.4 \
  pytest -q -p no:cacheprovider tests/requirements tests/contract
```

Команда не устанавливает production package и не изменяет `pyproject.toml` или
dependency files.

После реализации быстрый контур дополняется unit/semantic/adapter tests.
Полный контур добавляет live UT, весь corpus, browser, portability, performance,
failure injection и, после macOS acceptance, Windows smoke.

Performance-наборы не выбираются во время прогона. Basic p100 <=30 секунд
измеряется ровно на `Q001`, `Q011`, `Q031`, `Q041`, `Q051`, `Q062`, `Q071`,
`Q081`, `Q102`, `Q107`; supported p95 <=90 секунд рассчитывается по
`Q001-Q116`.

Q031 входит в performance run только как полный corpus scenario с SP04 list и
SP06 total. Slice-2 `Q031.list` subcase не является заменой этой метрики.

## 7. Повторяемость и отчетность

- `Q001-Q116` выполняются один раз для first-attempt метрик; сценарии с LLM
  interpretation дополнительно повторяются не менее трех раз для stability.
- Фактический результат сравнивается только при совпавшем marker.
- Для каждого run сохраняются app/package/schema/corpus/index versions,
  dependency health, timings, outcomes, evidence digests и trace IDs.
- Любой пропущенный сценарий имеет статус `blocked`, `not_run` или `not_applicable`
  с причиной; он не считается pass.
- Live acceptance запрещено выводить из synthetic fixtures.

## 8. Defect policy

Release blocker создается при записи в 1С, fabricated fact, смешении error и
empty, неправильном entity/period/unit, утечке секрета, использовании внешней
документации, неполном evidence как полном ответе или нарушении atomic catalog
revision. Contract defect передается руководителю до реализации общего
механизма; тест фиксирует наблюдаемое противоречие, но не выбирает решение.
