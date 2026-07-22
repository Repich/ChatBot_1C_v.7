# Каталог resolver-навыков slice 3B

Статус: требования к реализации каталога навыков для
`1С:Управление торговлей (базовая), редакция 11`, релиз `11.5.27.56`, режим
совместимости `8.3.27`.

## 1. Назначение и границы

Документ определяет минимальный переносимый каталог entity resolver-ов и
связанных exact-ref навыков, необходимый для завершения slice 3B. Нормативная
основа:

- `docs/requirements/product_requirements.md`;
- `docs/requirements/skill_catalog.md`;
- `docs/requirements/mvp_skill_catalog_business.md`;
- `docs/requirements/slice3_entity_context_requirements.md`;
- `docs/testing/slice3_acceptance_contract.md`;
- актуальные ветви `1.1.0` в `schemas/skill.schema.json` и
  `schemas/skill-package.schema.json`;
- Evidence `1.1.0` из `schemas/evidence.schema.json`;
- существующие навыки в `skills/ut-11.5.27.56/` и ссылки `R02-R12` из
  `docs/architecture/full_catalog_blueprint.md`.

Каталог задает бизнес-сущности, portable contracts, входы, typed facts,
кардинальность и поведение уточнений. Он не задает тексты запросов 1С,
application-классы, алгоритм shortlist или способ хранения контекста.

Физические имена стандартной УТ ниже являются metadata anchors переносимого
skill contract. Они не образуют core-справочник типов. Конкретные названия,
коды, ИНН, номера документов и пользовательские слова всегда остаются
параметрами. Примеры формулировок иллюстративны и не являются правилами выбора
навыка.

### 1.1. Состояние baseline

На момент подготовки документа все JSON-файлы в `skills/ut-11.5.27.56/`
имеют `schema_version=1.0.0`. Поэтому даже навыки с SemVer `1.1.0` еще не
объявляют `resolution`, `context_export_policy` и `context_slot_keys` schema
1.1.

Для slice 3B требуется публикация новых SemVer без изменения старых bytes:

- четыре существующих item resolver-а `R01A-R01D` переводятся на protocol
  `typed_entity_resolver_v1`;
- существующий `R06` переводится на тот же protocol без расширения его
  недоказанных фильтров;
- `R02-R05` и `R07-R12` добавляются по каталогу ниже;
- resolver-capable producers документов переводятся на candidate cardinality
  `many`;
- consumer facts организации унифицируются на канонический
  `party.organization`; существующий `catalog.organization` не считается
  совместимым alias и требует новой версии producer/consumer contracts.

## 2. Общий контракт schema 1.1

### 2.1. Обязательные поля resolver-а

Каждый resolver является `data_query` skill со следующими инвариантами:

| Поле | Требование |
| --- | --- |
| `schema_version` | Ровно `1.1.0` |
| `output_contract.cardinality` | Ровно `many`, даже для exact key |
| `output_contract.resolution.protocol` | `typed_entity_resolver_v1` |
| `identity_fact_id` | Required, non-null `entity_ref`, `role=entity`, exact `query_column_binding`, `converter=object_ref` |
| `row_identity_fact_ids` | Содержит `identity_fact_id`; label и presentation в identity не входят |
| `candidate_label_fact_ids` | Только безопасные различающие факты, без UUID, `_objectRef` и physical type |
| `role_proof_fact_ids` | Required non-null факты роли/вида, если семантический тип уже содержит роль |
| `default_slot_key` | Ровно слот из реестра раздела 3 |
| `context_export_policy` | `selected_only`, тот же identity fact и slot, lifetime `session`, `max_members<=100` |
| `parameters[*].context_slot_keys` | Обязательное поле; `[]` для text/system inputs, точный allowlist для context entity inputs |

Множество выбранных сущностей не является одним output fact типа
`entity_ref_list`: Evidence содержит несколько `entity_ref` fact instances с
одним context handle. `entity_ref_list` используется только как тип входного
параметра consumer-а.

Raw `EntityRef` принимается только из `previous_step` или `session_context`.
Текст, user slot, literal и LLM output не могут быть источником `entity_ref`.

### 2.2. Resolver use mode и cardinality

Сам skill не объявляет `select_one`, `select_set` или `display_only`. Mode
выводится core из проверенного downstream contract:

| Кандидаты | `select_one` | `select_set` | `display_only` |
| --- | --- | --- | --- |
| `0` | `success_empty/not_found`, без export и pending | То же | Обычный пустой список |
| `1` | `selected_one`, один exact context member | `selected_set`, один member | Показать строку, context не создавать |
| `2..5`, complete set | Один persisted вопрос выбора; до ответа descendants заблокированы | Один complete selected set, если `max_members` не превышен | Показать список, context не создавать |
| `N>5` или `has_more=true` | Не давать выбрать из видимой страницы; запросить один typed критерий сужения и повторить pinned resolver | `partial`/continuation, export запрещен | Обычная пагинация списка |
| Ошибка/invalid row | `query_error`, `mcp_unavailable` или `contract_error`; не `not_found` | То же | То же |

Два ряда с одной точной тройкой
`(semantic_type, ТипОбъекта, УникальныйИдентификатор)` являются одним
кандидатом. Одинаковый `presentation` при разных тройках означает разных
кандидатов.

### 2.3. Общие правила clarification

1. Задается ровно один вопрос об одном слоте.
2. Варианты содержат только business type/role, наименование, код или номер,
   дату, организацию, склад и другие безопасные различия.
3. `N>1` для единичного слота никогда не разрешается первым, последним,
   ближайшим по тексту или последним упомянутым кандидатом.
4. `N=0` при заданных типе и критерии дает not found; нехватка типа, роли или
   критерия дает конкретный вопрос, а не ложный not found.
