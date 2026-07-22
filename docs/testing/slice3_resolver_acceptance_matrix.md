# Приемочная матрица production resolver catalog 3B

Статус: обязательное дополнение к
`docs/testing/slice3_acceptance_contract.md` для production JSON-каталога
reference skills R02-R12. Документ не ослабляет общий контракт slice 3 и не
заменяет synthetic portability suite в `tests/acceptance_slice3`.
Concrete inventory и package topology синхронизированы с
`docs/requirements/slice3_resolver_catalog.md` и
`docs/architecture/slice3_resolver_packages.md`.

## 1. Граница 3B

В scope входят ровно следующие логические позиции каталога:

```text
R02, R03, R04, R05, R06, R07, R08, R09, R10, R11, R12
```

R01A-D используются только как уже принятый upstream item producer для
composition R01 -> R02/R03/R10/R11. Их повторная функциональная приемка не
входит в 3B. Downstream PR/SP/SL/SE/CF skills и generic operators могут быть
контролируемыми consumers в composition tests, но результат 3B не повышает их
собственный статус приемки.

В scope входят:

- production skill/package JSON, импортированный через public API;
- fixture MCP и DeepSeek transport, public HTTP/SSE, Evidence 1.1 и diagnostic
  ZIP;
- resolver state machine, pending clarification, exact context binding,
  restart, export/import и повторное использование выбранной сущности;
- отсутствие object-specific и lexical resolver logic в core;
- non-regression существующих slice-2 и slice-3 контрактов.

В scope не входят:

- доказательство бизнес-истины по live 1C вместо fixture proof;
- приемка полного результата Q-сценария, если он требует еще не принятого
  downstream skill или operator;
- отдельные ожидания по конкретным русским словам в вопросе, `aliases_ru`,
  presentation или query text;
- inferred pass для live gate при недоступном MCP. Такой gate остается
  `blocked_external` или `not_run`.

Текущий baseline перед 3B содержит только legacy production JSON R06 из этого
диапазона, причем без slice-3 `resolution` и `context_export_policy`; R02-R05 и
R07-R12 отсутствуют. Поэтому catalog-shape tests ниже обязаны быть red до
публикации 3B, а не пропускать отсутствующие entries.

## 2. Oracle и способ параметризации

Oracle структуры resolver-а - импортированные и затем экспортированные
production JSON bytes; ожидаемый inventory зафиксирован таблицей раздела 3.
Release test manifest обязан явно сопоставить каждый concrete `skill_id` одной
logical row/semantic role этой таблицы, включая exact/contains criterion
variants. Такой manifest является acceptance data, а не dispatch map в core.
Test collector строит отдельный `ResolverCase` для каждого concrete resolver
document из следующих portable полей:

```text
skill_id, version, digest
output_contract.resolution.*
output_contract.facts[*]
output_contract.row_identity_fact_ids
output_contract.context_export_policy[*]
operation.parameter_bindings[*]
operation.column_bindings[*]
operation.pagination
parameters[*].semantic_type/entity_types/allowed_sources/context_slot_keys
result_constraints[*]
dependency_lock
```

Test data для MCP строится из fact/column declarations. Correct physical type
берется из exact `accepted_mcp_types`; wrong type получается детерминированной
мутацией, а не из core map. Choice labels строятся из
`candidate_label_fact_ids`; тест не сравнивает их с захардкоженной русской
строкой.

DeepSeek fixture возвращает structured plan по `skill_id`, fact signatures и
consumer contract. Текст `question_ru` является canary и не выбирает test path.
Ни один pass не может зависеть от наличия в core слов вроде названия сущности,
роли, вида склада, вида кассы или назначения. Русские имена metadata/query
допустимы внутри portable skill JSON и MCP fixture, но не являются
application-level oracle.

Для проверки трех use modes acceptance может импортировать test-only exact
consumers `entity_ref` и `entity_ref_list`. Они не входят в production catalog
и не получают status `pass`; они только заставляют PlanValidator вывести
`select_one`, `select_set` или `display_only` для production resolver-а.

## 3. Обязательная форма каталога

`Semantic protocol role` ниже означает одну точную комбинацию identity semantic
type, role/kind proof и default slot. Каждый predicate strategy реализуется
отдельным immutable skill document; collector раскрывает все concrete documents
в отдельные cases. Core-side retagging не считается variant.

