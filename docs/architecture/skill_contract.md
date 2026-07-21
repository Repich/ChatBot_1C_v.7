# Контракт переносимого навыка и пакета

Нормативные schemas:

- `schemas/skill.schema.json` - один atomic skill;
- `schemas/skill-package.schema.json` - атомарно импортируемый набор skills;
- `schemas/planner-output.schema.json` - план, в котором skill может появиться;
- `schemas/evidence.schema.json` - нормализованный результат выполнения.

Версия schema `1.0.0` предназначена только для `data_query` и
`documentation_retrieval`. Это два закрытых варианта `oneOf`, а не один JSON с
произвольным `config`.

## 1. Семантика верхнего уровня

| Поле | Назначение |
| --- | --- |
| `skill_id`, `version` | Стабильная identity и SemVer реализации |
| `display` | Русское имя, назначение и ограничения для UI/shortlist |
| `provides` | Product capability IDs и semantic fact types |
| `compatibility` | Configuration ID/name, release range, compatibility modes, metadata assertions |
| `selection` | Intents, aliases, anti-examples и required context fact types |
| `parameters` | Закрытый список typed inputs; конкретные значения не входят в identity |
| `operation` | Ровно один data query или documentation retrieval contract |
| `output_contract` | Business semantics, cardinality, facts, units, sufficiency и renderer |
| `dependencies` | Runtime contracts и skill dependencies для composition availability |
| `examples` | Подходящие и неподходящие русские вопросы |
| `tests` | Положительные/отрицательные portable fixtures |
| `provenance` | Автор, источник metadata/help/test evidence, без local paths |
| `integrity` | SHA-256 по RFC 8785 canonical JSON без top-level `integrity` |

`skill_id` не содержит товар, склад, город, период, формулировку или instance ID.
Например, `ut.stock.balance-by-item` допустим, а
`ut.stock.balance-kurtka-central-warehouse` отклоняется lint rule.

## 2. Типизированные параметры

Общие value types: string/normalized text, boolean, integer, decimal, date,
datetime, period, enum, entity ref/list и pagination. Entity parameter содержит
`semantic_type` и допустимые `entity_types`.

Характеристика, серия и назначение запасов выражаются тем же механизмом:

```json
[
  {
    "name": "item_characteristic",
    "title_ru": "Характеристика номенклатуры",
    "description_ru": "Подтвержденная характеристика выбранного товара",
    "value_type": "entity_ref",
    "required": false,
    "allowed_sources": ["session_context", "previous_step"],
    "semantic_type": "catalog.item_characteristic",
    "entity_types": ["catalog.item_characteristic"],
    "normalization": "object_ref"
  },
  {
    "name": "item_series",
    "title_ru": "Серия номенклатуры",
    "description_ru": "Подтвержденная серия выбранного товара",
    "value_type": "entity_ref",
    "required": false,
    "allowed_sources": ["session_context", "previous_step"],
    "semantic_type": "catalog.item_series",
    "entity_types": ["catalog.item_series"],
    "normalization": "object_ref"
  },
  {
    "name": "inventory_purpose",
    "title_ru": "Назначение запасов",
    "description_ru": "Подтвержденное назначение партии или запаса",
    "value_type": "entity_ref",
    "required": false,
    "allowed_sources": ["session_context", "previous_step"],
    "semantic_type": "inventory.purpose",
    "entity_types": ["inventory.purpose"],
    "normalization": "object_ref"
  }
]
```

Это не новые intent classes. Если skill не использует такой разрез, parameters
его не содержат. Если использует, PlanValidator требует подходящий resolver fact
до вызова.

## 3. Data-query skill

Data skill содержит одну atomic operation и один immutable query package в
`operation.query_template.text`. Package исполняется одним `execute_query` MCP-вызовом и
имеет ровно одну внешнюю результирующую проекцию. Он может быть одним SELECT или
связанным пакетом SELECT с временными таблицами. `parameter_bindings` -
единственный путь значений бизнес-экземпляров в MCP; runtime string
interpolation запрещен.

Сокращенный пример операции:

```json
{
  "kind": "data_query",
  "tool": "execute_query",
  "read_only": true,
  "query_template": {
    "template_id": "ut.stock.balance-by-item.v1",
    "language": "1c-query",
    "text": "ВЫБРАТЬ Остатки.Номенклатура КАК Номенклатура, Остатки.Склад КАК Склад, Остатки.КоличествоОстаток КАК Количество ИЗ РегистрНакопления.<verified>.Остатки(&Момент, Номенклатура = &Номенклатура) КАК Остатки",
    "execution": {
      "kind": "single_select",
      "statement_count": 1,
      "final_statement": 1
    },
    "invariant_constants": [],
    "include_schema": true,
    "mcp_limit": {"default": 21, "maximum": 1000}
  },
  "parameter_bindings": [
    {"parameter": "item", "query_parameter": "Номенклатура", "encoding": "object_ref"},
    {"parameter": "moment", "query_parameter": "Момент", "encoding": "datetime"}
  ],
  "column_bindings": [
    {
      "column": "Номенклатура",
      "fact_id": "item.ref",
      "accepted_mcp_types": ["СправочникСсылка.Номенклатура"],
      "converter": "object_ref"
    },
    {
      "column": "Количество",
      "fact_id": "stock.balance_quantity",
      "accepted_mcp_types": ["Число"],
      "converter": "decimal"
    }
  ],
  "pagination": {"strategy": "none"}
}
```

`<verified>` в примере означает, что реальный package обязан содержать точное
имя, подтвержденное по metadata/config и live contract tests. Placeholder в
импортируемом skill запрещен lint rules.

### 3.1. Закрытый execution contract

`operation.query_template.execution` является обязательным discriminated union:

- `single_select`: `statement_count=1`, `final_statement=1`, единственный
  statement является read-only `ВЫБРАТЬ` без `ПОМЕСТИТЬ`;
- `linked_temp_batch`: `statement_count` от 2 до 16,
  `final_statement=statement_count`, а `temporary_tables` содержит от 1 до 15
  точных producer/consumer contracts.

Пример manifest связанного пакета:

```json
{
  "kind": "linked_temp_batch",
  "statement_count": 3,
  "final_statement": 3,
  "temporary_tables": [
    {
      "name": "ВтПродажи",
      "producer_statement": 1,
      "consumer_statements": [2]
    },
    {
      "name": "ВтИтоги",
      "producer_statement": 2,
      "consumer_statements": [3]
    }
  ]
}
```

Lexer/shallow parser, учитывающий строки, quoted identifiers и комментарии,
сверяет manifest с текстом. Каждый промежуточный statement обязан создать ровно
одну объявленную временную таблицу верхнеуровневым `ПОМЕСТИТЬ`; последний
statement не создает таблицу и является единственным результатом. Таблица
создается до чтения, не переопределяется, имеет потребителя, а каждый producer
имеет транзитивный путь к финальному statement. Orphan/independent statements,
циклы, пустые внутренние statements, несколько финальных SELECT и любой не-SELECT
statement запрещены. Допустим не более одного завершающего `;`.

Весь `linked_temp_batch` передается одним request в `execute_query` и использует
один менеджер временных таблиц. Разбиение на шаги composite plan запрещено:
atomic skill остается одной операцией. `parameter_bindings` относятся ко всему
package, `column_bindings` - только к aliases финального statement.

### 3.2. Инвариантные константы и бизнес-значения

`operation.query_template.invariant_constants` обязателен, даже если это пустой массив, и
содержит не более 64 закрытых typed declarations. После удаления comments и
разбора синтаксиса каждый непараметризованный литерал должен точно совпасть с
одной декларацией по statement, type/value, semantic role и `occurrences`.

Разрешены только следующие variants:

| `kind` | Допустимый смысл |
| --- | --- |
| `zero_boundary` | `0` как equality/sign boundary, null substitution или arithmetic identity |
| `boolean_literal` | Булево состояние, computed flag или null substitution |
| `null_literal` | `NULL`/`НЕОПРЕДЕЛЕНО` как отсутствие или computed value |
| `empty_literal` | Пустая строка как отсутствие/substitution, но не доменное значение |
| `metadata_constant` | Член перечисления, предопределенная или пустая ссылка по полному metadata symbol |
| `structural_integer` | Положительное целое для TOP/rank/query-language arity, максимум 1000 |
| `unit_scale` | Положительный фиксированный коэффициент percentage/unit conversion |