5. Явная коллекция использует `select_set` или `display_only` и не требует
   выбрать один объект только из-за нескольких строк.
6. Ответ на persisted clarification заполняет только pending slot и продолжает
   исходный план. Повторный resolver call после `choose` запрещен.
7. Неоднозначная замена нового объекта сохраняет старый active slot в ledger,
   но старый объект не подставляется в текущий вопрос.

## 3. Канонический реестр сущностей

| Сущность | `semantic_type` | `default_slot_key` | Standard UT metadata anchor | Обязательное доказательство |
| --- | --- | --- | --- | --- |
| Номенклатура | `catalog.item` | `selection.item` | `Справочник.Номенклатура` | Query predicate `ЭтоГруппа=false` и singleton fact `item.is_item=true` |
| Группа номенклатуры | `catalog.item.group` | `selection.item_group` | `Справочник.Номенклатура` | `ЭтоГруппа=true` |
| Партнер | `party.partner` | `selection.partner` | `Справочник.Партнеры` | Exact partner ref |
| Клиент | `party.customer` | `selection.customer` | `Справочник.Партнеры` | `Клиент=true` |
| Поставщик | `party.supplier` | `selection.supplier` | `Справочник.Партнеры` | `Поставщик=true` |
| Склад | `catalog.warehouse` | `selection.warehouse` | `Справочник.Склады` | Direct candidate facts `ТипСклада`/`Подразделение`; equality proof только для фактически примененного фильтра, без singleton role proof |
| Собственная организация | `party.organization` | `selection.organization` | `Справочник.Организации` | Producer физически читает каталог собственных организаций |
| Касса предприятия | `finance.cash_desk.enterprise` | `selection.cash_desk.enterprise` | `Справочник.Кассы` | Вид `enterprise` и owner-организация |
| Операционная касса/ККМ | `finance.cash_desk.pos` | `selection.cash_desk.pos` | `Справочник.КассыККМ` | Вид `pos` и owner-организация |
| Вид цены | `catalog.price_type` | `selection.price_type` | `Справочник.ВидыЦен` | Exact ref; назначение/валюта/VAT и признаки применения фактические |
| Заказ клиента | `document.sales_order` | `selection.sales_order` | `Документ.ЗаказКлиента` | Exact document kind |
| Реализация | `document.sales_shipment` | `selection.sales_shipment` | Стандартный документ реализации УТ | Exact document kind |
| Поступление | `document.purchase_receipt` | `selection.purchase_receipt` | Стандартный документ приобретения УТ | Exact document kind |
| Заказ поставщику | `document.purchase_order` | `selection.purchase_order` | `Документ.ЗаказПоставщику` | Exact document kind |
| Перемещение | `document.stock_transfer` | `selection.stock_transfer` | Стандартный документ перемещения УТ | Exact document kind |
| Характеристика | `catalog.item.characteristic` | `selection.item_characteristic` | `Справочник.ХарактеристикиНоменклатуры` | Применимость к exact item через стандартного владельца характеристик |
| Серия | `catalog.item.series` | `selection.item_series` | `РегистрСведений.АналитикаУчетаНоменклатуры` + `Справочник.СерииНоменклатуры` | Exact analytics row связывает item/characteristic/series и optional storage/purpose coordinates |
| Назначение запасов | `inventory.purpose` | `selection.inventory_purpose` | `Справочник.Назначения` | Exact purpose ref; не назначение склада |

`catalog.organization` и `party.organization` не взаимозаменяемы. Для slice 3
каноничен только `party.organization`.

## 4. Существующие item resolver-ы R01A-R01D

Эти навыки не входят в диапазон `R02-R12`, но являются обязательными
предшественниками `R02`, цен, запасов и `Q108`.

| Skill | Вход | Candidate facts | 1.1 additions |
| --- | --- | --- | --- |
| `ut115.ref.item.resolve-article-exact` | `article:string`, trim, exact | `item.ref`, `item.name`, `item.code`, `item.article`, `item.is_item=true` | identity `item.ref`, labels name/code/article, role proof `item.is_item`, slot `selection.item` |
| `ut115.ref.item.resolve-code-exact` | `catalog_code:string`, trim, exact | Те же | Те же |
| `ut115.ref.item.resolve-barcode-exact` | `barcode:string`, trim, exact в регистре штрихкодов | Те же плюс `item.matched_barcode` | Те же; barcode является параметром/label, не identity |
| `ut115.ref.item.resolve-name-contains` | `name_fragment:normalized_text`, escaped contains | Те же | Те же; keyset сохраняется |

Все четыре имеют producer cardinality `many`. Exact criterion не гарантирует
одну строку. Примеры: «Найди товар по артикулу `<артикул>`», «Какой штрихкод у
товара `<наименование>`?», «Покажи остатки товаров с названием
`<фрагмент>`». При выборе одного товара действует общая машина `0/1/N`; для
«покажи все» результат является list/set по downstream cardinality.

## 5. Каталог R02-R12

В обозначениях ниже `!` означает required/non-null fact, `?` —
optional/nullable fact. Все resolver facts имеют exact `query_column_binding`.

### 5.1. R02 — карточка номенклатуры

`ut115.ref.item.details` не является resolver-ом. Это exact-ref consumer
выбранного item из `R01A-R01D`.

**Входы**

| Параметр | Контракт |
| --- | --- |
| `item` | `entity_ref`, `semantic_type=catalog.item`, required, sources `previous_step|session_context`, `context_slot_keys=[selection.item]` |

**Typed facts**

