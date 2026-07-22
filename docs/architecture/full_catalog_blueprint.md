# Blueprint полного каталога и slices 2-6

## 1. Назначение и нормативная база

Этот документ является implementation handoff для срезов 2-6 и поставляемого
каталога УТ 11.5.27.56. Он конкретизирует, но не заменяет
`architecture.md`, `request_lifecycle.md`, `skill_contract.md`,
`integration_contracts.md`, `implementation_slices.md` и ADR-0001..0003.
Acceptance baseline: все 87 `CAP-*` и корпус `Q001-Q116`.

Локальный корень proof-источника:
`/Users/repnikov/Documents/УТ_Демо/config`. В переносимых JSON он никогда не
записывается; используется URI вида
`ut-config://11.5.27.56/<relative-path>#<anchor>`. `Configuration.xml`
подтверждает `УправлениеТорговлейБазовая`, версию `11.5.27.56`; наблюдавшийся
SHA-256 файла: `5d99174d849c48b24153f5e444966c52f5a84788aa7218f4a4767cd36b008003`.

## 2. Неподвижные границы реализации

1. В application code нет ветвей по Q-ID, capability ID, товару, складу,
   номеру документа или иному значению контрольной базы.
2. Product capability является обещанием и меткой приемки, а не handler/class.
3. Atomic skill содержит одну фиксированную read-only operation с одним
   семантическим output contract. Composite является только валидированным DAG
   текущего turn и не сохраняется как новый skill.
4. DeepSeek возвращает только typed interpretation/plan/clarification. Query
   text, metadata payload, raw MCP и полный `EntityRef` в prompt отсутствуют.
5. Все business-instance values поступают только через typed parameters или
   server-side `ContextBinding`. Константы допустимы только по ADR-0003.
6. Core доказывает final fact coverage до MCP и после нормализации evidence по
   semantic type, value type, cardinality, identity, unit/currency и time.
7. Detail, grouping, rank и filter не меняют семантику исходного факта. Нельзя
   использовать revenue вместо profit, document amount вместо debt или
   on-hand balance вместо available/expected quantity.
8. Каждый turn использует pinned context/catalog/docs/profile/database-marker
   tuple. Hot reload влияет только на следующий turn.
9. Data skills используют только `mcp.execute_query`; documentation skills -
   только pinned built-in-help index. `get_metadata` используется для profile и
   compatibility proof, но не как произвольный пользовательский data resolver.
10. Поставляемые skills проходят schema, semantic lint, exact lock/digest,
    fixture, live positive/negative и independent-oracle gates.
11. Нормальная identity ссылочного факта равна
    `(semantic entity type, ТипОбъекта, UUID)`. Presentation и прочие display
    fields не участвуют в identity, если конкретный output contract отдельно не
    объявляет их проверяемыми semantic facts. Полный исходный ref сохраняется и
    без реконструкции передается как MCP parameter.
12. Допустимый `ТипОбъекта` доказывается exact producer
    `fact_id -> column_binding.accepted_mcp_types` и origin evidence. Application
    не содержит map `semantic_type -> ТипОбъекта`; raw EntityRef из LLM/slot не
    принимается.

Static release gate сканирует `src/` на `Q[0-9]{3}` и на значения corpus
`question`/fixture business fields. Совпадение запрещено, кроме текстов
диагностических кодов, перечисленных в allowlist. Отдельный scanner проверяет,
что JSON skills не содержат concrete corpus values вне явно синтетических
examples/fixtures.

## 3. Малое число общих механизмов

| ID | Механизм | Capability coverage | Инвариант |
| --- | --- | --- | --- |
| M01 | Dialogue policy | clarify, out-of-scope, read-only | Решение typed; никакого MCP при отказе/уточнении |
| M02 | Context ledger | follow-up и замена условий | Полный ref хранится server-side, LLM видит opaque handle |
| M03 | Outcome classifier/renderer | not-found, dependency error, empty/error/partial | Outcome определяется transport/contract/evidence, не текстом ответа |
| M04 | `normalize_period` | явные/относительные даты | Один pinned turn time/timezone; оборот `[from,to)`, остаток `as_of` |
| M05 | Typed entity-resolution protocol | common entity/detail | Resolver возвращает exact ref или candidates; semantic/physical type доказаны producer binding и provenance, presentation не identity |
| M06 | `count_distinct` и `aggregate` | count/sum/avg/min/max | Operand semantic type, distinct key, collection scope, unit и denominator обязательны; page count не является total |
| M07 | `rank` и sign/null filter | top/bottom/zero/positive/negative | Stable tie key; направление и measure входят в result contract |
| M08 | typed equijoin и `calculate` | compare/VAT/profit/document link | Join только по declared typed coordinates; unit/currency guards |
| M09 | group/timeline renderer | group by entity/calendar grain | Grain typed; группировка не меняет measure meaning |
| M10 | Help retrieval/citation/disagreement | doc search/source | Только built-in help; каждый claim имеет chunk/citation provenance |

Planner может ссылаться только на allowlisted M04, M06-M09. M01-M03, M05 и
M10 являются domain/application mechanisms, а не модельными operators.

## 4. Переносимый каталог: 57 atomic skills

Ниже `R`, `PR`, `SP`, `SL`, `SE`, `CF`, `D` - стабильные shorthand этого
blueprint. Реальный `skill_id` указан полностью. Все data skills имеют target
`УправлениеТорговлейБазовая/11.5.27.56/8.3.27`, fixed query package, exact
bindings и immutable digest.

### 4.1. Built-in help package, 4 skills

| Ref | `skill_id` | Role/output | Capabilities |
| --- | --- | --- | --- |
| D01 | `ut115.doc.term` | definition, restriction, citation | `CAP-DOC-TERM` |
| D02 | `ut115.doc.procedure` | procedure, prerequisite, restriction, navigation, citation | `CAP-DOC-PROCEDURE` |
| D03 | `ut115.doc.error` | error cause, verification action, restriction, citation | `CAP-DOC-ERROR` |
| D04 | `ut115.doc.status` | status meaning, restriction, citation | `CAP-DOC-STATUS` |

`CAP-DOC-SEARCH` реализуется retrieval operation этих skills, а
`CAP-DOC-SOURCE` - обязательным citation binding и M10. Отдельный
`doc.source` skill не создается: он повторил бы тот же retrieval и не имеет
отдельного chunk role в schema v1.

### 4.2. Reference package, 15 skills

| Ref | `skill_id` | Required/optional typed inputs | Stable output |
| --- | --- | --- | --- |
| R01A | `ut115.ref.item.resolve-article-exact` | article; trim then exact `=` | item ref, `article_exact`, identifying fields |
| R01B | `ut115.ref.item.resolve-code-exact` | catalog code; trim then exact `=` | item ref, `code_exact`, identifying fields |
| R01C | `ut115.ref.item.resolve-barcode-exact` | barcode; trim then exact `=` in barcode register | item ref, `barcode_exact`, identifying fields |
| R01D | `ut115.ref.item.resolve-name-contains` | name fragment; `like_contains` encoding; limit/cursor | item ref, `name_contains`, identifying fields |
| R02 | `ut115.ref.item.details` | exact item ref | safe fixed detail projection, units, barcodes |
| R03 | `ut115.ref.item.group-members` | group ref or resolvable group name; include descendants | item refs with group relation |
| R04 | `ut115.ref.partner.resolve` | name/code/INN; role customer/supplier | partner and related contractor refs, proved role |
| R05 | `ut115.ref.partner.details` | exact partner ref | contractor, INN, KPP, legal address, contacts |
| R06 | `ut115.ref.warehouse.resolve` | name/type/department | warehouse ref plus matched name, `ТипСклада` and optional `Подразделение` |
| R07 | `ut115.ref.cash-desk.resolve` | organization, name, kind enterprise/POS | typed union of enterprise cash desk and KKM refs |
| R08 | `ut115.ref.price-type.resolve` | name/purpose | price-type ref, currency/VAT semantics |
| R09 | `ut115.ref.organization.resolve` | name/code/INN | organization ref and identifying fields |
| R10 | `ut115.ref.item-characteristic.resolve` | item ref, characteristic text | characteristic ref |
| R11 | `ut115.ref.item-series.resolve` | item ref, series text | series ref |
| R12 | `ut115.ref.inventory-purpose.resolve` | purpose text | inventory-purpose ref |