| Ref | Обязательные concrete production `skill_id` | Тип позиции | Обязательная portable семантика | Context contract | Hard gate |
| --- | --- | --- | --- | --- | --- |
| R02 | `ut115.ref.item.details` | Exact-ref consumer, не resolver | Вход `entity_ref` exact `catalog.item`; безопасная fixed detail projection | Принимает `selection.item`; `resolution=null`, нет `selected_only` export | Returned item identity, если проецируется, имеет `fact_equals_parameter`; unit/barcode/detail rows не создают selection |
| R03 | `ut115.ref.item-group.resolve-name-contains`, `ut115.ref.item-group.resolve-code-exact`, `ut115.ref.item.group-members` | Два resolvers плюс exact-ref member consumer | Resolver identity `catalog.item.group`; member consumer возвращает `catalog.item` rows и exact selected-group fact | Resolvers: `selection.item_group`, `selected_only`, `session`; member consumer: `resolution=null`, exports `[]` | `group.is_group=true`; candidate pagination отделена от member pagination; descendants rule является declared binding, а не догадкой core |
| R04 | Для каждой роли `partner`, `customer`, `supplier`: `ut115.ref.<role>.resolve-name-contains`, `ut115.ref.<role>.resolve-code-exact`, `ut115.ref.<role>.resolve-inn-exact` | Девять predicate documents, три semantic roles | `party.partner`, `party.customer`, `party.supplier` | `selection.partner`, `selection.customer`, `selection.supplier` | Base partner не требует role retag; customer/supplier имеют hard role facts. Один physical ref/UUID может законно иметь две role-qualified semantic identities |
| R05 | `ut115.ref.partner.details`, `ut115.ref.customer.details`, `ut115.ref.supplier.details` | Три exact-ref consumers, не resolvers | Exact entry points для `party.partner`, `party.customer`, `party.supplier` без subtype inference | Принимают только соответствующий exact slot R04; `resolution=null`, нет нового selection export | Returned subject ref, если проецируется, равен parameter; contractor/contact/settlement rows не заменяют выбранного partner/customer/supplier |
| R06 | `ut115.ref.warehouse.resolve` | Resolver | Identity `catalog.warehouse` | `selection.warehouse`, `selected_only`, `session` | Только metadata-proven name, warehouse type/`is_retail` и optional department; organization/purpose не объявляются и не выводятся |
| R07 | `ut115.ref.cash-desk.enterprise.resolve`, `ut115.ref.cash-desk.pos.resolve` | Два protocol documents | `finance.cash_desk.enterprise` и `finance.cash_desk.pos` | `selection.cash_desk.enterprise`, `selection.cash_desk.pos` | Exact kind и owner organization equality; виды не объединяются по label и не retag-ятся в core |
| R08 | `ut115.ref.price-type.resolve` | Resolver | Identity `catalog.price_type` | `selection.price_type`, `selected_only`, `session` | Purpose/currency/VAT и retail/wholesale flags имеют metadata proof; пустой criterion universe допустим только для typed clarification/display |
| R09 | `ut115.ref.organization.resolve-name-contains`, `ut115.ref.organization.resolve-inn-exact`, `ut115.ref.organization.resolve-kpp-exact` | Три immutable predicate documents | Identity `party.organization` | `selection.organization`, `selected_only`, `session` | `organization.is_own=true`; `catalog.organization` и одноименный partner не являются alias; `Справочник.Организации.CodeLength=0`, поэтому code variant запрещен; source `Catalogs/Организации.xml`, SHA-256 `90c61aca0b35ac7f573685956b6c815736c5ab405483b765a4dcbfc58388c875` |
| R10 | `ut115.ref.item-characteristic.resolve-name-contains` | Resolver в exact item scope | Identity `catalog.item.characteristic` | `selection.item_characteristic`, `selected_only`, `session` | Parent item binding exact и имеет allowed source/provenance; candidate другого item invalid |
| R11 | `ut115.ref.item-series.resolve-name-contains`, `ut115.ref.item-series.resolve-number-exact` | Два resolvers в exact analytics scope | Identity `catalog.item.series`; item/characteristic/series relationship доказывается строкой `РегистрСведений.АналитикаУчетаНоменклатуры` | `selection.item_series`, `selected_only`, `session` | Required item и примененные optional characteristic/warehouse/purpose filters равны измерениям analytics row; `series.analytics_match=true`; source `InformationRegisters/АналитикаУчетаНоменклатуры.xml`, SHA-256 `4f88fd400e114ac936b34c710bee40efb7abe4a9f8c833b2a32619e90e52880e`; owner/type relation запрещена |
| R12 | `ut115.ref.inventory-purpose.resolve-name-contains` | Resolver | Identity `inventory.purpose` | `selection.inventory_purpose`, `selected_only`, `session` | Role proof пуст; related partner/contract/order/direction только safe scalar presentations, не entity exports; purpose не выводится из R06 |

Exact minimum R02-R12 содержит 27 concrete documents: 22 resolvers и 5
consumers при 12 semantic protocol roles. Разбивка resolver documents:
R03 (2), R04 (9), R06 (1), R07 (2), R08 (1), R09 (3), R10 (1), R11 (2),
R12 (1). Consumers: R02 (1), R03 members (1), R05 (3). Ни один concrete
document нельзя пропустить. Все пять consumers имеют `resolution=null` и не
проходят resolver 0/1/N state machine.

Clean-transfer artifact имеет exact имя `ut115-reference-1.1.0.package.json`,
`package_id=ut115.reference`, `version=1.1.0` и включает self-contained R01-R12
closure. Upgrade существующего starter catalog выполняется двумя disjoint
artifacts: `ut115-reference-existing-upgrade-1.1.0.package.json`
(`ut115.reference.existing-upgrade@1.1.0`, replace R01A-D/R06) и
`ut115-reference-slice3-additions-1.0.0.package.json`
(`ut115.reference.slice3-additions@1.0.0`, create R02-R05/R07-R12). Functional
assertions этой matrix по-прежнему ограничены R02-R12.
Разные semantic roles и predicate strategies используют отдельные immutable
skill IDs. Prefix/subtype inference, runtime role parameter, переключаемая
exact/contains query shape, `presentation` и physical type prefix не заменяют
concrete document.

## 4. Catalog-shape matrix

Каждая строка является blocking. Проверка идет по source JSON, exported JSON и
активному catalog snapshot.