| Fact | Type / role | Обязательность |
| --- | --- | --- |
| `item.ref` | `catalog.item / entity_ref / entity` | `!`, `fact_equals_parameter(item)` |
| `item.name` | `catalog.item.name / string / attribute` | `!` |
| `item.code` | `catalog.item.code / string / attribute` | `!` |
| `item.article` | `catalog.item.article / string / attribute` | `?` |
| `item.storage_unit` | `catalog.item.unit / string / dimension` | `!` |
| `item.barcode` | `catalog.item.barcode / string / attribute` | `?`, одна строка на barcode binding |
| `item.barcode_characteristic` | `catalog.item.characteristic / entity_ref / dimension` | `?` |
| `item.barcode_series` | `catalog.item.series / entity_ref / dimension` | `?` |

Cardinality — `many`, поскольку у товара может быть несколько штрихкодов и
связанных измерений. `resolution=null`, `context_export_policy=[]`: detail rows
не создают selection. Пустой barcode означает незаполненный реквизит, а не
отсутствие item. Примеры: «Какой штрихкод у товара `<наименование>`?», «В каких
единицах хранится `<товар>`?», «Покажи реквизиты этой позиции».

### 5.2. R03 — группа и ее состав

Чтобы не принимать одноименный item за group, `R03` состоит из resolver-family
и exact-ref consumer-а:

- `ut115.ref.item-group.resolve-name-contains`;
- `ut115.ref.item-group.resolve-code-exact`;
- `ut115.ref.item.group-members`.

**Resolver inputs и facts**

| Contract | Значение |
| --- | --- |
| Входы | Ровно один required criterion: `name_fragment:normalized_text` либо `catalog_code:string`; оба из `user_slot`, `context_slot_keys=[]` |
| Identity | `group.ref: catalog.item.group / entity_ref / entity !` |
| Labels | `group.name:catalog.item.group.name/string/attribute !`, `group.code:catalog.item.group.code/string/attribute !` |
| Role proof | `group.is_group:catalog.item.is_group/boolean/attribute !`, exact value `true`; запрос обязан исключать обычную номенклатуру |
| Resolution | identity `group.ref`, labels name/code, role proof `group.is_group`, slot `selection.item_group` |

Каждая физическая resolver-вариация имеет cardinality `many` и стандартное
`0/1/N` behavior.

`ut115.ref.item.group-members` принимает required `group:entity_ref` из
`selection.item_group` и required/system-confirmed
`include_descendants:boolean`. Для MVP default — `true`, и ответ явно называет
это правило. Он возвращает
`item.ref:catalog.item/entity_ref/entity !`,
`item.name:catalog.item.name/string/attribute !`,
`item.code:catalog.item.code/string/attribute !`,
`item.parent_group_ref:catalog.item.group/entity_ref/dimension !` и
`selected_group.ref:catalog.item.group/entity_ref/dimension !` с equality к
параметру. Cardinality
`many`, `resolution=null`, exports отсутствуют: строки состава не становятся
выбранными товарами автоматически.

Примеры: «Сколько товаров входит в группу `<группа>`?», «Покажи товары группы
с кодом `<код>`», «Цены на товары этой группы». Если найден item и group с
одинаковым названием, выбирается только доказанный group resolver result.

### 5.3. R04 — партнер, клиент и поставщик

Schema 1.1 не допускает core-retagging одного `partner.ref` по параметру role.
Поэтому один logical `R04` публикуется как role-qualified family. Для каждого
из префиксов `partner`, `customer`, `supplier` нужны atomic criteria variants:

```text
ut115.ref.<role>.resolve-name-contains
ut115.ref.<role>.resolve-code-exact
ut115.ref.<role>.resolve-inn-exact
```

| Role | Identity semantic type | Slot | Hard proof |
| --- | --- | --- | --- |
| partner | `party.partner` | `selection.partner` | Exact `Справочник.Партнеры` ref |
| customer | `party.customer` | `selection.customer` | `partner.is_customer=true` из `Партнеры.Клиент` |
| supplier | `party.supplier` | `selection.supplier` | `partner.is_supplier=true` из `Партнеры.Поставщик` |

`role_proof_fact_ids=[]` у partner variant,
`[partner.is_customer]` у customer variant и `[partner.is_supplier]` у supplier
variant. Core не добавляет и не меняет эти факты.

**Входы:** одна из `name_fragment:normalized_text`, `partner_code:string` или
`inn:string`, required в своей atomic variation. `role` не передается строкой
и не меняет output semantic type.

**Общие candidate facts:**

- `partner.ref:party.partner/entity_ref/entity !` для partner variant,
  `customer.ref:party.customer/entity_ref/entity !` для customer variant или
  `supplier.ref:party.supplier/entity_ref/entity !` для supplier variant;
- `partner.name:party.partner.name/string/attribute !`;
- `partner.code:party.partner.code/string/attribute !`;
- `partner.is_customer:party.role.customer/boolean/attribute !`;
- `partner.is_supplier:party.role.supplier/boolean/attribute !`;
- `contractor.ref:party.contractor/entity_ref/dimension ?`;
- `contractor.name:party.contractor.name/string/attribute ?`;
- `contractor.inn:party.contractor.inn/string/attribute ?`, required label в
  `resolve-inn-exact`.

Несколько контрагентов одного партнера могут дать несколько rows, но кандидат
один по partner identity. Роль не выводится из названия, ИНН или вида вопроса.
Примеры: «Покажи карточку клиента `<наименование>`», «Найди поставщиков с
`<фрагмент>` в названии», «Найди партнера по ИНН `<ИНН>`». Если цель вопроса не
определяет `partner/customer/supplier`, задается один вопрос о роли.

### 5.4. R05 — реквизиты партнера

`R05` — exact-ref consumer family, а не resolver:

- `ut115.ref.partner.details` принимает `party.partner` из
  `selection.partner`;
