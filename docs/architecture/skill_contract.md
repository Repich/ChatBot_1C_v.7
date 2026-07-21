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
| `result_constraints` | Явные equality-инварианты output fact относительно typed parameter |
| `dependencies` | Runtime contracts и skill dependencies для composition availability |
| `examples` | Подходящие и неподходящие русские вопросы |
| `tests` | Положительные/отрицательные portable fixtures |
| `provenance` | Автор, источник metadata/help/test evidence, без local paths |
| `integrity` | SHA-256 по RFC 8785 canonical JSON без top-level `integrity` |

`skill_id` не содержит товар, склад, город, период, формулировку или instance ID.
Например, `ut115.stock.balance` допустим, а
`ut115.stock.balance-kurtka-central-warehouse` отклоняется lint rule.

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

### 2.1. Generic proof для `EntityRef`

`parameters[*].semantic_type` и `parameters[*].entity_types` содержат только
business semantic types. Их запрещено использовать как физические имена 1С и
запрещено дополнять application-словарем вида `semantic_type -> ТипОбъекта`.
Физический тип обычного MVP-входа доказывается producer-контрактом:

1. `previous_step` разрешается в исходный confirmed `Fact`, а не сразу в его
   JSON value. `session_context` разрешается по opaque handle в исходный
   `fact_instance_id` и сохраненный origin evidence.
2. Producer определяется из `Fact.step_id` и `StepEvidence.operation_ref` в
   pinned catalog snapshot. `Fact.source_locator` обязан указывать ровно на
   `query_column_binding` этого producer.
3. `output_contract.facts[*]` с тем же `fact_id` обязан иметь
   `value_type=entity_ref`; его `semantic_type` обязан совпасть с
   `parameters[*].semantic_type` consumer и входить в закрытый semantic allowlist
   `parameters[*].entity_types`. Planner `expected_semantic_type` обязан
   совпасть с semantic type исходного факта. Иерархия или prefix inference между
   semantic types не применяется.
4. Exact `operation.column_bindings[*]` с тем же `fact_id` и колонкой обязан
   иметь `converter=object_ref`. Значение `EntityRef.ТипОбъекта` обязано входить
   в его `accepted_mcp_types`. Это и есть декларативное доказательство
   допустимого physical object type.
5. Consumer дополнительно проверяет `value_type`, cardinality и
   `allowed_sources`, после чего без реконструкции передает сохраненный полный
   `_objectRef` в MCP.

Для `entity_ref_list` эти проверки выполняются для каждого элемента и каждого
origin fact. Deterministic operator не может создать, ретегировать или заменить
`EntityRef`: он только передает исходный confirmed fact с его provenance.

Raw `EntityRef` из `literal`, пользовательского slot или текста модели в MVP
запрещен. Упоминание пользователя сначала становится строковым параметром
resolver skill, а структурная ссылка появляется только как его evidence fact.
Если будущему trusted system source потребуется создавать ссылки без query
producer, это потребует новой закрытой portable source-схемы с явным массивом
`accepted_object_types` и provenance профиля; не application dictionary.

