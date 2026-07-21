# Стратегия тестирования ChatBot 1C v7

## 1. Назначение и границы

Стратегия независимо выводится из `docs/requirements`, `docs/architecture`,
`docs/adr`, четырех JSON Schema и корпуса `Q001-Q116`. До появления production
package исполняемый contract harness импортирует только стандартную библиотеку
и явно переданные test dependencies.

Тестировщик не изменяет требования, архитектуру и schemas. Несогласованность
контракта регистрируется как дефект и не компенсируется ослаблением oracle или
частным ожиданием под один вопрос.

Текущий статус live-проверок: **`blocked_external`**. MCP не запущен, поэтому
числовые и списочные baselines, marker контрольной базы и live acceptance не
сформированы и не считаются пройденными.

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

## 3. Уровни испытаний

| Уровень | Объект | Основной результат | Когда запускается |
| --- | --- | --- | --- |
| Unit/schema | Draft 2020-12 schemas, relative `$ref`, closed objects | Валидные документы приняты, schema-invalid отклонены | Каждый change |
| Semantic | bindings, required facts, refs, coverage, zero/empty, portable lint | Schema-valid нарушение получает стабильный semantic error | Каждый change после реализации validator |
| MCP contract | request DTO, allowlist, envelope normalization, outcome | Приняты только две документированные wrapper-формы | Каждый adapter change |
| DeepSeek contract | JSON extraction, schema, repair boundary, forbidden fields | Текст модели не расширяет полномочия planner | Каждый planner change |
| Integration live UT | metadata assertions и fixed templates на целевой базе | Запрос выполняется read-only и возвращает заявленные facts | При доступном MCP |
| Corpus | `Q001-Q116`, context, composition, negatives | Outcome и normalized evidence сверены с oracle | Relevant group / full acceptance |
| Browser | chat, keyboard, SSE, reload, catalog admin, health | Наблюдаемое поведение AC-033..040 | Каждый UI slice |
| Portability | web/CLI export-import между двумя чистыми data dirs | Совпадают revisions, skill IDs и digests | Каждый catalog change |
| Performance | p100 для exact basic set и p95 для полного supported corpus | AC-047: `Q001,Q011,Q031,Q041,Q051,Q062,Q071,Q081,Q102,Q107`; AC-048: `Q001-Q116` | Stabilization |
| Failure injection | timeout, transport, malformed response, crash points | Ошибка локализована, session/catalog сохранены | Каждый resilience change |
| Windows | startup, SQLite/FTS5, DeepSeek, MCP, chat, diagnostics, package | Тот же release и package проходят smoke | После macOS acceptance |

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
rank отдельно проверяются состав, порядок, направление и measure.

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
  pytest -q tests/requirements tests/contract
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