- `ut115.ref.customer.details` принимает `party.customer` из
  `selection.customer`;
- `ut115.ref.supplier.details` принимает `party.supplier` из
  `selection.supplier`.

Во всех трех случаях output entity fact сохраняет тот же exact semantic type и
имеет `fact_equals_parameter`. Остальные facts:

| Fact | Type / role | Обязательность |
| --- | --- | --- |
| `partner.name` | `party.partner.name / string / attribute` | `!` |
| `partner.code` | `party.partner.code / string / attribute` | `!` |
| `contractor.ref` | `party.contractor / entity_ref / dimension` | `?` |
| `contractor.name` | `party.contractor.name / string / attribute` | `?` |
| `contractor.inn` | `party.contractor.inn / string / attribute` | `?` |
| `contractor.kpp` | `party.contractor.kpp / string / attribute` | `?` |
| `contact.kind` | `party.contact.kind / string / dimension` | `?` |
| `contact.presentation` | `party.contact.presentation / string / attribute` | `?` |

Cardinality `many`: у партнера может быть несколько контрагентов и контактов.
Legal address определяется стандартным видом контактной информации в skill
query/proof, не словом в presentation. `resolution=null`, exports отсутствуют.
Примеры: «Покажи ИНН и КПП этого клиента», «Какой юридический адрес у выбранного
поставщика?». При нескольких контрагентах уточняется контрагент, если требуемый
реквизит нельзя отнести однозначно.

### 5.5. R06 — склад

Skill: новая schema-1.1 версия `ut115.ref.warehouse.resolve`.

| Вход | Контракт |
| --- | --- |
| `name_fragment` | Optional `normalized_text`, escaped contains, `user_slot|system` |
| `retail_only` | Optional `boolean`, default `false`; fixed metadata predicate на стандартный retail enum, не анализ названия |
| `department` | Optional `entity_ref`, `catalog.department`, source `previous_step`, `context_slot_keys=[]`; slice 3B не вводит недоказанный active department slot |

Candidate facts:
`warehouse.ref:catalog.warehouse/entity_ref/entity !`,
`warehouse.name:catalog.warehouse.name/string/attribute !`,
`warehouse.type:catalog.warehouse.type/string/attribute !`,
`warehouse.is_retail:catalog.warehouse.is_retail/boolean/attribute !`,
`warehouse.department:catalog.department/entity_ref/dimension ?` и
`warehouse.department_name:catalog.department.name/string/dimension ?`.
Identity — `warehouse.ref`; обязательные labels — name/type;
`role_proof_fact_ids=[]`; slot — `selection.warehouse`.

`warehouse.type` и `warehouse.is_retail` являются candidate/display facts, а не
singleton role proof общего resolver-а: при `retail_only=false` один вызов
законно возвращает склады разных типов. При `retail_only=true` соответствие
доказывают exact query predicate, bound boolean parameter и возвращенный fact;
при примененном `department` — exact parameter/result equality. Ни один из этих
условных фильтров не помещается в статический `role_proof_fact_ids`.

Cardinality `many`, keyset обязателен. Фильтры organization и inventory purpose
запрещены: в `Справочник.Склады` они не доказаны direct attributes. Связь с
организацией допускается только отдельным skill с exact join proof, а
назначение запасов разрешает `R12`.

Примеры: «Покажи все розничные склады» (`display_only`), «Сколько товара на
складе `<наименование>`?» (`select_one`), «Остатки на розничных складах»
(`select_set`). В `select_one` несколько retail warehouses требуют выбора; в
явном множественном вопросе те же rows не являются неоднозначностью.

### 5.6. R07 — кассы двух видов

Один union resolver с меняющимся semantic type запрещен. `R07` состоит из:

| Skill | Identity | Slot | Hard proof |
| --- | --- | --- | --- |
| `ut115.ref.cash-desk.enterprise.resolve` | `cash_desk.ref:finance.cash_desk.enterprise` | `selection.cash_desk.enterprise` | `cash_desk.kind=enterprise`, owner равен organization input |
| `ut115.ref.cash-desk.pos.resolve` | `cash_desk.ref:finance.cash_desk.pos` | `selection.cash_desk.pos` | `cash_desk.kind=pos`, owner равен organization input |

Оба принимают required `organization:entity_ref/party.organization` из
`selection.organization` и optional `name_fragment:normalized_text`. POS skill
может дополнительно вернуть warehouse, но не использовать его вместо owner
organization.

Обязательные facts каждого resolver-а:
`cash_desk.ref:finance.cash_desk.enterprise/entity_ref/entity !` в enterprise
skill или `cash_desk.ref:finance.cash_desk.pos/entity_ref/entity !` в POS skill,
`cash_desk.name:finance.cash_desk.name/string/attribute !`,
`cash_desk.kind:finance.cash_desk.kind/enum/attribute !`,
`cash_desk.organization:party.organization/entity_ref/dimension !` с
`fact_equals_parameter`,
`cash_desk.organization_name:party.organization.name/string/dimension !`,
`cash_desk.currency:currency.ref/entity_ref/dimension !` и
`cash_desk.currency_code:currency.code/string/dimension !`; для POS также
`cash_desk.pos_type:finance.cash_desk.pos_type/string/attribute ?` и
`cash_desk.warehouse:catalog.warehouse/entity_ref/dimension ?`.
`cash_desk.kind` имеет один разрешенный fact value: `enterprise` в enterprise
skill и `pos` в POS skill. Candidate labels: kind/name/organization name/
currency code; `role_proof_fact_ids=[cash_desk.kind]`.