Например, `Количество > 0` декларирует `zero_boundary` с ролью
`sign_boundary`; `ПЕРВЫЕ 20` декларирует `structural_integer` с ролью
`top_limit`. Произвольного `domain_constant` или строкового escape hatch нет.

Значение является запрещенным business-instance value и обязано быть typed
parameter, если оно идентифицирует или фильтрует изменяемый объект, период или
условие конкретной ИБ/turn: товар, склад, организацию, контрагента,
характеристику, серию, назначение, UUID/ref, код, артикул, имя, номер документа,
дату, произвольный доменный текст или business threshold. Лексическая форма не
определяет решение: `0` допустим как sign boundary, но не как код склада; `10`
допустимо как declared TOP limit, но не как порог задолженности.

Import validator отклоняет undeclared/extra declaration, неверное число
вхождений, роль вне variant, metadata symbol без compatibility assertion и
попытку представить business-instance value как invariant. Та же проверка
повторяется перед runtime execution pinned template.

### 3.3. Exact mapping, не поиск колонок

`column_bindings` связывает точный alias с `fact_id`, accepted MCP types и
converter. Import validator доказывает, что:

- каждый required output fact имеет ровно один binding;
- binding ссылается на объявленный fact;
- entity converter соответствует entity-ref fact;
- aliases в query уникальны и совпадают с bindings;
- required metadata содержит использованные источники/поля;
- fixture response проходит тот же normalizer и sufficiency validator.

Дополнительно множество `provides.fact_types` обязано быть в точности равно
множеству `output_contract.facts[*].semantic_type`. Лишний advertised type и
необъявленный output type одинаково являются import error.

Runtime не ищет колонки по словам. Любое несовпадение дает `contract_error`, а не
empty result.

### 3.4. Pagination

`none` используется для scalar/aggregate/exact object. `prefix` допустим только
при доказанном `maximum_total <= 1000`. `keyset` задает stable sort,
`has_cursor_query_parameter` и exact cursor bindings. Query author обязан
включить эти параметры в фиксированный template. Import validator сверяет
pagination contract с parameter names и output sort facts.

## 4. Documentation-retrieval skill

Documentation skill не содержит query 1С и не использует MCP. Он фиксирует
corpus/index, query parameter, retrieval policy, source filters, ожидаемые roles
и output bindings:

```json
{
  "kind": "documentation_retrieval",
  "index": "ut_built_in_help",
  "query_parameter": "search_text",
  "retrieval": {
    "engine": "fts5_bm25_ru_stem_v1",
    "top_k": 8,
    "max_chunks_per_source": 3
  },
  "filters": {
    "source_kind": "built_in_help",
    "language": "ru",
    "metadata_kinds": ["document", "form"],
    "path_prefixes": ["Documents/ЗаказКлиента/"]
  },
  "chunk_roles": ["definition", "status_meaning", "restriction"],
  "output_bindings": [
    {"chunk_field": "text", "fact_id": "documentation.fragment"},
    {"chunk_field": "citation", "fact_id": "documentation.citation"}
  ]
}
```

В schema v1 `source_kind` фиксирован `built_in_help`. Любое внешнее значение
отклоняется JSON Schema при import и непосредственно на retrieval boundary.
Поддержка другого корпуса потребует явной следующей версии контракта, а не
добавления произвольного поля.

Политика расхождений едина для всех documentation skills и поэтому не является
настраиваемым полем навыка. Несколько найденных фрагментов встроенного корпуса
с разными позициями сохраняются как отдельные `document_fragment` facts и
citations. Evidence связывает их типизированным `documentation_disagreements`;
renderer обязан показать минимум две позиции и citation каждой, не разрешая
расхождение по rank одного фрагмента.

## 5. Semantic output contract

Output contract описывает бизнес-факты независимо от физических колонок:

```json
{
  "contract_id": "ut.stock.balance.v1",
  "contract_version": "1.0.0",
  "cardinality": "many",
  "row_identity_fact_ids": ["item.ref", "warehouse.ref"],
  "facts": [
    {
      "fact_id": "stock.balance_quantity",
      "semantic_type": "measure.stock_balance",
      "value_type": "quantity",
      "role": "measure",
      "required": true,
      "nullable": false,
      "title_ru": "Фактический остаток",
      "unit_contract": {"mode": "from_fact", "fact_id": "item.unit"}
    }
  ],
  "sufficiency": {
    "required_fact_sets": [["item.ref", "stock.balance_quantity", "time.moment"]],
    "empty_semantics": "confirmed_no_rows",
    "zero_fact_ids": ["stock.balance_quantity"],
    "truncation_policy": "page_is_complete"
  },
  "renderer": {
    "kind": "table",
    "primary_fact_ids": ["stock.balance_quantity"],
    "column_fact_ids": ["item.ref", "warehouse.ref", "stock.balance_quantity"]
  }
}
```

`required_fact_sets` допускает только явно перечисленные альтернативные формы
достаточного результата. Нулевой value допустим только для `zero_fact_ids`.
Renderer hint выбирает generic renderer, но не изменяет facts.

Перед выполнением plan coverage сопоставляет каждый
`interpretation.required_facts[*]` и каждый `result.final_outputs[*]`, не только
по `fact_id`. Требования сравниваются по `semantic_type`, `value_type`,
`cardinality`, `unit_dimension` и `time_semantics`; provider доказывает их через
`output_contract.cardinality`, `output_contract.facts[*]`,
`output_contract.row_identity_fact_ids` и явные time facts. Для
optional/nullable facts это свойство также учитывается.
Факт промежуточного resolver шага не может закрыть final requirement другого
типа или cardinality. После выполнения те же требования проверяются против
конкретных evidence fact instances и их provenance.

Если два data evidence facts претендуют на один requirement при одинаковых
identity/unit/time coordinates, но содержат несовместимые values, coverage имеет
статус `ambiguous` и не создает единственный final fact. Для documentation
claims core сохраняет обе позиции с независимым provenance и создает элемент
`documentation_disagreements[*]` со ссылками на все fact/citation IDs. Ни один
вариант не выбирается по порядку, rank или confidence. Ответ может только явно
показать расхождение либо использовать отдельное детерминированное правило,
заранее объявленное контрактом; такого общего правила в MVP нет.

## 6. Dependencies

Runtime contracts указывают совместимую версию `skill-runtime` и ровно один из
`mcp.execute_query`/`help-index`, соответствующий operation kind. Skill
dependencies означают, что для заявленной composition availability в каталоге
должна присутствовать совместимая версия другого skill. Atomic skill не вызывает
dependency скрыто; каждый вызов все равно присутствует в composite plan.

Import validator строит dependency DAG, отклоняет cycle, missing/incompatible
version и конфликт fact types. Delete запрещен, если новый catalog snapshot
оставит обязательную зависимость активного skill неудовлетворенной.

## 7. Tests в навыке

Каждый skill содержит минимум один `positive` и один `negative` fixture. JSON
Schema проверяет форму, semantic validator - наличие обоих классов.

Data fixture хранит нормализуемый MCP envelope и expected outcome/facts. Doc
fixture хранит chunks с `ut-help://` citation. Эти тесты проверяют package в
изоляции; отдельно обязательны live tests query template на целевой УТ и corpus
composition tests. Fixture не может подменить live compatibility proof.

## 8. Provenance и checksum

Допустимы portable URI:

- `ut-config://11.5.27.56/...`;
- `ut-help://11.5.27.56/...#anchor`;
- `mcp-contract://execute_query/v1`;
- `test-evidence://...`.

Абсолютные local paths запрещены. Digest вычисляется:

1. удалить top-level `integrity`;
2. canonicalize оставшийся JSON по RFC 8785 (JCS);
3. вычислить SHA-256 и записать 64 lowercase hex в `integrity.digest`.

Package digest вычисляется тем же способом по package целиком, включая уже
заполненные integrity вложенных skills. Checksum обнаруживает повреждение, но не
доказывает доверенного автора; signing не входит в локальный MVP.

## 9. Package и конфликт версий