Текущей `skill.schema.json` для обычного пути достаточно: semantic leg задают
`semantic_type/entity_types`, physical leg задает
`column_bindings.accepted_mcp_types`. Нельзя переопределять `entity_types` как
physical types. Semantic validator обязан ограничить `allowed_sources` entity
parameters значениями `session_context`/`previous_step`; нарушение имеет code
`ENTITY_REF_SOURCE_UNPROVEN`. Domain/persistence обязаны хранить у context fact
как минимум `origin_turn_id + origin_fact_instance_id`; по turn
восстанавливаются evidence, pinned catalog snapshot и producer binding. DTO для
prompt может по-прежнему содержать только opaque handle, semantic type и
presentation. Потеря origin pointer является `CONTEXT_PROVENANCE_MISSING`, а
несовпадение любого звена - `ENTITY_REF_CONTRACT_MISMATCH`; MCP при этих ошибках
не вызывается.
Проверка по префиксам `document.*`, `catalog.*`, `ДокументСсылка.*` и подобным не
является доказательством и также не нужна: exact physical membership уже задан
producer binding.

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
    "template_id": "ut115.stock.balance.v1",
    "language": "1c-query",
    "text": "ВЫБРАТЬ Остатки.Номенклатура КАК Номенклатура, Остатки.Склад КАК Склад, Остатки.КоличествоОстаток КАК Количество ИЗ РегистрНакопления.<verified>.Остатки(&Момент, Номенклатура = &Номенклатура) КАК Остатки ГДЕ НЕ &ЕстьКурсор ИЛИ Остатки.Склад > &СкладКурсора УПОРЯДОЧИТЬ ПО Остатки.Склад",
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
      "column": "Склад",
      "fact_id": "warehouse.ref",
      "accepted_mcp_types": ["СправочникСсылка.Склады"],
      "converter": "object_ref"
    },
    {
      "column": "Количество",
      "fact_id": "stock.balance_quantity",
      "accepted_mcp_types": ["Число"],
      "converter": "decimal"
    }
  ],
  "pagination": {
    "strategy": "keyset",
    "has_cursor_query_parameter": "ЕстьКурсор",
    "sort": [{"fact_id": "warehouse.ref", "direction": "asc"}],
    "cursor_bindings": [
      {
        "fact_id": "warehouse.ref",
        "query_parameter": "СкладКурсора",
        "encoding": "object_ref"
      }
    ]
  }
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
- для entity converter фактический `ТипОбъекта` каждой ссылки входит в
  `accepted_mcp_types` exact binding;
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
при доказанном data-independent invariant
`cardinality(filtered result) <= maximum_total <= 1000`. Само JSON-поле
`maximum_total`, MCP/query limit, `ПЕРВЫЕ N`, наблюдавшийся row count и fixture
не являются proof. Skill обязан сослаться через
`provenance.source_references` на digest-pinned metadata/config assertion,
закрытый конечный domain либо bounded input contract с доказанным
one-row-per-input mapping. Semantic import validator сверяет citation, область
filter и declared M; обычный каталог, документ, tabular section или register по
умолчанию неограничен.

Cardinality/pagination matrix закрыта: `keyset|prefix` разрешены только при
`output_contract.cardinality=many`; `aggregate`, `exactly_one` и `zero_or_one`
обязаны иметь `pagination.strategy=none`. В частности, смена только cardinality
у list skill не создает aggregate producer. Нарушение отклоняется при import как
`PAGINATION_CARDINALITY_MISMATCH`. Успешный aggregate всегда дает один factual
row с `collection_scope=complete_set`; `page_is_complete` не может легализовать
paginated aggregate.

Prefix producer материализует полный ordered set один раз и public pagination
режет immutable evidence локально. При total ровно `maximum_total=M` это полный
результат: последняя display page имеет `has_more=false`. Если producer вернул
`M+1` rows либо сообщил `truncated=true/has_more=true` на границе M, invariant
ложен: `RESULT_PREFIX_BOUND_EXCEEDED`, весь response discarded, не `partial`.
Если adapter не умеет доказать отсутствие truncation ровно на M, prefix contract
не импортируется.

`keyset` обязателен для unbounded producers. Он задает stable total order,
`has_cursor_query_parameter` и exact cursor bindings. Sort fact IDs уникальны;
cursor fact IDs обязаны быть тем же ordered list. Cursor/guard/обычные query
parameter names уникальны без учета регистра и не пересекаются. Каждый sort
fact имеет один column binding, `required=true`, `nullable=false` и совместимый
cursor encoding. Sort suffix содержит всю declared row identity, каждый ее fact
ровно один раз, и не содержит иных facts.

Dedicated 1C-query AST checker связывает sort facts через column bindings с
final projection expressions и доказывает exact ORDER BY плюс guarded strict
lexicographic after-predicate (`>` для `asc`, `<` для `desc`, equality prefix
для следующей координаты). Это semantic query-contract check, не поиск
подстрок. Неполный AST proof дает `PAGINATION_QUERY_CONTRACT_UNPROVEN` и
запрещает import/activation. Полная матрица reject codes и mutation cases
нормативно задана в `docs/testing/slice2_acceptance_contract.md`, section 5.2.
Runtime использует `page_size+1` probe. Наличие лимита 1000 не разрешает
заменить keyset на prefix.

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