Каждый producer имеет cardinality `many`. «Все кассы организации» запускает
оба skills как два `display_only` набора и показывает виды раздельно; разные
semantic types нельзя слить в один selected set. Для единичной кассы сначала
уточняется organization, затем kind, затем конкретный кандидат — по одному
вопросу за раз. Примеры: «Какие кассы относятся к организации
`<организация>`?», «Покажи автономные ККМ организации `<организация>`».

### 5.7. R08 — вид цены

Skill: `ut115.ref.price-type.resolve`.

Входы: optional `name_fragment:normalized_text`; optional typed booleans
`retail_use_only` и `wholesale_use_only`, каждый связывается с direct standard
UT flags `ИспользоватьПриРозничнойПродаже` и
`ИспользоватьПриОптовойПродаже`. Никакой фильтр не сравнивает пользовательское
слово с presentation перечисления. Пустой name и оба false допустимы для
получения universe видов цен перед уточнением.

Candidate facts:

- `price_type.ref:catalog.price_type/entity_ref/entity !`;
- `price_type.name:catalog.price_type.name/string/attribute !`;
- `price_type.purpose:catalog.price_type.purpose/string/attribute !`;
- `price_type.currency:currency.ref/entity_ref/dimension !`;
- `price_type.currency_code:currency.code/string/dimension !`;
- `price_type.includes_vat:catalog.price_type.includes_vat/boolean/attribute !`;
- `price_type.for_retail:catalog.price_type.for_retail/boolean/attribute !`;
- `price_type.for_wholesale:catalog.price_type.for_wholesale/boolean/attribute !`.

Identity `price_type.ref`; labels name/purpose/currency code; slot
`selection.price_type`; `role_proof_fact_ids=[]`; cardinality `many`. Если
пользователь сказал только «цена» и доступно несколько видов, создается pending
clarification. Выбор по
порядку или похожему названию запрещен. Примеры: «Покажи розничные цены на
`<товар>`», «Сравни виды цен `<вид 1>` и `<вид 2>`», «Цены товаров группы
`<группа>`» — последний вопрос при нескольких видах требует выбора с
сохранением group slot.

### 5.8. R09 — собственная организация

Logical `R09` публикуется тремя atomic resolver-ами:

```text
ut115.ref.organization.resolve-name-contains
ut115.ref.organization.resolve-inn-exact
ut115.ref.organization.resolve-kpp-exact
```

Вход — ровно один required criterion: `name_fragment`, `inn` или `kpp`.
`Справочник.Организации` имеет `CodeLength=0`, поэтому поля
`organization_code` и факта `organization.code` в этом семействе нет. Это
подтверждено metadata source `Catalogs/Организации.xml`, SHA-256
`90c61aca0b35ac7f573685956b6c815736c5ab405483b765a4dcbfc58388c875`.

Facts: `organization.ref:party.organization/entity_ref/entity !`,
`organization.name:party.organization.name/string/attribute !`,
`organization.full_name:party.organization.full_name/string/attribute ?`,
`organization.inn:party.organization.inn/string/attribute ?`,
`organization.kpp:party.organization.kpp/string/attribute ?`,
`organization.is_own:party.organization.is_own/boolean/attribute !` с exact
value `true`. В `resolve-inn-exact` fact `organization.inn` required/non-null и
входит в candidate labels; в `resolve-kpp-exact` тем же правилам удовлетворяет
`organization.kpp`.
`role_proof_fact_ids=[organization.is_own]`; identity — organization ref,
labels — name и required exact INN/KPP соответствующего variant; slot —
`selection.organization`.

Cardinality `many`. Совпавший partner/contractor не является кандидатом этого
resolver-а. Примеры: «Покажи кассы организации `<наименование>`», «Найди нашу
организацию по ИНН `<ИНН>`», «Остатки в кассах организации» — в последнем
случае отсутствие критерия приводит к вопросу об организации, не к поиску всех
партнеров.

### 5.9. R10 — характеристика номенклатуры

Skill: `ut115.ref.item-characteristic.resolve-name-contains`.

Входы: required `item:entity_ref/catalog.item` из `selection.item` и required
`characteristic_text:normalized_text` из user slot. Resolver учитывает
стандартную модель владельца характеристик выбранной номенклатуры; совпадение
только по названию без item binding недопустимо.

У `item` разрешены только sources `previous_step|session_context` и
`context_slot_keys=[selection.item]`; у text criterion — `user_slot` и пустой
slot allowlist.

Facts: `characteristic.ref:catalog.item.characteristic/entity_ref/entity !`,
`characteristic.name:catalog.item.characteristic.name/string/attribute !`,
`characteristic.item:catalog.item/entity_ref/dimension !` с equality к input,
`characteristic.item_name:catalog.item.name/string/dimension !`,
`characteristic.applies_to_item:catalog.item.characteristic.applies_to_item/boolean/attribute !`
с exact value `true`. Последний факт входит в
`role_proof_fact_ids`. Identity — characteristic ref;
`candidate_label_fact_ids=[characteristic.name,characteristic.item_name]`;
slot — `selection.item_characteristic`; cardinality `many`.

Примеры: «Остаток `<товар>`, характеристика `<характеристика>`», «Выбери размер
`<значение>` для этого товара». Характеристика не спрашивается автоматически,
если ее отсутствие не меняет точность ответа. `N>1` при существенной
характеристике требует выбора.

### 5.10. R11 — серия номенклатуры

Logical `R11` имеет две atomic variations:

- `ut115.ref.item-series.resolve-name-contains`;
- `ut115.ref.item-series.resolve-number-exact`.

Обе принимают required `item:entity_ref/catalog.item` из `selection.item` и
required series criterion. Optional exact context inputs:
`characteristic:catalog.item.characteristic`,
`warehouse:catalog.warehouse` для координаты места хранения и
`inventory_purpose:inventory.purpose`. Свободный текст не может заменить ни
одну из этих ссылок.