Package содержит target, embedded skills, dependency lock, provenance и свой
digest. `dependency_lock` является точным замкнутым lock prospective catalog
graph для импортируемых roots: в нем ровно по одной записи для каждого embedded
skill и каждой его транзитивной skill dependency, без лишних записей. Embedded
entry обязана совпасть с `skills[*].version` и
`skills[*].integrity.digest`; external
dependency обязана уже существовать в pinned catalog с точно теми же
version/digest. Отсутствующая, лишняя или несовместимая запись отклоняет весь
package.

Уникальна пара `(skill_id, version)` во входном package и prospective catalog.
Если для нее встречается другой digest в embedded document, lock или active
catalog, это `SKILL_DIGEST_CONFLICT`, а не допустимая альтернатива или implicit
replace. Конфликт проверяется до transaction.

Команды импорта задают intent вне JSON:

- `create`: все добавляемые IDs отсутствуют;
- `replace`: для каждого заменяемого ID передан `expected_current_digest`, новая
  version отличается, dependency graph остается целым;
- `delete`: отдельная команда с `If-Match` digest;
- повтор того же package digest идемпотентен и возвращает текущую revision.

Не существует `draft`, `candidate`, `pending` или фонового дообучения. Результат
операции только `accepted` с новой catalog revision либо `rejected` с массивом
validation errors и неизменным каталогом.

## 10. Ресурсные пределы JSON

Граница API/CLI измеряет UTF-8 bytes до parse и отклоняет документ целиком:

| Document | Максимальный размер |
| --- | ---: |
| `skill` | 1 048 576 bytes (1 MiB) |
| `skill_package` | 33 554 432 bytes (32 MiB) |
| `planner_output` | 262 144 bytes (256 KiB) |
| `evidence_bundle` | 67 108 864 bytes (64 MiB) |

Для package предел 32 MiB применяется к исходному файлу до parse, а предел
1 MiB затем применяется к canonical UTF-8 representation каждого embedded
skill до schema/semantic validation.

Для всех четырех документов: nesting depth не более 32, общее число JSON nodes
не более 500 000, ни один массив не может содержать более 100 000 элементов.
Контекстные `maxItems` в schemas всегда строже generic ceiling. Например, skill
package содержит не более 500 skills, query package - 16 statements, invariants
- 64, а query text - 50 000 characters. Ограничение package bytes означает, что
500 максимальных skills одновременно не обязаны помещаться; первым срабатывает
любой превышенный limit.

Depth/node/byte limits невозможно выразить полностью в Draft 2020-12, поэтому
они являются обязательной pre-schema проверкой общего bounded JSON loader.
Decompression не используется; multipart upload и CLI file проходят один loader.
Ошибки имеют codes `JSON_BYTES_LIMIT`, `JSON_DEPTH_LIMIT`,
`JSON_NODE_LIMIT`/`JSON_ARRAY_LIMIT` и JSON pointer, если он уже известен.

## 11. Atomic import и hot reload

Pipeline до transaction:

1. проверить byte/depth/node/array limits bounded JSON loader;
2. parse и Draft 2020-12 schema validation;
3. проверить JCS/SHA-256 skills/package;
4. semantic lint: unique IDs, exact `provides/output`, parameter/fact mappings,
   no local values/paths, no placeholder/query interpolation,
   execution/invariant manifest, positive/negative tests;
5. compatibility against current database profile and `get_metadata` assertions;
6. read-only query lexer/shallow parser по ADR-0003; никакого BSL/tool кроме
   `execute_query`;
7. точный замкнутый dependency lock, DAG и digest conflict policy;
8. прогнать portable fixtures.

Затем одна SQLite transaction записывает immutable skill documents, active
bindings и `catalog_revision + 1`. После commit CatalogManager строит immutable
snapshot и атомарно меняет одну reference. Если процесс завершился между commit
и swap, startup/revision watcher восстанавливает snapshot из БД. Turn всегда
держит strong reference на pinned snapshot, поэтому не видит половину импорта.

Проверка через web и CLI вызывает один `ImportSkillPackage` use case и обязана
дать одинаковые revision/digests. После успешного commit следующий запрос видит
skill без перезапуска приложения.