| ID | Объект проверки | Positive expectation | Обязательная negative mutation / failure |
| --- | --- | --- | --- |
| `3B-CAT-001` | Полнота scope | Ровно 27 R02-R12 documents из frozen manifest: 22 resolvers, 5 consumers, 12 semantic roles; нет skip при отсутствии | Удаление/добавление любого Ref/role/document делает suite red с exact inventory diff |
| `3B-CAT-002` | Version/schema | Каждый измененный/new skill имеет новый SemVer, `schema_version=1.1.0`; у каждого parameter явно есть `context_slot_keys`, включая `[]` | Legacy 1.0 с inferred resolver/context policy rejected; старые bytes не переписываются |
| `3B-CAT-003` | Resolver declaration | `protocol=typed_entity_resolver_v1`, producer cardinality `many`, required non-null entity identity, identity входит в row identity | Unknown/extra/missing field -> `RESOLVER_CONTRACT_INVALID`; bad identity -> `RESOLVER_IDENTITY_FACT_INVALID` или `RESOLVER_ROW_IDENTITY_INVALID` |
| `3B-CAT-004` | Physical proof | Ровно один exact `object_ref` column binding identity с непустым allowlist из JSON | Missing/duplicate/wrong converter/empty allowlist -> `RESOLVER_PHYSICAL_PROOF_MISSING` |
| `3B-CAT-005` | Label/role proof | Label facts required/non-null, safe non-entity scalar и identifying; role facts required/non-null boolean или one-member enum; query фильтрует ту же роль | Entity/ref/JSON label, missing/nullable/false/out-of-domain role proof -> import reject либо runtime `contract_error`, никогда не candidate/public raw ref |
| `3B-CAT-006` | Context policy | Ровно одна matching `selected_only` policy identity/default slot, `lifetime=session`, `max_members<=100` | Wrong fact/slot/mode, entity under `confirmed_filter`, arbitrary extra export -> `CONTEXT_EXPORT_POLICY_INVALID`/`CONTEXT_EXPORT_MODE_INVALID` |
| `3B-CAT-007` | Exact/list consumers | R02, R03 members и все три R05 documents имеют cardinality `many`, `resolution=null`, exports `[]`, explicit context slot allowlist, exact semantic/cardinality/source and same-object constraint | Literal/model/user ref, wrong role slot, customer-to-partner subtype inference, member/detail entity export or missing equality proof rejected |
| `3B-CAT-008` | Parent-scoped R10/R11 | Parent parameters accept only declared `previous_step\|session_context` paths, exact semantic and slot; result relation is projected/proved | Same presentation with another parent, raw ref literal, dropped parent relation -> pre-MCP/runtime contract error |
| `3B-CAT-009` | R06 metadata boundary | Portable query/proof contains only declared direct warehouse selection attributes; existing stable keyset remains valid | Organization/purpose claim without proved join, prefix keyset regression or hidden cap fails import/contract gate |
| `3B-CAT-010` | Pagination/completeness | Каждый potentially unbounded resolver/member list has full slice-2 keyset proof; bounded producer has cited exact invariant and boundary tests | Prefix without invariant, cursor not covering full immutable identity, hidden drain or page-as-total fails |
| `3B-CAT-011` | Package lock/digest/ID uniqueness | Все три package name/id/version tuple exact; RFC 8785 skill digests, package integrity и dependency closure совпадают; в package одна версия каждого `skill_id`; activation atomic | Duplicate `skill_id` даже с другой version, orphan/extra/missing lock или embedded mutation rejected; revision/active bytes unchanged |
| `3B-CAT-012` | Built-in fixtures | Для каждого variant есть positive, confirmed-empty и malformed/wrong-type MCP case с exact required facts | Query error/schema error cannot be relabeled `not_found`; invalid rows are discarded |
| `3B-CAT-013` | Predicate immutability | Exact/contains/code/INN strategies имеют разные IDs и по одному fixed query/predicate/normalization contract | Model-controlled strategy switch, OR across criteria или fallback exact -> contains rejected |
| `3B-CAT-014` | Final entity mode contract | Final required entity с logical cardinality `one` и `zero_or_one` выводит `select_one` generic path | `zero_or_one` становится `display_only`, выбирает row без proof или требует entity-specific core branch |
| `3B-CAT-015` | Deployment topology | Full package creates clean catalog; existing-upgrade replaces only active R01A-D/R06 with exact `If-Match`; additions creates all 26 new R02-R05/R07-R12 documents; overlapping skill bytes identical | Mixed create/replace через unsupported single intent, partial artifact revision или digest drift между full/upgrade packages |
| `3B-CAT-016` | Bounded shortlist | В worst-case full catalog нужные exact role, predicate strategy и consumer входят в shortlist `<=16` по capability/fact/dependency/typed requirement signals | Inclusion зависит от конкретного русского token, нужный exact resolver вытеснен contains variant или limit превышен |
| `3B-CAT-017` | R11 analytics proof source | Оба R11 documents читают `РегистрСведений.АналитикаУчетаНоменклатуры`, связывают exact `Номенклатура`/`Характеристика`/`Серия` и примененные `МестоХранения`/`Назначение`, проецируют эти dimensions и `series.analytics_match=true`; provenance фиксирует exact metadata path и SHA-256 `4f88fd400e114ac936b34c710bee40efb7abe4a9f8c833b2a32619e90e52880e` | Mutation, удаляющая analytics register или заменяющая proof на `Номенклатура.ВладелецСерий`, `СерииНоменклатуры.ВидНоменклатуры`, прямую catalog owner/type связь либо иной/missing hash, rejected до activation; package revision и active bytes неизменны |

Import mutation tests parameterize at least: absent identity, nullable identity,
wrong role, identity absent from row identity, missing physical allowlist, unknown
label fact, wrong slot, entity under scalar policy, missing keyset proof, broken
skill digest, duplicate `skill_id` across versions and broken package lock.
Atomicity is asserted after every mutation.