Все entity inputs допускают только `previous_step|session_context` и exact
slots: `selection.item`, `selection.item_characteristic`,
`selection.warehouse`, `selection.inventory_purpose` соответственно. Series
criterion приходит из `user_slot` и имеет `context_slot_keys=[]`.

Единственное допустимое доказательство связи в локальной standard UT — строка
`РегистрСведений.АналитикаУчетаНоменклатуры`, удовлетворяющая exact item и
примененным optional filters. Для proof используются измерения
`Номенклатура`, `Характеристика`, `Серия`, `МестоХранения`, `Назначение`.
Metadata source:
`InformationRegisters/АналитикаУчетаНоменклатуры.xml`, SHA-256
`4f88fd400e114ac936b34c710bee40efb7abe4a9f8c833b2a32619e90e52880e`.
Каталожные признаки владельца или вида сами по себе не являются relation proof
и не должны подменять строку указанного регистра в query, provenance
assertions или tests `R11`.

`МестоХранения` имеет составной physical type. Поэтому optional `warehouse`
разрешен только как exact `catalog.warehouse` filter с proved parameter binding;
он не ретегирует остальные возможные значения измерения и не создает generic
storage-place entity fact.

Facts: `series.ref:catalog.item.series/entity_ref/entity !`,
`series.name:catalog.item.series.name/string/attribute !`,
`series.number:catalog.item.series.number/string/attribute ?`,
`series.expiration_date:catalog.item.series.expiration_date/date/attribute ?`,
`series.production_date:catalog.item.series.production_date/date/attribute ?`,
`series.item:catalog.item/entity_ref/dimension !` с equality к input,
`series.item_name:catalog.item.name/string/dimension !`,
`series.characteristic:catalog.item.characteristic/entity_ref/dimension ?`,
`series.characteristic_name:catalog.item.characteristic.name/string/dimension ?`,
`series.storage_place_presentation:inventory.storage_place.presentation/string/dimension ?`,
`series.inventory_purpose:inventory.purpose/entity_ref/dimension ?` и
`series.analytics_match:catalog.item.series.analytics_match/boolean/attribute !`
с exact value `true`. Последний факт означает наличие строки регистра по exact
координате, а не эвристику применимости. При bound `characteristic` или
`inventory_purpose` соответствующий output fact становится required/non-null и
получает exact equality constraint к параметру. Identity — series ref;
`candidate_label_fact_ids=[series.name,series.item_name]`; в number-exact
variant в него также входит required/non-null `series.number`.
Characteristic, expiration date и место хранения являются optional display
facts, а не nullable candidate label facts.
`role_proof_fact_ids=[series.analytics_match]`; slot —
`selection.item_series`; cardinality `many`.

Примеры: «Остаток `<товар>` серии `<номер>`», «Покажи данные по этой серии
товара». Серия не подменяет item или characteristic и уточняется только когда
существенна для результата.

### 5.11. R12 — назначение запасов

Skill: `ut115.ref.inventory-purpose.resolve-name-contains`.

Вход: required `purpose_text:normalized_text`. Facts:

- `purpose.ref:inventory.purpose/entity_ref/entity !`;
- `purpose.name:inventory.purpose.name/string/attribute !`;
- `purpose.type:inventory.purpose.type/string/attribute !`;
- `purpose.partner_name:party.partner.name/string/dimension ?`;
- `purpose.contract_presentation:party.contract.presentation/string/dimension ?`;
- `purpose.order_presentation:document.order.presentation/string/dimension ?`;
- `purpose.business_direction_name:business.direction.name/string/dimension ?`.

Связанные поля выводятся как безопасные presentation, потому что standard UT
может хранить в них полиморфные ссылки. Они не создают entity facts и не могут
стать context slots без отдельного producer-а с exact semantic/physical proof.

Identity — purpose ref; `candidate_label_fact_ids=[purpose.name,purpose.type]`;
безопасные presentation связанных измерений являются optional display facts;
`role_proof_fact_ids=[]`; slot — `selection.inventory_purpose`;
cardinality `many`. Примеры: «Покажи запас по назначению `<назначение>`»,
«Остаток этого товара для выбранного назначения». Назначение запасов не
является назначением, организацией или типом склада; `R06` его не разрешает.

## 6. Resolver-capable producers документов

Эти skills не относятся к `R02-R12`, но обязательны для полного реестра
slice 3. Их list/header facts могут участвовать в selection только через тот же
protocol и `SelectionProof`.

| Producer | Identity / slot | Typed search inputs | Required candidate facts | Clarification/selection |
| --- | --- | --- | --- | --- |
| Новая версия `ut115.sales.order-header-status-by-number` | `order.ref:document.sales_order`; `selection.sales_order` | required exact number; optional date/customer/organization only as declared typed filters | ref, number, date, customer ref+name, organization ref+name, warehouse ref+name, status, amount, currency | Cardinality меняется с `zero_or_one` на `many`; duplicate number дает выбор, exact choice feeds lines/status/header |
| `ut115.sales.shipment-list` | `shipment.ref:document.sales_shipment`; `selection.sales_shipment` | period, optional customer/organization/warehouse/status/number | ref, number, date, customer, organization, warehouse, status, amount, currency | List is `display_only`; «последняя» выбирается доказанным rank-one, не первым row |
| `ut115.purchase.receipt-list` | `receipt.ref:document.purchase_receipt`; `selection.purchase_receipt` | period/number, optional supplier/organization | ref, number, date, supplier ref+name, organization ref+name, amount, currency | Exact number uses `0/1/N`; «последнее» uses deterministic rank-one |
| `ut115.purchase.order-list` | `purchase_order.ref:document.purchase_order`; `selection.purchase_order` | period/number, optional supplier/organization/status/warehouse | ref, number, date, supplier, organization, warehouse, status | Order and receipt semantic types never mix |
| `ut115.logistics.transfer-list` | `transfer.ref:document.stock_transfer`; `selection.stock_transfer` | period/number, optional from/to warehouse/status | ref, number, date, from warehouse ref+name, to warehouse ref+name, status | Direction roles are separate typed inputs; unresolved direction is clarified |

