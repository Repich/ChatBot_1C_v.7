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

Data skill содержит один immutable query template. `parameter_bindings` -
единственный путь значений в MCP; runtime string interpolation запрещен.

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

### 3.1. Exact mapping, не поиск колонок

`column_bindings` связывает точный alias с `fact_id`, accepted MCP types и
converter. Import validator доказывает, что:

- каждый required output fact имеет ровно один binding;
- binding ссылается на объявленный fact;
- entity converter соответствует entity-ref fact;
- aliases в query уникальны и совпадают с bindings;
- required metadata содержит использованные источники/поля;
- fixture response проходит тот же normalizer и sufficiency validator.

Runtime не ищет колонки по словам. Любое несовпадение дает `contract_error`, а не
empty result.

### 3.2. Pagination

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
digest. Уникальна пара `(skill_id, version)`; другой digest для уже известной
пары всегда отклоняется.

Команды импорта задают intent вне JSON:

- `create`: все добавляемые IDs отсутствуют;
- `replace`: для каждого заменяемого ID передан `expected_current_digest`, новая
  version отличается, dependency graph остается целым;
- `delete`: отдельная команда с `If-Match` digest;
- повтор того же package digest идемпотентен и возвращает текущую revision.

Не существует `draft`, `candidate`, `pending` или фонового дообучения. Результат
операции только `accepted` с новой catalog revision либо `rejected` с массивом
validation errors и неизменным каталогом.

## 10. Atomic import и hot reload

Pipeline до transaction:

1. ограничить размер/depth/count JSON;
2. parse и Draft 2020-12 schema validation;
3. проверить JCS/SHA-256 skills/package;
4. semantic lint: unique IDs, parameter/fact mappings, no local values/paths,
   no placeholder/query interpolation, positive/negative tests;
5. compatibility against current database profile and `get_metadata` assertions;
6. read-only query lexer/allowlist; никакого BSL/tool кроме execute_query;
7. dependency DAG и conflict policy;
8. прогнать portable fixtures.

Затем одна SQLite transaction записывает immutable skill documents, active
bindings и `catalog_revision + 1`. После commit CatalogManager строит immutable
snapshot и атомарно меняет одну reference. Если процесс завершился между commit
и swap, startup/revision watcher восстанавливает snapshot из БД. Turn всегда
держит strong reference на pinned snapshot, поэтому не видит половину импорта.

Проверка через web и CLI вызывает один `ImportSkillPackage` use case и обязана
дать одинаковые revision/digests. После успешного commit следующий запрос видит
skill без перезапуска приложения.