`empty_semantics` применяется не до, а после проверки envelope, schema,
column bindings, cardinality и nullability. Эффективно пустым результатом
считаются либо ноль строк, либо ровно одна null-sentinel строка: у нее нет
ненулевого `row_identity_fact_id`, все значения facts выбранного
`required_fact_set`, полученные из этой строки, равны `null`, и каждый такой
fact объявлен `nullable=true`. Строка с валидной identity и только пустым
optional/nullable реквизитом не является sentinel и остается обычной строкой.

| `empty_semantics` | Terminal outcome эффективной пустоты |
| --- | --- |
| `confirmed_not_found` | `success_empty`, reason `not_found` |
| `confirmed_no_rows` | `success_empty`, reason `no_rows` |
| `not_applicable` | `contract_error`, code `RESULT_EMPTY_SEMANTICS_NOT_APPLICABLE` |
| `error_if_empty` | `contract_error`, code `RESULT_EMPTY_FORBIDDEN` |

`not_applicable` означает, что skill не объявляет бизнес-интерпретацию пустого
результата; это не отдельный public outcome. Поэтому наблюдаемая пустота для
него является producer contract violation, а не успешным «не применимо».

`null` в fact с `nullable=false` всегда дает `contract_error` с code
`RESULT_REQUIRED_FACT_NULL` до применения таблицы. Null-sentinel никогда не
преобразуется в `0`, даже если fact перечислен в `zero_fact_ids`; только
фактическое типизированное числовое значение `0` может дать `zero_aggregate`.
Ни ноль строк, ни null-sentinel сами по себе не дают `partial`.

Exact entity identity между входом и результатом задается только явным
ограничением навыка:

```json
"result_constraints": [
  {
    "kind": "fact_equals_parameter",
    "fact_id": "order.ref",
    "parameter": "order"
  }
]
```

Оба конца обязаны быть `entity_ref` одного semantic type, а output fact должен
иметь exact column binding. Runtime сравнивает business identity
`(semantic_type, ТипОбъекта, УникальныйИдентификатор)`; `Представление` может
обновиться и в identity не входит. Полный входной ref при этом передается в MCP
losslessly. Совпадение semantic type само по себе не создает equality-инвариант.

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

Для execute plan mapping является one-to-zero/one со стороны requirement и
ровно one со стороны final output: каждый `required=true` requirement имеет
ровно один matching final output, optional requirement - ноль или один, а
каждый объявленный final output обязан принадлежать одному requirement.
Несколько эквивалентных providers, unclaimed output и одинаковые requirement
signatures с разными `required` отклоняются как semantic ambiguity, а не
разрешаются порядком planner output.

Evidence coverage не теряет эту criticality: для каждого planner
`requirement_id` создается ровно одна запись с тем же обязательным boolean
`required`. Optional requirement без final/provider остается
`required=false,status=missing`. `coverage.sufficient` является conjunction
`status=covered` и satisfied `fact|visible_page|complete_set` collection
obligation для записей `required=true`; optional
missing/ambiguous/incompatible/incomplete публикуется в details, но не меняет
sufficient или terminal success. Валидная incomplete page остается
`status=covered`, однако не удовлетворяет required `complete_set` obligation.

`skill_call.required_output_fact_ids` не означает, что сам step required. Это
producer-local demand contract на случай, если call выполняется. Массив обязан
включать:

- facts этого step, указанные в `result.final_outputs`;
- facts, которые downstream bindings/operators читают из этого step;
- identity/unit/time/support facts выбранного declared `required_fact_set`,
  нужные для проверки первых двух групп и result constraints.

Массив не может включать unrelated output только для того, чтобы сделать step
«используемым». Core пересчитывает demand по validated DAG и pinned skill
contract; missing/extra demand отклоняется до MCP. Step criticality затем
выводится обратным closure от required final requirements, одинаково для skills
и operators. У optional step его `required_output_fact_ids` все равно являются
обязательными для валидности response этого call: их нарушение делает response
step-level `contract_error`, но final reducer discard-ит его, если required
coverage независимой ветви уже sufficient.