Все пять producers имеют output cardinality `many`, identity in
`row_identity_fact_ids`, safe labels, `selected_only` policy и no automatic
export for ordinary list rows. Exact-ref consumers обязаны иметь
`fact_equals_parameter`/same-object result constraints. После выбора или
rank-one follow-up не выполняет повторный поиск по номеру, дате или
presentation.

Минимальные typed fact contracts этих producers:

- sales order: `order.ref:document.sales_order/entity_ref/entity`,
  `order.number:document.number/string/attribute`,
  `order.date:time.document_moment/datetime/time`,
  `order.customer:party.customer/entity_ref/dimension`,
  `order.customer_name:party.partner.name/string/dimension`,
  `order.organization:party.organization/entity_ref/dimension`,
  `order.organization_name:party.organization.name/string/dimension`,
  `order.warehouse:catalog.warehouse/entity_ref/dimension`,
  `order.warehouse_name:catalog.warehouse.name/string/dimension`,
  `order.status:document.sales_order.status/string/attribute`,
  `order.amount:measure.document_amount/money/measure` и
  `order.currency:currency.code/string/dimension`;
- shipment: `shipment.ref:document.sales_shipment/entity_ref/entity`,
  `shipment.number:document.number/string/attribute`,
  `shipment.date:time.document_moment/datetime/time`,
  `shipment.customer:party.customer/entity_ref/dimension`,
  `shipment.customer_name:party.partner.name/string/dimension`,
  `shipment.organization:party.organization/entity_ref/dimension`,
  `shipment.organization_name:party.organization.name/string/dimension`,
  `shipment.warehouse:catalog.warehouse/entity_ref/dimension`,
  `shipment.warehouse_name:catalog.warehouse.name/string/dimension`,
  `shipment.status:document.sales_shipment.status/string/attribute`,
  `shipment.amount:measure.document_amount/money/measure` и
  `shipment.currency:currency.code/string/dimension`;
- purchase receipt: `receipt.ref:document.purchase_receipt/entity_ref/entity`,
  `receipt.number:document.number/string/attribute`,
  `receipt.date:time.document_moment/datetime/time`,
  `receipt.supplier:party.supplier/entity_ref/dimension`,
  `receipt.supplier_name:party.partner.name/string/dimension`,
  `receipt.organization:party.organization/entity_ref/dimension`,
  `receipt.organization_name:party.organization.name/string/dimension`,
  `receipt.amount:measure.document_amount/money/measure` и
  `receipt.currency:currency.code/string/dimension`;
- purchase order: `purchase_order.ref:document.purchase_order/entity_ref/entity`,
  `purchase_order.number:document.number/string/attribute`,
  `purchase_order.date:time.document_moment/datetime/time`,
  `purchase_order.supplier:party.supplier/entity_ref/dimension`,
  `purchase_order.supplier_name:party.partner.name/string/dimension`,
  `purchase_order.organization:party.organization/entity_ref/dimension`,
  `purchase_order.organization_name:party.organization.name/string/dimension`,
  `purchase_order.warehouse:catalog.warehouse/entity_ref/dimension`,
  `purchase_order.warehouse_name:catalog.warehouse.name/string/dimension` и
  `purchase_order.status:document.purchase_order.status/string/attribute`;
- transfer: `transfer.ref:document.stock_transfer/entity_ref/entity`,
  `transfer.number:document.number/string/attribute`,
  `transfer.date:time.document_moment/datetime/time`,
  `transfer.from_warehouse:catalog.warehouse/entity_ref/dimension`,
  `transfer.from_warehouse_name:catalog.warehouse.name/string/dimension`,
  `transfer.to_warehouse:catalog.warehouse/entity_ref/dimension`,
  `transfer.to_warehouse_name:catalog.warehouse.name/string/dimension` и
  `transfer.status:document.stock_transfer.status/string/attribute`.

Identity, number, date и все labels, используемые в clarification, являются
required/non-null. Остальные required facts определяются advertised product
capability; nullable факт не может быть единственным различием кандидатов.

## 7. Каталог уточнений

| Ситуация | Один допустимый вопрос | Что сохраняется |
| --- | --- | --- |
| Не указан тип одноименного объекта | «Что вы имеете в виду: собственную организацию, клиента или поставщика?» | Все уже подтвержденные несовпадающие slots |
| Партнер найден, но нужна роль | «Искать его как клиента или как поставщика?» | Исходный критерий и остальные условия |
| Несколько item/group candidates | «Какой товар выбрать?» / «Какую группу выбрать?» с различающими code/name | Ни один candidate; подтвержденные фильтры |
| Несколько складов для одного магазина | «Какой склад выбрать?» | Item, period/moment, metric |
| Явно запрошены все retail warehouses | Уточнение не задается | Complete selected set либо display list по downstream contract |
| Несколько организаций перед cash query | «Для какой организации показать кассы?» | Момент и вид результата |
| Не указан вид цены | «Какой вид цены использовать?» | Item или item-group, дата цены |
| Совпали касса предприятия и ККМ по названию | «Вы имеете в виду кассу предприятия или операционную кассу/ККМ?» | Organization и прочие фильтры |
| Один номер у нескольких документов нужного вида | «Какой документ выбрать?» с date/organization/party | Остальные slots; candidates только в pending |
| Характеристика/серия/назначение несущественны | Вопрос не задается | Явные параметры сохраняются, отсутствующие не выдумываются |
| Характеристика/серия/назначение меняют точность | Один вопрос о первом существенном параметре | Item, warehouse, time и другие подтвержденные условия |
| Более пяти candidates или incomplete page | «Уточните один критерий поиска» | Frozen bindings pinned resolver-а; visible rows не selectable |