Четыре catalog production-hardening проверки закрыты generic core commits
`b2a16fc` и `de8d7d4`, но остаются обязательными regression gates и не могут
быть отмечены `xfail`: duplicate `skill_id` независимо от version
(`3B-CAT-011`), safe scalar label facts (`3B-CAT-005`), collision rendered label
tuple (`3B-AMB-011`) и final `zero_or_one` mode
(`3B-CAT-014`/`3B-SM-013`).
Отдельный обязательный red gate для не реализованного executor path `rank`
определен разделом 9; он также не может быть `xfail` или skip.

## 5. Universal 0/1/N and mode matrix

Следующая matrix выполняется для каждого concrete resolver document из exact
3B release manifest и тем самым покрывает минимум 12 semantic roles. `N`
означает число distinct valid identity triples, а не число physical rows.
Invalid rows сначала дают `contract_error`; они не уменьшают и не увеличивают
`N`.

| ID | Distinct candidates / scope | Derived mode | Required outcome | Pending/context | External-call invariant |
| --- | --- | --- | --- | --- | --- |
| `3B-SM-001` | `0` | `select_one` | `success_empty`, reason `not_found` | Нет pending/export; прежний same-slot context не используется и не заменяется | Только resolver MCP; descendant не вызывается |
| `3B-SM-002` | `1`, complete | `select_one` | `selected_one`, затем terminal outcome downstream | При sufficient terminal closure или valid downstream empty один `cardinality=one` slot generation; при downstream failure commit отсутствует | Resolver один раз, каждый required descendant не более одного раза |
| `3B-SM-003` | `2..5`, complete | `select_one` | `clarification_required` | Typed pending с 2..5 opaque choices; export none; descendants blocked | Только resolver MCP до ответа |
| `3B-SM-004` | `>=6` или `has_more=true` | `select_one` | `clarification_required`, `has_more_candidates=true` | `choices=[]`; разрешен только typed `narrow`; export none | Нет hidden continuation/drain и descendant calls |
| `3B-SM-005` | `0` | `select_set` | `success_empty`, reason `not_found` | Нет пустого set handle и pending | Только resolver MCP |
| `3B-SM-006` | `1`, `complete_set` | `select_set` | `selected_set` из одного member | Один slot `cardinality=many`, `member_count=1` | Downstream получает list из exact одного full ref |
| `3B-SM-007` | `N=2..100`, `complete_set` | `select_set` | `selected_set` | Один shared handle, N unique members и N Evidence exports | Downstream получает exact set без reorder-dependent identity |
| `3B-SM-008` | incomplete/`has_more`/101 members | `select_set` | `partial` либо contract-defined continuation | Export/pending none; prior slot не заменяется | Нет consumer call и hidden drain |
| `3B-SM-009` | `0` | `display_only` | `success_empty` по producer empty semantics | Нет selection/pending | Только resolver MCP |
| `3B-SM-010` | `1` или `N>1` | `display_only` | Обычный list outcome с exact page metadata | Все rows display-only; export/pending none | Continuation следует slice-2 keyset без DeepSeek replan |
| `3B-SM-011` | Один resolver identity одновременно нужен как incompatible one и set/display selection | mixed | `contract_error / PLAN_RESOLVER_MODE_AMBIGUOUS` | Нет export/pending/mutation | Failure до первого MCP |
| `3B-SM-012` | Две rows с одной exact identity, complete | любой | Candidate/member dedup по exact triple | `select_one`: один candidate; `select_set`: один member; display сохраняет rows по output contract | Presentation/order не участвуют в dedup identity |
| `3B-SM-013` | Final entity requirement `one` или `zero_or_one` | `select_one` | Один generic 0/1/N path для обеих logical cardinalities | Selection/ambiguity rules идентичны; `zero_or_one` не ослабляет proof | Mode derived pre-MCP без semantic/object branches |
| `3B-SM-014` | Query error, MCP unavailable, malformed envelope, wrong type/role/proof | любой | Exact non-empty failure outcome или `contract_error`, никогда не `not_found` | Export/pending/mutation none; prior context unchanged | Descendants blocked; retry policy не превращает error в `0` |

Для R03 оба resolver documents независимо проходят 0/1/N. После exact выбора
группы отдельный `item.group-members` consumer получает full group ref и может
вернуть N paged member rows. Member count/`has_more` никогда не пересчитывается
как candidate count группы; item rows не становятся group choices и не
экспортируются. Result `selected_group.ref` обязан быть equal parameter.

Для R04 одна и та же physical UUID/type в customer и supplier instances остается
двумя semantic identities и двумя slot roles. Внутри одного role instance rows
deduplicate как одна identity. Core не создает base/customer/supplier identity
из текста запроса.

Для R07 enterprise и POS identities считаются раздельно. Mixed user result
представляется двумя typed lists/sections или explicit composition, но не одним
retagged set с общим slot.

## 6. Ambiguity и pending clarification