R09-R12 are support skills without separate product capability IDs. They keep
organization, characteristic, series and purpose as typed parameters rather
than new question classes or application branches.

R01A-D form one logical item resolver capability but are four physical atomic
skills because the closed query contract has one fixed parameter binding per
operation. R01A/R01B/R01C use equality and can never degrade to a text
prefix/contains predicate; this does not imply bounded pagination cardinality.
R01D alone declares parameter normalization and binding encoding
`like_contains`; the generic codec escapes wildcard characters before adding
the contains pattern. No discriminator, OR branch or application-selected query
text is used. Q011 and Q102 may select only R01A.

R06 v1 использует только metadata-proven direct warehouse attributes:
standard ref/name, `ТипСклада` и optional `Подразделение` из
`Catalogs/Склады.xml`. В этом XML нет direct attributes `Организация` или
`Назначение`; choice parameter с текстом `Назначение` и union attribute
`Поклажедержатель` не доказывают такие business filters. Поэтому R06 не
принимает и не возвращает organization/purpose. Organization criterion может
появиться только в отдельной composition/query с exact proof ledger join;
inventory purpose разрешается через R12 и proved stock/document dimensions.
Пока такого join proof нет, planner уточняет конкретный склад либо сообщает
capability gap, но не выводит связь по названию.

### 4.3. Pricing package, 4 skills

| Ref | `skill_id` | Semantics | Capabilities |
| --- | --- | --- | --- |
| PR01 | `ut115.price.current` | last effective price at `as_of`, by item/characteristic/price type | `CAP-PRICE-CURRENT` |
| PR02 | `ut115.price.history` | price change events within `[from,to)` | `CAP-PRICE-HISTORY` |
| PR03 | `ut115.price.last-purchase` | latest actual purchase price and source document | `CAP-PRICE-LAST-PURCHASE` |
| PR04 | `ut115.price.missing` | item universe anti-joined to nonzero effective price | `CAP-PRICE-MISSING` |

VAT representation and comparison are M08 over price evidence, not queries
with new product values.

### 4.4. Sales and purchases package, 16 skills

| Ref | `skill_id` | Semantics | Capabilities |
| --- | --- | --- | --- |
| SP01 | `ut115.sales.order-header-status-by-number` | unique order ref, number/date, customer, organization, warehouse, status, amount/currency and execution indicators by typed number | `CAP-SALES-ORDER-HEADER`, `CAP-SALES-ORDER-STATUS` |
| SP02 | `ut115.sales.order-list` | order documents with filters, stable paging | `CAP-SALES-ORDER-LIST` |
| SP03 | `ut115.sales.order-lines` | lines of exact order ref | `CAP-SALES-ORDER-LINES` |
| SP04 | `ut115.sales.shipment-list` | keyset-paged shipment documents with amount/status and refs; no total aggregate | `CAP-SALES-SHIPMENT-LIST` |
| SP05 | `ut115.sales.shipment-lines` | lines of exact shipment ref | `CAP-SALES-SHIPMENT-LINES` |
| SP06 | `ut115.sales.performance` | distinct shipment-document total, quantity, revenue, cost and gross profit on identical period/filter coordinates | `CAP-SALES-TURNOVER`, `CAP-SALES-PROFIT`, `CAP-FIN-REVENUE`, `CAP-FIN-COST`, `CAP-FIN-PROFIT` |
| SP07 | `ut115.sales.return` | customer-return documents/items/quantity/amount | `CAP-SALES-RETURN` |
| SP08 | `ut115.sales.customer-history` | shipment/item facts retaining selected customer identity | `CAP-CUSTOMER-SALES-HISTORY` |
| SP09 | `ut115.purchase.order-list` | supplier orders with filters and refs | `CAP-PURCHASE-ORDER-LIST` |
| SP10 | `ut115.purchase.order-status` | status/execution of exact supplier-order ref | `CAP-PURCHASE-ORDER-STATUS` |
| SP11 | `ut115.purchase.receipt-list` | receipt documents with amount and refs | `CAP-PURCHASE-RECEIPT-LIST` |
| SP12 | `ut115.purchase.receipt-header` | header of exact receipt ref | `CAP-PURCHASE-RECEIPT-HEADER` |
| SP13 | `ut115.purchase.receipt-lines` | lines of exact receipt ref | `CAP-PURCHASE-RECEIPT-LINES` |
| SP14 | `ut115.purchase.turnover` | purchased quantity/amount by period and dimensions | `CAP-PURCHASE-TURNOVER` |
| SP15 | `ut115.purchase.return` | supplier-return documents/items/quantity/amount | `CAP-PURCHASE-RETURN` |
| SP16 | `ut115.supply.expected` | not-yet-received quantity and active order grounds | `CAP-PURCHASE-EXPECTED`, `CAP-STOCK-EXPECTED` |