## 8. Покрытие R02-R12

Frozen scope содержит ровно 27 concrete skill documents: 22 resolver-а и 5
exact/list consumers. Resolver-часть покрывает 12 semantic roles: item group,
partner, customer, supplier, warehouse, enterprise cash desk, POS cash desk,
price type, organization, item characteristic, item series и inventory purpose.
Item selection для `R02` поставляют отдельно учитываемые `R01A-R01D`.

| Ref | Реализация каталога | Product coverage | Corpus / slice-3 proof |
| --- | --- | --- | --- |
| `R02` | Exact `ut115.ref.item.details` после R01 selection | `CAP-REF-ITEM-DETAILS`, `CAP-COMMON-DETAIL` | `Q013`, `Q014`, `Q108`; same item ref, no detail-row export |
| `R03` | Два item-group resolver-а + `ut115.ref.item.group-members` | `CAP-REF-ITEM-GROUP` | `Q016`, `Q029`; group не item, descendants rule explicit |
| `R04` | Role-qualified partner/customer/supplier resolver families | `CAP-REF-PARTNER-FIND`, `CAP-COMMON-ENTITY` | `Q017`, `Q018`, `Q073`, `Q096-Q097`; hard role proof |
| `R05` | Partner/customer/supplier exact detail consumers | `CAP-REF-PARTNER-DETAILS`, `CAP-COMMON-DETAIL` | `Q017`, `Q073`, `Q097`; same selected party, contractor not substituted |
| `R06` | Schema-1.1 `ut115.ref.warehouse.resolve` | `CAP-REF-WAREHOUSE-FIND` | `Q019`, `Q056`, `Q091-Q093`; factual retail predicate when applied, set versus one, dynamic type facts не являются role proof |
| `R07` | Separate enterprise and POS resolver producers | `CAP-REF-CASH-DESK-FIND` | `Q020`, `Q081-Q082`; exact organization equality, kinds separate |
| `R08` | `ut115.ref.price-type.resolve` | `CAP-REF-PRICE-TYPE-FIND` | `Q021`, `Q024-Q030`; `Q029` pending resumes with group retained |
| `R09` | Organization name/INN/KPP resolver family; code forbidden by `CodeLength=0` | Support for cash/finance, no separate baseline capability ID | `Q020`, `Q081-Q082`; `party.organization`, no partner substitution |
| `R10` | Item-bound characteristic resolver | Typed optional filter support | Property/negative coverage `S3-NEG-010`; no separate intent class |
| `R11` | Analytics-bound series name/number resolver family | Typed optional filter support | Exact `АналитикаУчетаНоменклатуры` coordinate, pinned metadata hash; `S3-NEG-010` |
| `R12` | Inventory-purpose resolver | Typed optional stock/document filter support | Warehouse boundary `S3-NEG-011`; purpose never inferred by R06 |

`R09-R12` остаются support skills без новых product capability IDs. Их наличие
не увеличивает baseline 87 и не создает новые классы пользовательских вопросов.

## 9. Запрет core-классификаторов

Реализация каталога не должна добавлять в application/core:

- словари названий товаров, групп, партнеров, складов, касс, видов цен,
  организаций, характеристик, серий или назначений;
- списки русских слов, префиксов номеров или Q-ID для выбора resolver-а;
- map `semantic_type -> ТипОбъекта`, `slot_key -> class` или prefix inference;
- retagging `party.partner` в customer/supplier по тексту вопроса;
- retagging cash-desk ref по physical type или presentation;
- fallback-поиск по presentation при невалидном handle.

Разрешены отдельные atomic query skills по exact article/code/barcode/name,
фиксированные standard metadata predicates роли и вида, portable
`display/selection/examples/anti_examples`, typed planner requirements и
consumer contracts. Их интерпретирует общий protocol. Synthetic resolver с
неизвестными словами, semantic type и slot должен работать после импорта без
изменения source.

## 10. Definition of done каталога

Каталог `R02-R12` готов к приемке, когда одновременно выполнено следующее:

1. Каждый resolver проходит strict skill schema `1.1.0`; каждый package —
   package schema `1.1.0`, digest и dependency lock.
2. Для каждого resolver instance есть fixture cases `0`, `1`, `2..5`, `>5` и
   source error; для role-qualified skills — wrong-role case.
3. Для каждого exact-ref consumer проверены source provenance, exact semantic
   и physical type, accepted slot, cardinality и same-object result constraint
   до MCP.
4. Unbounded producers используют доказанный keyset. Ни одна visible page не
   сохраняется как complete selected set.
5. Ambiguous, display-only, partial, empty и error outcomes имеют
   `context_exports=[]`; selected one/set экспортируют только identity facts
   из `SelectionProof`.
6. `R02`, `R03 members` и `R05` не экспортируют detail/list entity rows как
   новый selection.
7. Clarification сохраняется, переживает restart, является одноразовым и после
   `choose` продолжает исходный plan без второго resolver search.
8. `party.organization` используется одинаково в `R07`, document producers и
   cash consumers; legacy `catalog.organization` не принимается как alias.
9. Примеры и fixtures используют только явно синтетические значения; ни одно
   значение не влияет на skill identity, query template или fixed runtime
   binding.
10. Exit matrix slice 3 и synthetic portability suite подтверждают отсутствие
    object-specific и lexical branches в core.