| ID | Сценарий | Expected pass condition | Immediate failure |
| --- | --- | --- | --- |
| `3B-AMB-001` | Две exact identities с одинаковым presentation и разными declared identifying facts | Две различимые labels из `candidate_label_fact_ids` и разные opaque `choice_id`; UUID/type отсутствуют в public DTO | Merge по presentation, выбор первой, игнорирование declared code/date/role или raw ref в DTO |
| `3B-AMB-002` | Choice из complete `2..5` | Resume stored DAG, bind exact chosen origin, execute blocked descendants once | DeepSeek replan, второй resolver call, export unchosen candidate |
| `3B-AMB-003` | Restart до choice | Pending handle, frozen catalog/marker/context version и candidates сохранены | Потеря pending или новый resolver search |
| `3B-AMB-004` | `>=6`/truncated then narrow | Frozen resolver, role/kind/parent bindings и old conditions сохраняются; добавляется один declared criterion | Choice из visible first five, смена resolver-а или ослабление parent/role filter |
| `3B-AMB-005` | R10/R11 narrow | Item и все примененные R11 analytics coordinates, включая characteristic, сохраняют exact bytes и provenance initial call; R11 source hash не меняется | Глобальный поиск characteristic/series по label, снятие analytics filter или переход на owner/type relation |
| `3B-AMB-006` | R07 narrow | Organization и cash-desk kind остаются frozen exact bindings | Смешение enterprise/POS или касса другой organization |
| `3B-AMB-007` | Unknown choice/cancel/reuse | Unknown choice не consumes; cancel consumes без external calls; повторный claim -> `CLARIFICATION_CONSUMED` | Любой MCP/DeepSeek на reject/cancel/reuse |
| `3B-AMB-008` | Two concurrent claims | Ровно один 202 winner, второй 409 consumed; один downstream execution | Два winners или duplicate business query |
| `3B-AMB-009` | Stale pending | Exact codes для expired, superseded, context version, catalog и marker changes; external calls zero | Resume на recent messages или guessed compatibility |
| `3B-AMB-010` | Foreign pending | `CLARIFICATION_SESSION_MISMATCH`, calls zero | Cross-session selection либо information leak |
| `3B-AMB-011` | Две different identities имеют одинаковый полный rendered label tuple | Typed pending допускает только `narrow`, `choices=[]`, pinned resolver/bindings сохранены | Два неразличимых selectable choices, tie-break по row order или добавление UUID/type в label |

## 7. Context, stale и foreign matrix

Matrix выполняется минимум для одного обычного resolver-а, каждого role/kind
variant family и каждого parent-scoped resolver-а. Property mutations затем
прогоняются параметрически по всем 12 semantic roles; exact/contains documents
одной роли дополнительно проходят exact canonical follow-up smoke.

| ID | Context condition | Required result | Call/context invariant |
| --- | --- | --- | --- |
| `3B-CTX-001` | Active same-session exact handle | Consumer получает full stored ref, равный origin по RFC 8785 canonical JSON | Нет поиска по label/name/number; resolver call count не растет |
| `3B-CTX-002` | Same triple, property order changed | Identity accepted; outbound value semantically/canonically unchanged | Property order не создает новое entity |
| `3B-CTX-003` | Same triple, new presentation and valid re-selection | New refreshed generation/handle, old `replaced`; identity unchanged | Presentation не участвует в identity |
| `3B-CTX-004` | Same presentation, another UUID | New candidate/entity; old handle не matches | Нельзя объединить или принять result equality |
| `3B-CTX-005` | Same UUID, another physical type | `contract_error / ENTITY_REF_CONTRACT_MISMATCH` | Consumer MCP zero или, для resolver result, descendants zero; mutation none |
| `3B-CTX-006` | Same UUID/type, another semantic type/role/kind | `contract_error / ENTITY_REF_CONTRACT_MISMATCH` | Нет prefix/subtype/role inference |
| `3B-CTX-007` | Replaced handle explicitly selected | `contract_error / CONTEXT_HANDLE_REPLACED` | DeepSeek/MCP zero; active replacement unchanged |
| `3B-CTX-008` | Expired/invalidated handle | `CONTEXT_HANDLE_EXPIRED` / `CONTEXT_HANDLE_INVALIDATED` | Calls zero; unrelated slots unchanged |
| `3B-CTX-009` | Handle from another session | `CONTEXT_HANDLE_SESSION_MISMATCH` | Calls zero; public response не раскрывает foreign slot |
| `3B-CTX-010` | Missing/corrupt origin fact/evidence/digest/binding | Slot fail-closed invalidated или `CONTEXT_PROVENANCE_MISSING` | No fallback search; session продолжает работать |
| `3B-CTX-011` | Explicit new mention resolves to `0`, ambiguity, partial или error while old slot exists | Old generation остается active historically, но shadowed и не используется в текущем request | Target consumer не вызывается; нет silent fallback к old slot |
| `3B-CTX-012` | Explicit new mention resolves to `1` | Только exact same `slot_key` получает next generation | Unmentioned item/warehouse/organization/role/kind/filter slots byte-for-byte unchanged |
| `3B-CTX-013` | Restart before follow-up | Active slot и origin chain восстанавливаются; exact ref доходит consumer | No resolver repeat/reconstruction |
| `3B-CTX-014` | Catalog revision/marker changed, producer contract все еще restorable | Durable active entity не retag-ится и не re-resolve-ится; exact consumer validation продолжается | Revision/marker alone не меняют identity; missing producer contract invalidates fail-closed |
| `3B-CTX-015` | Planner/public/trace surfaces | Public session/SSE/details и actual DeepSeek body содержат только opaque handle/type/slot/cardinality/presentation | Full ref разрешен только в protected replay artifacts; UUID/physical type отсутствуют снаружи |

Каждый pre-MCP reject фиксирует `mcp_call_count=0` в details/result record.
Любая failed replacement discards staged mutation целиком.

## 8. Reuse и composition matrix R02-R12