Если один call физически общий для required и optional branches, он целиком
входит в `required_closure`: нарушение любого его demanded output делает
required response недоверенным и доминирует. Best-effort optional enrichment,
которое должно иметь независимую failure boundary, planner обязан вынести в
отдельный optional call.

Collection completeness входит в fact signature для `count`. StepResult
producer-а с `truncation_policy=page_is_complete` и любой отдельный keyset page
имеют scope `visible_page`; `has_more=false` не повышает scope отдельной
continuation page. `CountOperator` читает только этот StepResult, не выполняет
hidden drain и поэтому создает только visible count. Он может создать total
count лишь для `complete_set`: fully materialized proved-prefix evidence,
непагинируемого full-set producer либо отдельного aggregate producer.
Page-scoped output не сопоставляется с total requirement и дает pre-execution
`PLAN_COUNT_SCOPE_MISMATCH`. Document count всегда использует distinct document
identity, а не число rows/lines. Новый Evidence 1.1 всегда явно сохраняет
`collection_scope`; отсутствие поля является contract error. Только legacy
Evidence 1.0 reader нормализует отсутствующее поле в `complete_set` in-memory и
не переписывает исходный payload.

Если два data evidence facts претендуют на один requirement при одинаковых
identity/unit/time coordinates, но содержат несовместимые values, coverage имеет
статус `ambiguous` и не создает единственный final fact. Для documentation
claims core сохраняет обе позиции с независимым provenance и создает элемент
`documentation_disagreements[*]` со ссылками на все fact/citation IDs. Ни один
вариант не выбирается по порядку, rank или confidence. Ответ может только явно
показать расхождение либо использовать отдельное детерминированное правило,
заранее объявленное контрактом; такого общего правила в MVP нет.

Этот runtime ambiguity contract относится к нескольким fact instances уже
выбранного final provider. Он не разрешает два альтернативных
`final_outputs` для одного requirement: такая plan ambiguity отклоняется до
execution.

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

## 9. Skill/package import и конфликт версий

Import boundary принимает два уже существующих закрытых wire documents по
top-level discriminator: `document_type=skill` и
`document_type=skill_package`. Это один `ImportSkillDocument` use case, а не два
разных catalog pipeline.

Для bare `skill` use case строит внутренний `ResolvedImportSet`: imported skill
является единственным embedded root, а его транзитивные skill dependencies
разрешаются в одном pinned active catalog snapshot и фиксируются точными
`skill_id/version/digest`. Документ и его digest не переписываются. Missing,
incompatible или неоднозначная dependency отклоняет import до transaction. В
чистый instance bare skill поэтому переносим непосредственно только при пустом
`dependencies.skills`; dependency-bearing skill переносится тем же одним
self-contained `skill_package` файлом, содержащим выбранный skill и closure.
Никакой download/implicit upgrade зависимости во время import нет.

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
2. прочитать top-level `document_type` и применить ровно одну Draft 2020-12
   schema: skill или skill-package;
3. проверить JCS/SHA-256 skills/package;
4. semantic lint: unique IDs, exact `provides/output`, parameter/fact mappings,
   no local values/paths, no placeholder/query interpolation,
   execution/invariant manifest, positive/negative tests;
5. compatibility against current database profile and `get_metadata` assertions;
6. read-only query lexer/shallow parser по ADR-0003; никакого BSL/tool кроме
   `execute_query`;
7. построить exact closure для bare skill либо проверить package
   `dependency_lock`, затем DAG и digest conflict policy;
8. прогнать portable fixtures.

Затем одна SQLite transaction записывает immutable skill documents, active
bindings и `catalog_revision + 1`. После commit CatalogManager строит immutable
snapshot и атомарно меняет одну reference. Если процесс завершился между commit
и swap, startup/revision watcher восстанавливает snapshot из БД. Turn всегда
держит strong reference на pinned snapshot, поэтому не видит половину импорта.

Проверка через web и CLI вызывает один `ImportSkillDocument` use case и обязана
дать одинаковые revision/digests. Exported bytes не меняются между instance A и
instance B. После успешного commit следующий запрос видит skill без перезапуска
приложения.