SP01 is the accepted minimal correction for Q036/Q037 and Q111. It is not an
application resolver. Zero rows export no handle; multiple orders produce
clarification; exactly one exports the exact order ref. SP03 accepts only that
ref for Q037 and does not search again by number or presentation. SP01 may claim
`CAP-SALES-ORDER-HEADER` only when its required fact set contains order ref,
number, date, customer ref, organization ref, warehouse ref, status, amount and
currency (or the contract's explicit unresolved-currency fact). A status-only
projection is insufficient for Q111 and cannot advertise the header capability.

SP04 produces one visible keyset page and continuation only. Its row count and
MCP envelope `count` are page-scoped. Full Q031 combines SP04 with the separate
SP06 aggregate producer, whose distinct document count must use the same
normalized period/filter fingerprint and pinned database marker. A generic M06
count over an SP04 StepResult cannot replace SP06 and cannot be labeled total.

### 4.5. Stock and logistics package, 10 skills

| Ref | `skill_id` | Semantics | Capabilities |
| --- | --- | --- | --- |
| SL01 | `ut115.stock.balance` | on-hand balance at `as_of`, item/warehouse dimensions | `CAP-STOCK-BALANCE`, `CAP-STOCK-BY-WAREHOUSE`, `CAP-STOCK-BY-ITEM` |
| SL02 | `ut115.stock.availability` | on-hand, reserved and available on one coordinate/time | `CAP-STOCK-AVAILABLE`, `CAP-STOCK-RESERVED` |
| SL03 | `ut115.stock.movement` | receipt/consumption turnover and registrar documents | `CAP-STOCK-MOVEMENT`, `CAP-STOCK-CONSUMPTION` |
| SL04 | `ut115.stock.deficit` | scoped item universe anti-joined/filtered by balance threshold | `CAP-STOCK-DEFICIT` |
| SL05 | `ut115.logistics.transfer-list` | transfers by period/number/from/to/status | `CAP-MOVE-LIST`, `CAP-MOVE-DIRECTION` |
| SL06 | `ut115.logistics.transfer-status` | status/execution of exact transfer ref | `CAP-MOVE-STATUS` |
| SL07 | `ut115.logistics.transfer-lines` | lines of exact transfer ref | `CAP-MOVE-LINES` |
| SL08 | `ut115.logistics.inventory-result` | accounting/factual quantities and difference | `CAP-INVENTORY-RESULT` |
| SL09 | `ut115.logistics.internal-consumption` | internal-consumption documents/items/quantity | `CAP-INTERNAL-CONSUMPTION` |
| SL10 | `ut115.logistics.delivery-status-date` | planned/actual date, delivered/open indicators and grounds | `CAP-DELIVERY-STATUS`, `CAP-DELIVERY-DATE` |

### 4.6. Settlements package, 5 skills

| Ref | `skill_id` | Semantics | Capabilities |
| --- | --- | --- | --- |
| SE01 | `ut115.settlement.receivable` | customer debt at `as_of`, optional object/document grain | `CAP-SETTLEMENT-AR`, `CAP-SETTLEMENT-DETAIL` |
| SE02 | `ut115.settlement.payable` | supplier debt at `as_of`, optional object/document grain | `CAP-SETTLEMENT-AP`, `CAP-SETTLEMENT-DETAIL` |
| SE03 | `ut115.settlement.overdue` | debt with proved due date before `as_of` | `CAP-SETTLEMENT-OVERDUE` |
| SE04 | `ut115.settlement.by-document` | actual debt linked to exact receipt/shipment ref | `CAP-SETTLEMENT-BY-DOCUMENT` |
| SE05 | `ut115.customer.no-activity` | customer universe anti-joined to sales in period | `CAP-CUSTOMER-NO-ACTIVITY` |

### 4.7. Cash and finance package, 3 skills

| Ref | `skill_id` | Semantics | Capabilities |
| --- | --- | --- | --- |
| CF01 | `ut115.cash.balance` | enterprise cash and POS balance, separated by kind/currency | `CAP-CASH-BALANCE` |
| CF02 | `ut115.cash.bank-balance` | bank-account balance by organization/currency | `CAP-CASH-BANK-BALANCE` |
| CF03 | `ut115.cash.flow` | cash receipts, expenses and net movement in one period/grain | `CAP-CASH-RECEIPTS`, `CAP-CASH-EXPENSES`, `CAP-CASH-FLOW` |

Finance capabilities use SP06 because revenue/cost/profit are sales-result
facts, not cash-flow facts. Package ownership does not alter their semantics.

## 5. Parameterization versus separate semantics

| Decision | One parameterized skill | Must stay separate |
| --- | --- | --- |
| Stock detail | SL01 changes declared output grain item/warehouse; same balance fact | SL02 availability/reserve, SP16 expected supply and SL03 period consumption are different facts |
| Sales measures | SP06 emits quantity, revenue, cost and gross profit on identical coordinates | Facts have distinct semantic types; renderer/coverage may never substitute one for another |
| Settlement views | SE01/SE02 can group the same debt by partner/object/document; rank is M07 | Receivable and payable remain separate because role, sign and source registers differ; document amount is not debt |
| Cash movement | CF03 may output receipt/expense/net and group by month/article | CF01/CF02 are moment balances, not period flows |
| Supply | SP16 serves purchase and stock viewpoints for the same expected quantity/ground | It is not current stock, availability or already received quantity |
| Price | Item/characteristic/price type/date are parameters | current, history, last purchase and missing-price complement have different time/source/cardinality semantics |
| Documents | period, party, organization, status, source/destination and limit are parameters | list, header, lines and status are separate cardinality/identity contracts; SP01 combines only the accepted coherent order header/status lookup |
| Logistics | source/destination in SL05 are parameters | transfer status/lines, inventory result, consumption and delivery are different document/fact semantics |
| Item dimensions | characteristic, series and purpose are typed optional refs | They do not create capability IDs, intent classes or application branches |
| Documentation | path prefixes and roles are declarative per D01-D04 | Documentation evidence never joins data evidence as if it were a data fact |

## 6. Canonical composite plans, not stored skills

| Composite | DAG shape | Corpus/oracle focus |
| --- | --- | --- |
| C01 entity detail | resolver -> exact ref -> detail | Q013-Q014, Q017, Q073, Q097, Q108 |
| C02 current price | one R01A-D resolver + R08 -> PR01 | Q021, Q028-Q030 |
| C03 price transform/compare | PR03 -> VAT calculate; or two PR01 calls -> typed compare | Q023-Q024 |
| C04 document follow-up | list/rank -> exact document ref -> header/lines | Q037, Q041-Q043, Q094-Q095, Q114 |
| C05 order status/lines | SP01 -> context export -> SP03 in later turn | Q036-Q037; bound full ref is preserved, result identity uses semantic type/object type/UUID |
| C06 sales/purchase ranking | fact/list -> M09 group -> M07 rank | Q035, Q038-Q039, Q045, Q050, Q110 |
| C07 stock query | one R01A-D resolver/R06 -> SL01/SL02/SL03 -> group/aggregate/rank | Q051-Q057, Q059-Q060, Q091-Q093, Q115 |
| C08 expected supply | one R01A-D resolver/R06 -> SP16 | Q047, Q116 |
| C09 debtor detail | SE01 -> rank -> partner ref -> R05 | Q072-Q074, Q096-Q097 |
| C10 debt by delivery document | SP11/SP04 -> rank -> SE04 | Q076-Q077; document sum and debt remain two facts |
| C11 delivery | SP01 order ref -> SL10 | Q069-Q070 |
| C12 cash flow | R09/R07 + CF01/CF03 -> M09/M06 | Q081-Q086 |
| C13 profitability | SP06 -> filter/group/rank | Q040, Q087-Q090, Q112 |
| C14 context replacement | prior typed slots + replace one slot -> same skill | Q092-Q093; unaffected slots/time persist |
| C15 paged shipment list with total | SP04 page + independent SP06 aggregate -> combine on exact period/filter coordinates | Full Q031; visible/page count and distinct total remain different facts |

PlanValidator checks every edge by exact fact signature and dependency lock.
There is no fallback that executes an unlisted resolver or reconstructs a ref
from presentation.

## 7. UT 11.5.27.56 proof ledger

### 7.1. Required proof chain

Наличие поля в XML доказывает только metadata shape. Для каждого data skill
обязательны четыре независимых уровня:

1. `metadata`: exact object/field/type из XML выгрузки и `get_metadata` profile;
2. `query-semantics`: читаемый query/report/module target-релиза, подтверждающий
   virtual table и бизнес-трактовку, либо явная пометка `live-only semantics`;
3. `live-contract`: fixed package успешно компилируется и выполняется через
   read-only MCP на positive и confirmed-empty/negative input;
4. `oracle`: отдельный control query, не импортирующий production template,
   формирует expected facts при том же `acceptance_observable_state` marker.

Builder сохраняет relative URI, SHA-256 каждого proof-файла, object/field
assertions, query anchor, дату проверки и reviewer в provenance. Изменение
любого proof hash требует нового skill version/digest и повторного live gate.

### 7.2. Exact source groups

| Proof | Exact sources and anchors | Skills |
| --- | --- | --- |
| P00 release | `Configuration.xml#Name=УправлениеТорговлейБазовая`, `#Version=11.5.27.56` | all |
| P01 items | `Catalogs/Номенклатура.xml` (`Артикул`, `ЕдиницаИзмерения`, hierarchy); `InformationRegisters/ШтрихкодыНоменклатуры.xml` (`Штрихкод`, `Номенклатура`, `Характеристика`, `Серия`, `Упаковка`) | R01A-D, R02-R03, R10-R11 |
| P02 parties | `Catalogs/Партнеры.xml` (`Клиент`, `Поставщик`); `Catalogs/Контрагенты.xml` (`Партнер`, `ИНН`, `КПП`, contact-information table) | R04-R05 |
| P03 places/money refs | `Catalogs/Склады.xml` (standard ref/name, direct `ТипСклада`, `Подразделение`; no direct `Организация`/`Назначение` claim); `Catalogs/Кассы.xml`; `Catalogs/КассыККМ.xml` (`ТипКассы`, `Склад`, currency); `Catalogs/Организации.xml`; `Catalogs/БанковскиеСчетаОрганизаций.xml` | R06-R09, CF01-CF02 |
| P04 inventory dimensions | `Catalogs/ХарактеристикиНоменклатуры.xml`; `Catalogs/СерииНоменклатуры.xml`; `Catalogs/Назначения.xml` | R10-R12 and typed filters in PR/SP/SL |
| P05 prices | `Catalogs/ВидыЦен.xml`; `InformationRegisters/ЦеныНоменклатуры.xml` (`Цена`, `Упаковка`, `Валюта`, dimensions); `InformationRegisters/ЦеныНоменклатурыПоставщиков.xml`; query anchors `Reports/ПрайсЛист/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#ЦеныНоменклатуры.СрезПоследних` and `Reports/ДинамикаИзмененияЦенНоменклатуры/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#ЦеныНоменклатуры` | PR01-PR04 |
| P06 sales documents | `Documents/ЗаказКлиента.xml` (`Партнер`, `Организация`, `Склад`, `Статус`, `СуммаДокумента`, `Товары`); `InformationRegisters/СостоянияЗаказовКлиентов.xml` (`Состояние`, payment/shipment/debt percentages); `Documents/РеализацияТоваровУслуг.xml`; `Documents/ВозвратТоваровОтКлиента.xml`; line semantics from their `Товары` tabular sections | SP01-SP05, SP07 |
| P07 sales result | `AccumulationRegisters/ВыручкаИСебестоимостьПродаж.xml` (`Количество`, `СуммаВыручки`, `Стоимость`, sales dimensions); query anchor `Reports/ВыручкаИСебестоимостьПродаж/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#РегистрНакопления.ВыручкаИСебестоимостьПродаж` | SP06, SP08 |
| P08 purchases/supply | `Documents/ЗаказПоставщику.xml`; `InformationRegisters/СостоянияЗаказовПоставщикам.xml`; `Documents/ПриобретениеТоваровУслуг.xml`; `Documents/ВозвратТоваровПоставщику.xml`; `AccumulationRegisters/Закупки.xml`; `AccumulationRegisters/ЗаказыПоставщикам.xml`; `AccumulationRegisters/ЗапасыИПотребности.xml`; query anchor `Reports/ИсполнениеПланаЗакупок/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#РегистрНакопления.Закупки.Обороты` | PR03, SP09-SP16 |
| P09 stock | `AccumulationRegisters/ТоварыНаСкладах.xml` (`ВНаличии`, item/warehouse/characteristic/series/purpose); `AccumulationRegisters/ЗапасыИПотребности.xml` (`ВНаличии`, `Поступит`, reserve/ensure resources); query anchors `Reports/ВедомостьПоТоварамНаСкладах/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#ТоварыНаСкладах.ОстаткиИОбороты` and `Reports/ОстаткиИДоступностьТоваров/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#ЗапасыИПотребности.Остатки` | SL01-SL04, SP16 |
| P10 logistics | `Documents/ПеремещениеТоваров.xml` (`СкладОтправитель`, `СкладПолучатель`, `Статус`, `Товары`); `Documents/ПересчетТоваров.xml` (`Количество`, `КоличествоФакт`); `Documents/ВнутреннееПотребление.xml`; `Documents/ЗаказНаДоставку.xml`; `AccumulationRegisters/Доставка.xml`; query anchors `Reports/ИсполнениеРаспоряженийНаПеремещениеСборку/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#ЗаказыНаПеремещение.ОстаткиИОбороты` and `Reports/СостояниеДоставки/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#РегистрНакопления.Доставка.Обороты` | SL05-SL10 |
| P11 settlements | `AccumulationRegisters/РасчетыСКлиентами.xml`; `AccumulationRegisters/РасчетыСПоставщиками.xml`; `AccumulationRegisters/РасчетыСКлиентамиПоДокументам.xml`; `AccumulationRegisters/РасчетыСПоставщикамиПоДокументам.xml`; `AccumulationRegisters/РасчетыСКлиентамиПоСрокам.xml`; query anchors `Reports/РасчетыСКлиентами/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#РасчетыСКлиентами.ОстаткиИОбороты`, `Reports/РасчетыСПоставщиками/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#РасчетыСПоставщиками.ОстаткиИОбороты` and `Reports/ЗадолженностьКлиентовПоСрокам/Ext/ObjectModule.bsl#РасчетыСКлиентамиПоСрокам.Остатки` | SE01-SE05 |
| P12 cash | `AccumulationRegisters/ДенежныеСредстваНаличные.xml`; `AccumulationRegisters/ДенежныеСредстваБезналичные.xml`; `AccumulationRegisters/ДенежныеСредстваВКассахККМ.xml`; `AccumulationRegisters/ДвиженияДенежныхСредств.xml`; query anchors `Reports/ВедомостьПоДенежнымСредствам/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#ОстаткиИОбороты` and `Reports/ДвиженияДенежныхСредств/Templates/ОсновнаяСхемаКомпоновкиДанных/Ext/Template.xml#ДвиженияДенежныхСредств.Обороты` | CF01-CF03 |
| P13 help | Exact built-in files listed below; index revision includes parser/tokenizer/chunker configuration and every chunk hash | D01-D04 |

For P06 return queries, P08 expected supply and P10 inventory/internal
consumption, document/register XML is not sufficient semantic proof by itself.
If no readable configuration query exactly matches the intended fact, the skill
is marked `live-only semantics` and cannot ship until two independently authored
live queries agree on identity, values, units and time coordinates.

### 7.3. Required built-in help paths for Q001-Q010

| Scenarios | Pinned path prefixes |
| --- | --- |
| Q001/Q010 | `Documents/ЗаказКлиента/Ext/Help/ru.html` |
| Q002 | `Catalogs/Партнеры/Ext/Help/ru.html`, `Catalogs/Контрагенты/Ext/Help/ru.html` |
| Q003/Q007 | `Documents/РеализацияТоваровУслуг/Ext/Help/ru.html`, `Documents/РеализацияТоваровУслуг/Forms/ФормаДокумента/Ext/Help/ru.html` |
| Q004 | `Documents/ЗаказПоставщику/Ext/Help/ru.html` |
| Q005 | `Documents/ПеремещениеТоваров/Ext/Help/ru.html` |
| Q006 | `Documents/ВозвратТоваровОтКлиента/Ext/Help/ru.html` |
| Q008 | `Catalogs/ВидыЦен/Ext/Help/ru.html` |
| Q009 | `Documents/ПересчетТоваров/Ext/Help/ru.html` |

Path prefixes constrain retrieval but do not hardcode an answer. D02/D03 may
search several listed prefixes; every final claim still cites exact chunks.

## 8. Package grouping, locks and build order

| Package file | Embedded roots | External exact locks | Install order |
| --- | --- | --- | --- |
| `ut115-docs-1.0.0.package.json` | D01-D04 | runtime `help-index` only | 1 |
| `ut115-reference-1.0.0.package.json` | R01A-D, R02-R12 | runtime `mcp.execute_query` only | 1 |
| `ut115-pricing-1.0.0.package.json` | PR01-PR04 | R01D, R03, R08 | 2 |
| `ut115-sales-purchases-1.0.0.package.json` | SP01-SP16 | R01D, R04, R06 | 2 |
| `ut115-stock-logistics-1.0.0.package.json` | SL01-SL10 | R01D, R06, SP01 | 3 |
| `ut115-settlements-1.0.0.package.json` | SE01-SE05 | SP04, SP11 | 3 |
| `ut115-cash-finance-1.0.0.package.json` | CF01-CF03 | R07/R09 | 3 |

`External exact locks` is the union shown for planning. Actual package
`dependency_lock` is the exact transitive closure of dependencies declared by
its embedded skills: no blanket unused entry, no missing producer, no floating
version and no alternate digest. Web and CLI import these same bytes and must
produce the same package/skill/catalog digests.

The v1 dependency schema has no alternative-producer expression. Therefore a
ref-consuming skill declares one canonical availability producer, while runtime
coverage may use any compatible producer of the same exact fact signature. The
baseline uses R01D as canonical item-ref producer; this never authorizes R01D
for an exact-article intent, where R01A remains the only valid call. Mandatory
availability edges include R02<-R01D, R05<-R04, PR01/PR02<-R01D+R08,
SP03<-SP01, SP05<-SP04, SP10<-SP09, SP12/SP13<-SP11, SL06/SL07<-SL05 and
SE04<-SP04+SP11. The arrow does not mean a hidden invocation: every selected
call remains explicit in the plan.

Build sequence inside each package:

1. freeze semantic fact IDs, cardinality, unit/time and row identity;
2. record P00 plus exact proof group and metadata assertions;
3. write fixed parameterized query and ADR-0003 execution/literal manifest;
4. add positive, confirmed-empty and malformed/wrong-type fixtures;
5. run schema/semantic lint and exact dependency closure;
6. run live compile/positive/negative through MCP;
7. generate independent oracle at the active marker;
8. run relevant Q scenarios and offline trace replay;
9. import/export in clean data dirs through web and CLI;
10. atomically activate only after the whole package passes.

## 9. Bounded shortlist with 87 capabilities/skills

The core must remain bounded even if an installation has 87 or more active
skills, although this baseline uses 57 atomic skills.

1. Hard-filter pinned skills by operation kind, target compatibility, required
   entity/ref types, time mode and forbidden write intent.
2. Build a candidate pool from independent signals: exact semantic-fact index,
   entity-type index, declared aliases/examples, lexical retrieval and required
   dependency producers. Text similarity alone never establishes applicability.
3. Compute a deterministic minimum cover for all required fact signatures,
   including cardinality/unit/time. Add dependency closure and then fill spare
   slots by reciprocal-rank fusion with stable `skill_id/version` tie-break.
4. Planner shortlist has `maxItems=16`. Core must retain at least one compatible
   producer for every required fact; if cover+closure exceeds 16, return one
   clarification or `capability_gap`, never silently discard a producer.
5. At most one expanded planning pass is allowed. Expansion uses only missing
   fact signatures from a structurally valid planner result, replaces
   distractors, and remains at 16. It never exposes the full catalog.
6. Query text, full provenance and raw examples are not part of skill cards.
   Cards contain purpose, typed inputs, produced fact signatures, limits and
   compatibility only.

Worst-case test activates 87 synthetic skills including at least 20 lexical
near-duplicates. It asserts shortlist `<=16`, deterministic order, inclusion of
the only exact fact producer, one bounded expansion, no MCP before coverage and
no dependence on a Q-ID or corpus business value.

## 10. Test and oracle model

| Oracle class | Compares | Used for |
| --- | --- | --- |
| O-DOC | cited chunk hashes, source URI, role, grounded claims, disagreement set | Q001-Q010 |
| O-ENTITY | parsed structural object ref, semantic entity type, identifying fields | resolvers and follow-ups |
| O-LIST | canonical row identities/fields, stable ordering, truncation/cursor | document/item lists |
| O-MEASURE | Decimal value, semantic measure, unit/currency, identity coordinates, moment/period | balances, turnover, debt, cash, price |
| O-RANK | O-MEASURE plus direction, requested N and stable tie policy | top/bottom scenarios |
| O-COMPOSITE | each step evidence, exact edge binding, final coverage and no intermediate final | multi-skill and follow-up scenarios |
| O-OUTCOME | outcome code, dependency/stage, no prohibited call, session survival | Q098-Q106 |
| O-META | active catalog summary, read-only claim, versions | Q107 |

Rules:

- Every atomic skill has positive and negative portable fixtures. Every mapped
  capability has at least one positive and one negative assertion, even when
  several capabilities share one skill.
- The corpus runner maps Q-ID to expected fact signatures and oracle class only
  in tests. Production input contains no Q-ID.
- Independent control queries live under test/oracle ownership and are not
  copied into package query templates. Both execute against one accepted marker.
- The full stored ref passed into the next MCP request is compared after
  canonical JSON normalization and must be unchanged. Returned or reloaded refs
  are matched by `(semantic entity type, ТипОбъекта, UUID)`; presentation and
  incidental JSON property order are ignored unless presentation is an
  explicitly required semantic fact.
- Empty-list or zero aggregate is accepted only after schema/type validation;
  partial/truncated evidence cannot satisfy a complete final requirement.
- Money uses Decimal and currency minor-unit comparison; quantities/counts are
  exact; ranking compares identity, measure, direction and order separately.
- Each composition C01-C14 has an automatic DAG/edge/coverage test. Adversarial
  tests inject forged refs, wrong semantic type, incompatible currency/time,
  disagreement and a skill from outside the pinned shortlist.

## 11. Capability completeness matrix, 87/87

`Implementation` names a portable skill, mechanism or composition. A capability
mapped to an operator still appears in active `capability_manifest`; it is not a
dummy JSON skill. `Q` lists every direct corpus occurrence as of the accepted
116-scenario corpus.

### 11.1. Dialogue and common, 13

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-CHAT-CONTEXT` | M02, context edges in C01/C04/C05/C09/C14 | Q037,Q042,Q063,Q064,Q073,Q082,Q092,Q093,Q095,Q097,Q108 / O-COMPOSITE |
| `CAP-CHAT-CLARIFY` | M01 plus typed ambiguity reasons | Q029,Q056,Q057,Q065,Q077,Q081,Q086,Q089,Q090,Q098,Q099 / O-OUTCOME |
| `CAP-CHAT-NOT-FOUND` | M03 after validated `success_empty` | Q102,Q103 / O-OUTCOME |
| `CAP-CHAT-OUT-OF-SCOPE` | M01 scope decision, no MCP/help | Q100,Q104 / O-OUTCOME |
| `CAP-CHAT-READ-ONLY` | M01 write-intent rejection before planning | Q101 / O-OUTCOME |
| `CAP-CHAT-DEPENDENCY-ERROR` | M03 typed MCP/LLM failure renderer | Q105,Q106 / O-OUTCOME |
| `CAP-COMMON-PERIOD` | M04 | Q025,Q030-Q034,Q038-Q039,Q044-Q045,Q049,Q059-Q061,Q068,Q080,Q084-Q085,Q087,Q103,Q109-Q110,Q112-Q113,Q115 / O-MEASURE |
| `CAP-COMMON-ENTITY` | M05 with R01A-D/R04/R06-R12, SP01, SL05 | Q036,Q062,Q069,Q108,Q111 / O-ENTITY |
| `CAP-COMMON-COUNT` | M06 with declared distinct identity | Q015,Q016,Q032,Q044,Q061 / O-MEASURE |
| `CAP-COMMON-AGGREGATE` | M06 | Q033,Q053,Q071,Q075,Q084,Q085 / O-MEASURE |
| `CAP-COMMON-RANK` | M07 | Q035,Q038,Q039,Q041,Q043,Q048,Q050,Q067,Q076,Q077,Q090,Q094,Q114 / O-RANK |
| `CAP-COMMON-DETAIL` | M05 exact ref plus R02/R05 or typed document header | Q108 / O-ENTITY |
| `CAP-COMMON-GROUP` | M09 | Q032,Q038-Q040,Q045,Q061,Q086,Q088-Q090 / O-MEASURE |

### 11.2. Documentation, 6

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-DOC-SEARCH` | M10 retrieval used by D02/D03 | Q003-Q009 / O-DOC |
| `CAP-DOC-TERM` | D01 | Q001,Q002 / O-DOC |
| `CAP-DOC-PROCEDURE` | D02 | Q003-Q006,Q008,Q009 / O-DOC |
| `CAP-DOC-ERROR` | D03 | Q007 / O-DOC |
| `CAP-DOC-STATUS` | D04 | Q010 / O-DOC |
| `CAP-DOC-SOURCE` | mandatory citation binding in D01-D04 plus M10 | Q001-Q010 / O-DOC |

### 11.3. Reference data, 8

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-REF-ITEM-FIND` | R01A-D logical resolver family, P01; Q011/Q102 use R01A only | Q011-Q015,Q021-Q025,Q028,Q030,Q048,Q051-Q053,Q055,Q059-Q060,Q091-Q092,Q102,Q112,Q115-Q116 / O-ENTITY |
| `CAP-REF-ITEM-DETAILS` | R02, P01 | Q013,Q014 / O-ENTITY |
| `CAP-REF-ITEM-GROUP` | R03, P01 | Q016,Q029 / O-LIST |
| `CAP-REF-PARTNER-FIND` | R04, P02 | Q017,Q018,Q079,Q080,Q109 / O-ENTITY |
| `CAP-REF-PARTNER-DETAILS` | R05, P02 | Q017,Q073,Q097 / O-ENTITY |
| `CAP-REF-WAREHOUSE-FIND` | R06, P03 | Q019,Q053,Q054,Q056,Q058,Q065,Q091,Q115,Q116 / O-ENTITY |
| `CAP-REF-CASH-DESK-FIND` | R07, P03 | Q020,Q081,Q082 / O-ENTITY |
| `CAP-REF-PRICE-TYPE-FIND` | R08, P05 | Q021,Q024-Q030 / O-ENTITY |

### 11.4. Sales, 10

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-SALES-ORDER-LIST` | SP02, P06 | Q035 / O-LIST |
| `CAP-SALES-ORDER-HEADER` | SP01, P06 | Q111 / O-ENTITY |
| `CAP-SALES-ORDER-LINES` | SP03, P06 | Q037 / O-COMPOSITE |
| `CAP-SALES-ORDER-STATUS` | SP01, P06 | Q036 / O-MEASURE |
| `CAP-SALES-SHIPMENT-LIST` | SP04, P06 | Q031-Q034,Q077,Q103,Q114 / O-LIST |
| `CAP-SALES-SHIPMENT-LINES` | SP05, P06 | Q114 / O-COMPOSITE |
| `CAP-SALES-TURNOVER` | SP06, P07 | Q038,Q039 / O-MEASURE |
| `CAP-SALES-PROFIT` | SP06, P07 | Q112 / O-MEASURE |
| `CAP-SALES-RETURN` | SP07, P06/P07 | Q113 / O-LIST+O-MEASURE |
| `CAP-SALES-AVERAGE` | SP04 document amount -> M06 average with document denominator | Q034 / O-MEASURE |

### 11.5. Purchases, 9

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-PURCHASE-ORDER-LIST` | SP09, P08 | Q046 / O-LIST |
| `CAP-PURCHASE-ORDER-STATUS` | SP10, P08 | Q046 / O-MEASURE |
| `CAP-PURCHASE-RECEIPT-LIST` | SP11, P08 | Q041,Q043,Q044,Q048,Q050,Q076,Q094 / O-LIST |
| `CAP-PURCHASE-RECEIPT-HEADER` | SP12, P08 | Q076,Q095 / O-ENTITY |
| `CAP-PURCHASE-RECEIPT-LINES` | SP13, P08 | Q042,Q043,Q048 / O-COMPOSITE |
| `CAP-PURCHASE-TURNOVER` | SP14, P08 | Q045,Q110 / O-MEASURE |
| `CAP-PURCHASE-EXPECTED` | SP16, P08/P09 | Q047 / O-MEASURE |
| `CAP-PURCHASE-RETURN` | SP15, P08 | Q049 / O-LIST+O-MEASURE |
| `CAP-PURCHASE-SUPPLIER-RANK` | SP14 -> M09/M07 | Q110 / O-RANK |

### 11.6. Prices, 6

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-PRICE-CURRENT` | PR01, P05 | Q021,Q028-Q030 / O-MEASURE |
| `CAP-PRICE-HISTORY` | PR02, P05 | Q025 / O-LIST+O-MEASURE |
| `CAP-PRICE-LAST-PURCHASE` | PR03, P08 | Q022,Q023 / O-MEASURE |
| `CAP-PRICE-VAT` | PR03/PR01 -> M08 using typed VAT rate/inclusion facts | Q023 / O-COMPOSITE |
| `CAP-PRICE-COMPARE` | two PR01 facts -> M08 typed compare | Q024 / O-COMPOSITE |
| `CAP-PRICE-MISSING` | PR04, P01/P05 | Q026,Q027 / O-LIST |

### 11.7. Stock, 10

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-STOCK-BALANCE` | SL01, P09 | Q051-Q054,Q056-Q057,Q059,Q091-Q093 / O-MEASURE |
| `CAP-STOCK-AVAILABLE` | SL02, P09 | Q055 / O-MEASURE |
| `CAP-STOCK-RESERVED` | SL02, P09 | Q055 / O-MEASURE |
| `CAP-STOCK-BY-WAREHOUSE` | SL01 warehouse grain | Q052,Q057,Q091,Q092 / O-MEASURE |
| `CAP-STOCK-BY-ITEM` | SL01 item grain | Q051,Q052,Q054,Q056,Q059,Q091-Q093 / O-MEASURE |
| `CAP-STOCK-MOVEMENT` | SL03 receipt/consumption/document facts | Q060 / O-LIST+O-MEASURE |
| `CAP-STOCK-CONSUMPTION` | SL03 consumption fact | Q115 / O-MEASURE |
| `CAP-STOCK-DEFICIT` | SL04 | Q058 / O-LIST |
| `CAP-STOCK-RANK` | SL01 -> M07 | Q056,Q057 / O-RANK |
| `CAP-STOCK-EXPECTED` | SP16, P08/P09 | Q116 / O-MEASURE |

### 11.8. Internal operations and logistics, 8

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-MOVE-LIST` | SL05, P10 | Q061,Q062,Q066 / O-LIST |
| `CAP-MOVE-LINES` | SL07, P10 | Q064 / O-COMPOSITE |
| `CAP-MOVE-STATUS` | SL06, P10 | Q063,Q066 / O-MEASURE |
| `CAP-MOVE-DIRECTION` | SL05 source/destination parameters | Q065 / O-LIST |
| `CAP-INVENTORY-RESULT` | SL08, P10 | Q067 / O-MEASURE |
| `CAP-INTERNAL-CONSUMPTION` | SL09, P10 | Q068 / O-LIST+O-MEASURE |
| `CAP-DELIVERY-STATUS` | SL10, P10 | Q069,Q070 / O-MEASURE |
| `CAP-DELIVERY-DATE` | SL10, P10 | Q069,Q070 / O-MEASURE |

### 11.9. Customers and settlements, 8

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-SETTLEMENT-AR` | SE01, P11 | Q071,Q072,Q074,Q077,Q079,Q096 / O-MEASURE |
| `CAP-SETTLEMENT-AP` | SE02, P11 | Q075,Q076 / O-MEASURE |
| `CAP-SETTLEMENT-DETAIL` | SE01/SE02 object/document grain | Q079 / O-MEASURE |
| `CAP-SETTLEMENT-OVERDUE` | SE03, P11 | Q078 / O-LIST+O-MEASURE |
| `CAP-SETTLEMENT-RANK` | SE01/SE02 -> M07 | Q072,Q074,Q096 / O-RANK |
| `CAP-SETTLEMENT-BY-DOCUMENT` | SE04, P11 | Q076,Q077 / O-COMPOSITE |
| `CAP-CUSTOMER-SALES-HISTORY` | SP08, P07 | Q109 / O-LIST+O-MEASURE |
| `CAP-CUSTOMER-NO-ACTIVITY` | SE05, P02/P07 | Q080 / O-LIST |

### 11.10. Cash and finance, 9

| Capability | Implementation | Q / oracle |
| --- | --- | --- |
| `CAP-CASH-BALANCE` | CF01, P12 | Q081,Q082 / O-MEASURE |
| `CAP-CASH-BANK-BALANCE` | CF02, P12 | Q083 / O-MEASURE |
| `CAP-CASH-RECEIPTS` | CF03 receipt fact | Q084 / O-MEASURE |
| `CAP-CASH-EXPENSES` | CF03 expense fact | Q085 / O-MEASURE |
| `CAP-CASH-FLOW` | CF03 receipt/expense/net facts | Q086 / O-MEASURE |
| `CAP-FIN-REVENUE` | SP06 revenue fact | Q040,Q087-Q090 / O-MEASURE |
| `CAP-FIN-COST` | SP06 cost fact | Q040,Q087-Q090 / O-MEASURE |
| `CAP-FIN-PROFIT` | SP06 gross-profit fact | Q040,Q087-Q090 / O-MEASURE |
| `CAP-FIN-TREND` | SP06 -> M09 timeline | Q088 / O-MEASURE |

## 12. Concrete implementation plan for slices 2-6

Skills are introduced when an earlier E2E scenario needs them, even if their
final distribution package is completed in slice 5. This resolves the ordering
in `implementation_slices.md`: slice 5 is the full proof/package completion
gate, not the first appearance of every business query.

### 12.1. Slice 2 - outcomes, pagination and failures

Deliver:

- M03 outcome state machine with all eight outcomes and deterministic fallback;
- immutable required/optional step criticality from reverse closure of typed
  final requirements; required branches execute before optional-only branches;
- exact `requirement_id/required` preservation in evidence coverage;
  `sufficient` is conjunction of typed coverage and collection completeness
  over required requirements only;
- keyset pagination for every unbounded producer; prefix only with a cited,
  digest-pinned `cardinality <= maximum_total <= 1000` invariant, exact-boundary
  behavior, default display page 20, page-size+1 keyset detection, 30-minute
  single-use opaque server-side continuation handle, public DTO and explicit
  continuation message;
- reject `keyset|prefix` for aggregate/exact cardinality; zero aggregates come
  only from non-paginated one-row complete-set producers;
- stage deadlines/retries, dependency diagnostics and two-phase preview/confirm
  manual clear with a five-minute confirmation token;
- finish SP01's full header/status contract; add metadata-constrained R06 and
  SP04 because Q054 and Q031/Q103 need real producers, not fixture-only
  application branches; Q054 filters R06 only by proved `ТипСклада`; slice 2
  validates only Q015/Q031 list/pagination, while their totals wait for separate
  aggregate producers (SP06 for Q031);
- replace the six remaining unproved `prefix:1000` contracts in
  R01A/R01B/R01C/R01D, SP03 and SL01 with metadata-proven keyset contracts;
  retain the already-keyset R06 and SP04 contracts;
- persisted tests proving `success_empty`, zero aggregate, partial, query error,
  MCP unavailable and LLM unavailable remain distinct;
- use `docs/testing/slice2_acceptance_contract.md` as the black-box DTO and
  decision table for this slice.

Risks:

- an unstable sort key can duplicate/skip rows between pages;
- an asserted `maximum_total` without a cardinality proof can silently lose
  rows at 1000;
- malformed/empty MCP envelopes can be mislabeled `success_empty`;
- direct warehouse fields not present in metadata can leak into R06;
- retry can exceed the turn deadline or duplicate an execution trace.

Exit gate:

- Q012,Q054,Q099,Q102-Q106 and the explicit `Q015.list`/`Q031.list` subcases pass
  on fixtures; selected positive/empty queries pass live when MCP is available;
- `Q015.total`, full Q015, `Q031.total` and full Q031 remain `not_run` in slice
  2; returning page size 20 as a business total is an immediate failure, not
  partial credit;
- R01A/R01B/R01C/R01D, R06, SP03, SP04 and SL01 expose keyset cursor parameters
  and metadata-proven stable total order; generic import tests prove ordered
  sort/cursor bijection, non-null typed coordinates, full identity suffix and
  AST-equivalent ORDER BY/strict after-predicate; no current XML citation is
  accepted as a proof that their result is bounded by 1000;
- prefix contract tests, if any proved-prefix skill is later added, cover
  `M-1`, exactly `M`, and invariant violation `M+1` with
  `RESULT_PREFIX_BOUND_EXCEEDED`;
- every new evidence bundle is 1.1 and emits explicit step `collection_scope`
  and requirement `required` with no defaults; frozen 1.0 remains readable via
  its version-specific in-memory defaults and is never rewritten; missing
  optional remains reported while sufficient ignores it;
- cursor is bound to session, source turn, skill/version/digest, params, exact
  catalog snapshot, marker and sort tuple; forged/cross-session/consumed/
  expired/catalog-changed/marker-changed paths return the specified public code;
- one-row zero/null aggregate, truncation and malformed envelope tests pass;
- cardinality-only mutation of a paged list to aggregate is rejected with
  `PAGINATION_CARDINALITY_MISMATCH`;
- maintenance preview/confirm counts, stale token and clear-scope closure pass;
- R06 query/fixture binds only name, `ТипСклада` and optional `Подразделение`;
- no retry crosses deadline and the next turn survives every injected failure;
- AC-018, AC-021, AC-046, AC-049 and AC-050 slice-2 evidence is green;
- only `AC-024.list` may become `pass` here, using `Q031.list` rather than a
  pass of full Q031. `AC-024.rank` and global `AC-024`
  remain `not_run` until the M07 rank suite proves composition, order,
  direction and ranking measure.

### 12.2. Slice 3 - entity identity and context

Deliver:

- M02/M05 context ledger, pending clarification and exact typed binding;
- R01A-D and R02-R12 complete, including role-aware partner and two-kind
  cash-desk refs;
- early E2E producers required by this slice's corpus: PR01, SP11-SP13,
  SL05-SL07, SE01 and CF01;
- minimal stable M07 rank needed by Q056/Q057/Q096; slice 4 completes the full
  operator suite;
- explicit context-export allowlist plus core-derived SelectionProof; resolver
  candidates, display-only lists and ordinary entity rows never export merely
  because they contain an entity ref; an empty resolver, partial or ambiguous
  selection never exports, while a proved selection survives a valid empty
  downstream result;
- separate generic `confirmed_filter` proof for typed scalar conditions such as
  moment/period/enum/detail; exact canonical value is retained and accepted only
  by a compatible consumer, never recomputed in a follow-up;
- context row stores `origin_fact_instance_id`; binding restores original Fact,
  producer step and pinned skill/column contract instead of trusting copied JSON.

Normative ref behavior:

- identity key is `(semantic entity type, ТипОбъекта, UUID)`;
- context stores both identity key and the canonical full ref received from MCP;
- previous-step/context input is accepted only when producer fact semantic type
  is allowed by the consumer parameter and its actual `ТипОбъекта` belongs to
  the producer's exact `column_binding.accepted_mcp_types`;
- `entity_types` remains a semantic allowlist. There is no application object
  dictionary; a user mention first goes through a resolver and cannot inject a
  raw structural ref;
- the exact stored full ref is passed as Q037/SP03 MCP parameter;
- if SP03 returns the same type/UUID with a renamed presentation, linkage passes
  and UI may show the new presentation; different UUID/type or wrong semantic
  entity type is `contract_error`;
- byte/property-order differences alone do not break result identity;
  presentation equality is checked only when the output contract explicitly
  requires it as a semantic fact.

Risks:

- presentation collision replacing UUID identity;
- stale/forged handle crossing session, catalog or semantic type boundaries;
- optional characteristic/series/purpose silently dropped between steps.

Exit gate:

- Q013-Q020,Q029,Q037,Q042,Q056-Q057,Q062-Q064,Q073,Q081-Q082,Q091-Q097,Q108
  pass with exact context-edge evidence, except that Q015 closes only its
  resolver/list/context component here while `Q015.total` and full Q015 remain
  `not_run` until the separate aggregate producer;
- property tests cover same presentation/different UUID, renamed
  presentation/same UUID, wrong object type, forged/missing origin provenance
  and wrong semantic type; every rejection occurs before MCP;
- a synthetic new entity semantic/physical pair works by importing its producer
  and consumer contracts without changing application source; source scan
  rejects an object-specific `_physical_type`/equivalent map and semantic/
  physical prefix heuristics as substitutes for exact producer provenance;
- Q036 creates exactly one order handle, Q037 sends its unchanged full ref to
  MCP, and returned same-identity/new-presentation rows are accepted;
- restart/reload preserves context and pending clarification; AC-016 and AC-035
  are measured over the accepted 11 follow-ups.

The normative black-box protocol, migration rules and per-scenario matrix are in
`docs/testing/slice3_acceptance_contract.md`.

### 12.3. Slice 4 - generic operators and composition

Deliver:

- complete M06-M09 allowlist with closed typed input/output contracts;
- stage the skills required for genuine E2E operator tests: PR03, SP02, SP06,
  SP09-SP10, SP14-SP16, SL02, SL08, SE01-SE02, SE04 and CF03; reuse already
  staged SP04, SP11-SP13 and SL01/SL05;
- deterministic Decimal/currency arithmetic, half-open periods, stable rank
  ties, distinct identity definitions and typed join coordinates;
- plan coverage proof before execution and evidence coverage after every
  required step, including disagreements.

Risks:

- a generic operator becomes an escape hatch for mismatched semantic types;
- document/row/item counts or amount/debt/revenue/profit are conflated;
- grouping changes time grain or joins currencies without conversion evidence.

Exit gate:

- Q016,Q023-Q024,Q032-Q040,Q043-Q050,Q053-Q055,Q061,Q067,Q071-Q077,Q084-Q090
  pass as full plans, not isolated operator unit tests;
- negative matrix rejects rows-vs-documents, incompatible units/currencies,
  balance-vs-turnover time, revenue-vs-profit and document-amount-vs-debt;
- rank is stable under equal measures; average exposes its denominator;
- no intermediate resolver/list fact can satisfy a final measure requirement.

### 12.4. Slice 5 - full business data packages

Deliver:

- complete remaining data skills PR02/PR04, SP05/SP07/SP08, SL03/SL04/SL09/SL10,
  SE03/SE05 and CF02; harden all previously staged skills;
- assemble all six data packages containing exactly 53 data skills, with exact
  dependency locks, JCS/SHA-256, P00-P12 provenance and web/CLI portability;
- live positive/negative tests and independently authored control queries for
  each skill, including every `live-only semantics` item;
- active capability manifest mapping all data/dialogue/common capabilities;
- worst-case shortlist test with 87 active synthetic skills and max 16 cards.

Risks:

- static metadata presence is mistaken for correct business semantics;
- linked temp batches are slow or exceed MCP/turn limits;
- anti-join skills PR04/SL04/SE05 use an undefined universe;
- package closure over-locks unrelated skills or accepts a digest conflict;
- target demo data does not contain a positive example for a required fact.

Exit gate:

- all 53 data skills pass schema, semantic, fixture, live and independent-oracle
  gates; unresolved static semantics block the affected skill rather than being
  inferred;
- relevant Q011-Q116 data/composite/negative scenarios have a recorded result,
  with direct emphasis on Q109-Q116;
- web and CLI import the exact exported bytes in a clean data dir for both a
  dependency-free bare skill and a dependency-bearing selected-root package;
- replace/delete/hot reload and pinned in-flight turns pass concurrently;
- AC-001..027 applicable to data, AC-041..051 and package AC-006..013 are green.

### 12.5. Slice 6 - built-in documentation package

Deliver:

- D01-D04 over one pinned FTS5 index and M10 citation/disagreement pipeline;
- deterministic parser/chunker revision, safe MATCH construction, stable rank
  tie-break and source URI/anchor resolution;
- exact P13 path-prefix provenance for Q001-Q010;
- schema and retrieval hard-reject for every `source_kind` except
  `built_in_help`;
- typed disagreement containing every conflicting built-in fragment and an
  independent citation for each position.

Risks:

- retrieval rank is treated as truth when built-in fragments disagree;
- procedure renderer invents or reorders uncited steps;
- malformed HTML produces unstable anchors/chunks;
- an external source enters indexing before the source-kind boundary.

Exit gate:

- Q001-Q010 pass manual claim/citation review and automated O-DOC comparison;
- external-source schema, import, index and direct-retrieval tests all reject
  before reading content;
- disagreement fixture displays both positions/citations without silent choice;
- package transfers through web/CLI and activates without restart;
- after D01-D04 activation, the complete manifest is 87/87 and Q107 derives its
  claims only from that active manifest; AC-028..032, AC-058 and AC-059 pass.

## 13. Validation and gap/duplication appendix

### 13.1. Machine checks required on every blueprint/catalog change

Run from repository root:

```bash
expected=$(mktemp)
actual=$(mktemp)
rg -o 'CAP-[A-Z0-9-]+' docs/requirements/skill_catalog.md | sort -u > "$expected"
rg -o 'CAP-[A-Z0-9-]+' docs/architecture/full_catalog_blueprint.md | sort -u > "$actual"
test "$(wc -l < "$expected" | tr -d ' ')" = 87
test "$(wc -l < "$actual" | tr -d ' ')" = 87
test -z "$(comm -23 "$expected" "$actual")"
test -z "$(comm -13 "$expected" "$actual")"

inventory=$(rg '^\| (D|R|PR|SP|SL|SE|CF)[0-9]{2}[A-Z]? \| `ut115\.' \
  docs/architecture/full_catalog_blueprint.md)
test "$(printf '%s\n' "$inventory" | wc -l | tr -d ' ')" = 57
test "$(printf '%s\n' "$inventory" | sed -E 's/^[^`]*`([^`]*)`.*/\1/' \
  | sort -u | wc -l | tr -d ' ')" = 57
```

Expected result is exactly 87 required capability IDs, no extra IDs, 57
inventory rows and 57 unique atomic `skill_id` values. Runtime mechanisms and
operators are intentionally excluded from the atomic skill count. Data/doc
split is exactly 53/4.

Observed blueprint validation on 2026-07-21:

| Check | Result |
| --- | --- |
| unique IDs in `skill_catalog.md` | 87 |
| unique IDs anywhere in this blueprint | 87; missing 0; extra 0 |
| capability rows in section 11 | 87; unique 87; duplicates 0 |
| capability IDs used by Q001-Q116 | 87; missing from corpus 0 |
| atomic inventory rows | 57 |
| unique atomic `skill_id` values | 57; duplicates 0 |

This is a blueprint-level completeness/semantic review. It does not waive the
later exact `provides`/output-contract and evidence gates on the actual 57 JSON
skills.

### 13.2. Semantic overclaim audit

| Shared implementation | Claims allowed only if | Rejected overclaim |
| --- | --- | --- |
| R01A-D item resolver family | article/code/barcode use separate equality queries; only R01D uses escaped `like_contains` | one OR/contains query advertised as exact article/code/barcode resolution |
| SP01 header/status | required facts include ref, number, date, customer, organization, warehouse, status, amount and currency/unresolved reason | status-only result claiming `CAP-SALES-ORDER-HEADER` |
| SP06 sales performance | quantity, revenue, cost and profit are independently bound facts on identical coordinates | deriving/renaming missing cost or profit from revenue |
| SL01 stock balance | item/warehouse grain changes only declared dimensions of one on-hand fact | claiming available, reserved, expected or period consumption |
| SL02 availability | available and reserved have distinct exact bindings at one moment | assuming `available = on_hand` or silently deriving reserve |
| SL03 movement | receipt and consumption are distinct turnover facts with period and registrars | using ending balance as consumption |
| SP16 expected supply | only unreceived active grounds contribute and order/ref remains in evidence | including already received quantity or current balance |
| SE01/SE02 debt | receivable/payable direction, partner role, currency and as-of are explicit | treating document amount, shipment or receipt sum as debt |
| CF03 cash flow | receipt, expense and net are separate period facts | using cash/bank balance as flow or mixing bank movement into cash-only request |
| D01-D04/M10 | every claim is entailed by cited built-in chunks | adding uncited procedure/status steps or external content |
| M06-M09 | operand signatures and output signatures are closed and checked | operator used to coerce incompatible semantic types |

### 13.3. Q036/Q037/Q111 acceptance details

1. Q036 selects SP01 by required order-status fact, binds the document number as
   a parameter and receives one full header/status row. It stores the full ref
   and identity key only after sufficient evidence.
2. Q037 selects SP03 by required order-line facts and binds the exact stored full
   ref. Application code contains no resolver/query and does not search by
   document number or presentation.
3. The outbound Q037 MCP parameter must equal the stored full ref after canonical
   JSON normalization. This verifies lossless continuation transport.
4. Each returned line carries source order ref and stable line identity. Source
   order matching uses `(sales-order semantic type, ТипОбъекта, UUID)`.
5. Same UUID/type with changed presentation is accepted; display may refresh and
   trace records both representations. Changed UUID/type or wrong semantic type
   is rejected. Full-ref byte equality is not a result-identity requirement.
6. Q111 uses SP01 but validates the full header required set. A result containing
   only number/date/status fails coverage even if Q036 would otherwise be
   answerable.

### 13.4. Resolved gaps and remaining risks

| Item | Resolution |
| --- | --- |
| Slice 1 listed four skills but Q036/Q037 needed an order producer | SP01 is the fifth skill and has no application hardcode |
| Slice 6 wording suggested a standalone source skill | Citation/source is mandatory M10 output of D01-D04; no duplicate retrieval skill |
| Slice 3/4 scenarios require business skills before slice 5 | Skills are staged earlier; slice 5 completes packaging/live proof |
| Presentation versus identity | Identity is semantic type/object type/UUID; full ref is preserved only for transport/provenance unless presentation is explicitly semantic |
| Physical type of input EntityRef | Restored producer fact and exact column binding prove semantic and physical type; context retains origin fact pointer; no application object map |
| Single-skill portability | Import dispatch accepts bare skill or package; bare dependencies resolve against pinned active closure, while clean transfer of a dependency-bearing selected skill uses the same self-contained package file |
| Finance package versus sales source | SP06 owns sales-result facts once; finance manifest references it without duplicate skill |
| Zero/missing sets | PR04, SL04 and SE05 are explicit anti-join skills with declared universes, not filters over absent rows |
| Common detail | M05 plus entity-specific fixed detail projections; no arbitrary metadata/query synthesis |

### 13.5. Item resolver acceptance details

1. Q011 and Q102 shortlist/plan assertions require R01A and reject R01D even if
   lexical similarity is higher. The fixed R01A query contains exact equality
   on `Номенклатура.Артикул` and no `ПОДОБНО`, prefix or wildcard binding.
2. R01B and R01C have equivalent exact tests for catalog code and barcode;
   R01C reads `InformationRegisters/ШтрихкодыНоменклатуры` and returns the linked
   item identity.
3. Q012 and other contains-name cases select R01D. Its sole search binding uses
   the schema's generic `like_contains` encoding; `%`, `_` and the platform
   escape character in user text are escaped before surrounding wildcards are
   added.
4. Cross-mode negative tests prove exact article/code/barcode values are never
   passed to R01D and name fragments are never silently passed to R01A-C.
5. All four physical skills provide the same logical item-ref signature and
   `CAP-REF-ITEM-FIND`, but diagnostics retain the actual physical skill ID and
   match kind.

Residual release risks are target-data coverage, unreadable `.bin` modules,
query performance and unavailable live MCP. They do not justify guessed
semantics: affected skills remain outside the active release manifest until
P00-P12 live/oracle gates pass. No candidate/draft lifecycle is introduced.