| ID | DAG / reuse | Required proof | Запрещенная подмена |
| --- | --- | --- | --- |
| `3B-CMP-001` | R01 selected item -> R02 details; затем R02 follow-up после restart | Exact `selection.item` ref bytes, producer provenance и result equality; downstream empty не удаляет item selection | Повторный R01 search, lookup по presentation, detail/barcode/unit row как новая selection |
| `3B-CMP-002` | R03 selected group -> group members/count | Group hard proof и descendants binding frozen; member item refs display/operand only | Одноименный item вместо group; member rows экспортируются как selected item set без отдельного `select_set` proof |
| `3B-CMP-003` | R04 customer -> R05 customer details | Exact role-qualified semantic, role fact и same physical identity; customer slot survives empty details | Base partner/contractor/settlement object retagged as customer |
| `3B-CMP-004` | R04 supplier display list | `display_only`, N rows, zero pending/export | Требование выбрать одного supplier только из-за N rows или export списка |
| `3B-CMP-005` | R04 dual-role physical entity | Customer and supplier slots may coexist with same physical ref but distinct semantic identity/provenance | Replacement одного role slot удаляет другой либо core role map |
| `3B-CMP-006` | R06 `select_one` -> exact warehouse consumer | Exact warehouse ref; used type/department facts traceable | Name heuristic, fictional organization relation, purpose from R06 |
| `3B-CMP-007` | R06 `select_set` -> stock probe | Only materialized `complete_set<=100`; exact list reaches consumer | Visible keyset page committed as all warehouses или hidden drain |
| `3B-CMP-008` | R09 organization -> R07 enterprise/POS display | Organization exact equality in every returned row; kinds remain distinct typed instances | Cash desk of another organization, merged kind by label, organization guessed from cash desk name |
| `3B-CMP-009` | R08 price type -> exact price consumer | Selected type ref plus applicable purpose/currency/VAT proof; N types uses pending and exact resume | Первый/самый похожий price type, recomputed choice after clarification |
| `3B-CMP-010` | Item -> R10 characteristic -> R11 series | R10 использует exact item; R11 получает exact item/characteristic slots и возвращает series только из analytics row с равными `Номенклатура`/`Характеристика`/`Серия`, `series.analytics_match=true` и pinned metadata hash; replacing characteristic cannot replace item | Global label search, characteristic as item, owner/type proof или series без exact analytics row |
| `3B-CMP-011` | R12 purpose -> inventory/stock probe | Exact `inventory.purpose` binding and compatible consumer slot | Warehouse purpose/name or arbitrary string substituted for entity ref |
| `3B-CMP-012` | Independent slots then replace one | Replacement key is exact portable `slot_key`; all other handles/value digests unchanged | Last-by-semantic scan, collateral clear, application object branch |
| `3B-CMP-013` | R11 foreign analytics coordinate | Два parameterized malformed-result cases: series имеет analytics row для другого item; series имеет analytics row для selected item, но другой characteristic. Оба дают `contract_error`, zero pending/export/downstream calls и не изменяют active item/characteristic/series slots | Совпадение name/number, series ref или owner/type не позволяет принять row, переиспользовать foreign series либо ослабить characteristic filter |

Composition acceptance checks actual MCP request parameters and
`fact_equals_parameter|fact_in_parameter` result constraints. Plan shape alone
не является доказательством.

## 9. Обязательный pre-production gate rank-one

Этот gate не добавляет документы к exact R02-R12 inventory, но блокирует
production acceptance 3B. `rank` уже является допустимым `operator_call` в
plan schema/domain model, однако одного schema pass недостаточно: executor
обязан выполнить operator и построить доказательство выбора. На текущем
baseline valid `rank` доходит до `OPERATOR_NOT_IMPLEMENTED`; соответствующие
тесты являются обычными blocking assertions, а не `xfail`, skip или
допустимым `not_run`.

| ID | Scenario | Positive expectation | Обязательный negative / invariant |
| --- | --- | --- | --- |
| `3B-RANK-001` | Schema -> executor | Валидный `operator=rank` реально исполняется после producer step; `limit` resolves to integer `1`; result step и Evidence имеют `collection_scope=complete_set` | Schema acceptance с runtime `OPERATOR_NOT_IMPLEMENTED`, пустым synthetic result или пропуском rank step делает gate red |
| `3B-RANK-002` | Unique rank-one winner | На complete input с distinct comparable `sort_fact_id` и доказанным total order `ascending` и `descending` выбирают exact ожидаемую identity; только winner получает `SelectionProof` и eligible export | Первый transport row, все ranked rows, non-winning entity/dimension или identity с другим semantic/physical type не экспортируются |
| `3B-RANK-003` | Tie determinism | `stable_first` допустим только при доказанном stable producer order с identity tie-break; permutations дают одного и того же winner. `include_all` при top tie не считается rank-one и требует typed clarification/selection без export | Неявный row-order tie-break, молчаливый выбор одного из tied rows или multi-member proof при required cardinality one |
| `3B-RANK-004` | Incomplete input | `has_more=true`, continuation, `partial` или иной scope кроме `complete_set` запрещает rank-one selection | Нет hidden drain; zero SelectionProof/context mutation/downstream calls; incomplete visible page не становится universe |
| `3B-RANK-005` | Invalid rank contract | Missing/null/non-comparable/wrong `sort_fact_id`, unresolved/mixed currency or unit, duplicate identity с conflicting rank value, invalid direction/ties и `limit != 1` rejected до selection/export | Нельзя coerce строку/дату/число по entity-specific правилам, сравнивать разные единицы, отбрасывать invalid rows или продолжать exact-ref consumer |
| `3B-RANK-006` | Reuse и portability | Один generic executor path проходит минимум на двух разных semantics: Q094 receipt и Q096 `party.customer`; exact winner после restart feeds matching exact-ref consumer без повторного producer/rank search | Ни semantic/physical/slot literal, ни русское слово, Q-ID или skill ID не появляется в operator dispatch; non-winners не входят в context |

Gate считается green только при реальном исполнении всех строк, включая
Evidence, call counts и context assertions. Наличие `RankOperator` в Pydantic
model, JSON schema или static plan validation не закрывает ни одну строку.

## 10. Evidence и observability для каждой runtime row

Каждый `3B-SM`, `3B-AMB`, `3B-CTX`, `3B-CMP` и `3B-RANK` result record содержит:

- active package/schema/app versions, catalog snapshot и database marker;
- production `skill_id`, version, digest, resolver spec digest и derived mode;
- valid row count, distinct candidate/member count и collection scope;
- SelectionProof digest либо явное отсутствие eligibility;
- pending transition или context before/after handles;
- Evidence 1.1 digest, required coverage и `collection_scope`;
- DeepSeek/MCP call deltas и exact assertion status.

Обязательные cross-artifact assertions:

| Outcome | Evidence/context expectation |
| --- | --- |
| `selected_one` | Ровно один identity fact export с новым handle, если sufficient terminal closure достигнута |
| `selected_set` | N identity fact exports с одним shared handle и exact unique membership digest |
| Ambiguity / truncated / partial / empty / contract error | `context_exports=[]`, staged mutation discarded |
| `display_only` | Все entity rows остаются Evidence facts, но `context_exports=[]` |
| Selected entity + valid downstream empty | Selected identity остается eligible; empty detail/business result не отменяет selection |
| R02/R05/detail/member/line dimensions | Никакого нового selection export без отдельного resolver/SelectionProof |

`GET /turns/{id}/details`, normal trace events и public context не содержат raw
refs. Diagnostic ZIP содержит как минимум `resolver-proofs.json`,
`context-mutations.json`, `planner/http-request.json` и `evidence.json` для
соответствующего turn; protected `planner/request.json` сохраняет replay data.

## 11. JSON portability matrix

| ID | Scenario | Pass condition | Failure condition |
| --- | --- | --- | --- |
| `3B-PORT-001` | Source package import в clean `APP_DATA_DIR` | Exact `ut115-reference-1.1.0.package.json` активирует 27 R02-R12 documents внутри full R01-R12 closure одним atomic revision, exact digests совпадают | Partial activation, wrong package id/version или inventory diff |
| `3B-PORT-002` | Public export -> second clean import | Export bytes byte-for-byte stable; RFC 8785 skill documents, versions, digests, policies, locks и query templates совпадают | Reformat/regeneration, потеря resolution/context field или semantic/slot/physical mapping |
| `3B-PORT-003` | JSON object/property reorder | Integrity и business identity остаются valid после canonicalization | Raw serialization order влияет на digest/identity |
| `3B-PORT-004` | Runtime smoke во втором instance | Data-driven `0`, `1`, `N` на каждый из 22 resolver documents и exact follow-up/wrong-type на 12 semantic roles без source change; R11 сохраняет analytics metadata path/hash | Новые слова/map/branch нужны для R04 role, R07 kind, R10/R11 scope или R12 purpose; R11 proof заменен owner/type relation |
| `3B-PORT-005` | Restart imported instance | Active slots и pending сохраняются; package/digest restorable | Re-import/re-resolution нужен после restart |
| `3B-PORT-006` | Export hygiene | Нет absolute paths, secrets, fixture-only values и host-specific bytes | `/Users/`, drive path, token или local source path в package |
| `3B-PORT-007` | Atomic invalid package | Каждая mutation из `3B-CAT` rejected, catalog revision и prior bytes unchanged | Частичная замена или inferred defaults |
| `3B-PORT-008` | Core portability | Existing synthetic unseen semantic/physical/slot suite остается green; production semantic/slot/physical literals не появляются как dispatch map в `src/chatbot1c` | Object/Q-ID/prefix/lexical selection branch в core |
| `3B-PORT-009` | Web и CLI import/hot visibility | Оба пути импортируют те же bytes/digests; следующий новый turn видит committed snapshot без restart, уже active turn остается на pinned old snapshot | Starter filename discovery как requirement, restart-only activation или mid-turn catalog swap |
| `3B-PORT-010` | Existing starter upgrade | Exact snapshot -> replace package -> additions create package дает две complete revisions и тот же final R01-R12 skill/digest subset, что full package в clean catalog | Один mixed-intent call, половина additions, wrong `If-Match`, R01 external lock drift или различие reference subset |

Source scan для `3B-PORT-008` не ищет и не требует конкретные русские слова.
Он проверяет structural branch patterns и значения semantic type/slot/physical
allowlist, извлеченные из JSON, плюс existing unseen synthetic case.

## 12. Non-regression matrix

| ID | Gate | Mandatory expectation |
| --- | --- | --- |
| `3B-NR-001` | Full `tests/acceptance_slice3` | Existing synthetic 0/1/N, pending, context, scalar, opacity, migration и portability tests green |
| `3B-NR-002` | Slice-2 keyset/continuation | R01A-D exact/contains split не меняется; R03/R04/R06-R12 unbounded paths obey exact keyset; no hidden drain |
| `3B-NR-003` | R06 contract | Existing name/type/department boundary and keyset traversal remain green; no organization/purpose overclaim |
| `3B-NR-004` | Evidence | New writes remain exactly Evidence 1.1 with explicit `steps[*].collection_scope` and `coverage.requirements[*].required`; legacy 1.0 read has no rewrite |
| `3B-NR-005` | Outcome/failure semantics | Empty, query error, MCP unavailable, malformed/schema mismatch and partial stay distinct; failures do not mutate context |
| `3B-NR-006` | Package compatibility | Legacy 1.0 skills remain readable/exportable with no inferred resolution/policy; all changed resolver producers use new SemVer/digest |
| `3B-NR-007` | Public privacy | Existing canary scan for UUID, `_objectRef`, physical type, raw scalar, secret и absolute path remains green |
| `3B-NR-008` | Corpus components | Q013-Q021, Q024-Q030, Q056, Q073, Q081-Q082, Q091-Q097, Q108 и R10-R12 property negatives match resolver/context expectations; rank-one Q094/Q096 не может быть `not_run`/`xfail`; недоступный внешний downstream остается explicit `not_run` |
| `3B-NR-009` | Distribution topology | Existing `ut.starter.slice-*.package.json` остаются bootstrap/test artifacts; production pass доказывается exact `ut115.reference` package через normal import paths, fixed starter filename не считается portability gate |

Minimum regression command includes the full slice-3 suite and all slice-2
acceptance files that cover keyset, continuation, skill composition, outcomes
and Evidence. Running only `test_slice2_non_regression.py` is insufficient for
production 3B because it does not recertify the R06 query/keyset boundary.

```bash
uv run pytest -q \
  tests/acceptance_slice3 \
  tests/acceptance_slice2/test_followup_keyset_evidence.py \
  tests/acceptance_slice2/test_pagination_continuations.py \
  tests/acceptance_slice2/test_skill_contracts_composition.py \
  tests/acceptance_slice2/test_outcomes_failures.py \
  tests/contract/test_skill_v11_context_contract.py \
  --tb=short
```

## 13. Минимальный обязательный набор автотестов

Допускается другая раскладка файлов, но следующие семь parameterized groups
обязательны. Один test function может покрывать много cases; collection обязан
падать, если inventory отличается от 27 concrete documents, 22 resolvers,
5 consumers или 12 semantic roles.

| Group | Минимальное содержание |
| --- | --- |
| `production_catalog_contract` | `3B-CAT-001..017`, exact 27-document R02-R12 inventory, package topology, bounded shortlist, safe labels, predicate immutability, duplicate-ID/import mutation atomicity, lock/digest и exact R11 analytics source/hash; owner/type bypass mutation обязана fail |
| `production_resolver_state_machine` | Полный cartesian `22 concrete resolver documents x {select_one,select_set,display_only} x {0,1,N}` с покрытием 12 semantic roles; для каждого document source-error/wrong-proof, плюс duplicate, incomplete, 101, `zero_or_one` и mixed-mode cases |
| `production_resolver_clarification` | Complete 2/5, full-label collision, 6/has_more narrow, choose/restart/cancel/reuse/concurrency и stale/foreign pending |
| `production_resolver_context` | Exact canonical binding, identity property mutations, stale/foreign/expired/invalidated/provenance paths, restart и opaque planner/public surfaces |
| `production_resolver_composition` | Все `3B-CMP-001..013`, включая R02/R05, R04 roles, R07 kinds, R10/R11 scope и отдельные R11 foreign-item/foreign-characteristic cases |
| `production_rank_one_gate` | Все `3B-RANK-001..006`: реальное generic execution, unique winner, asc/desc, deterministic ties, incomplete/invalid rejection, SelectionProof/export, restart reuse и Q094/Q096 portability; ordinary blocking assertions без `xfail`/skip |
| `production_resolver_portability_regression` | Clean import/export/re-import/runtime, no source branch, full existing slice-3 suite и relevant full slice-2 acceptance suites |

Минимальные boundary values нельзя сокращать до одного произвольного `N`:
обязательны `0, 1, 2, 5, 6, 100, 101`, duplicate identity и `has_more=true`.
Property mutations обязательны для same label/different UUID, same
UUID/different physical type, same physical identity/different semantic type и
same exact triple/new presentation/property order.
Для каждого из 22 resolver documents также обязателен отдельный source-error
case; wrong-role применяется к role-qualified documents, parent mismatch к
R10, analytics item/characteristic mismatch к R11, owner mismatch к R07.

## 14. Exit rule

Production resolver catalog 3B получает `pass` только если одновременно:

1. Все 27 R02-R12 documents, включая 22 resolvers, 5 consumers и 12 semantic
   protocol roles, присутствуют в imported/exported package и проходят
   catalog-shape gates.
2. Вся universal state matrix проходит на fixture transport, включая exact
   call counts, Evidence и context mutations.
3. R04 role, R07 kind, R10 scope, R11 exact analytics relationship и R02/R05
   exact consumers доказаны composition tests без retag/subtype/object
   branches; R11 owner/type bypass rejected.
4. Pending и active context переживают restart; stale и foreign handles fail
   before external calls.
5. Exported JSON byte-stable переносится web/CLI путями в второй clean instance
   без source change и без потери digest/lock/contract semantics; новый turn
   видит package без restart.
6. Все `3B-RANK-001..006` green на реально исполняемом generic rank-one path;
   schema-only support и `OPERATOR_NOT_IMPLEMENTED` не принимаются.
7. Full existing slice-3 и relevant slice-2 non-regression gates green.
8. Live-required rows имеют самостоятельный `pass` либо честный
   `blocked_external/not_run`; fixture success не подменяет live evidence.

Любой missing case, skipped production Ref, inferred role/kind, raw-ref leak,
visible-page selection, lexical core dependency, R11 proof вне analytics
register, skipped rank-one gate или partial package activation делает общий
status 3B равным `fail`.
