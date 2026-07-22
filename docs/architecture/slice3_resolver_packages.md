# Production-пакеты resolver skills для типовой УТ

- Статус: архитектурное решение
- Целевой контракт: `skill`/`skill_package` schema `1.1.0`
- Целевой профиль первой поставки: `УправлениеТорговлейБазовая / 11.5.27.56 / 8.3.27`

## 1. Решение

Production resolver остается обычным atomic `data_query` skill schema `1.1.0`.
Отдельный тип документа, executable plugin, semantic registry или application map
для сущностей не вводится. Универсальная семантика выбора задается уже
существующими `output_contract.resolution`, `context_export_policy`, typed facts и
consumer parameters; физическая реализация УТ задается `compatibility`,
immutable query и exact bindings того же skill.

Семантическая роль и физический объект 1С разделяются внутри одного
самодостаточного skill contract, но не разносятся по независимо версионируемым
документам:

| Слой | Нормативные поля | Что в нем запрещено |
| --- | --- | --- |
| Semantic role | `skill_id`, `provides.fact_types`, `output_contract.facts[*].semantic_type`, `resolution`, `default_slot_key`, `role_proof_fact_ids`, consumer `semantic_type/entity_types/context_slot_keys` | Имена metadata, `ТипОбъекта`, вывод типа по префиксу или presentation |
| 1С binding | `compatibility`, `required_metadata`, `query_template`, `parameter_bindings`, `column_bindings.accepted_mcp_types`, provenance | Переназначение semantic type, создание context slot или выбор первого кандидата |
| Доказательная связь | Один exact `fact_id`, producer evidence и `(skill_id, version, digest)` | Application-словарь `semantic_type -> ТипОбъекта` и реконструкция `_objectRef` |

Разносить semantic descriptor и 1С binding в два JSON не следует. Schema 1.1
проверяет атомарную согласованность query, output facts, resolution и physical
column binding; раздельные документы создали бы неприкрепленный к digest join и
потребовали бы нового core resolver registry.

Функционально текущего `typed_entity_resolver_v1` достаточно. Новые типы
сущностей, роли, slot keys и физические типы добавляются JSON-пакетом без
entity-specific изменений application code. Перед production activation нужны
только перечисленные в разделе 9 общие hardening-проверки core; расширять schema
или protocol для самой структуры пакетов не требуется.

## 2. Граница поставки и файловая структура

Canonical transport для clean install является одним self-contained package
JSON. Upgrade уже активного starter catalog использует два disjoint package JSON
из-за единого `create|replace` intent текущего import API. Отдельные `.skill.json`
остаются authoring-единицами и должны детерминированно встраиваться в packages;
runtime не разрешает относительные ссылки на файлы.

```text
skills/ut-11.5.27.56/
  ut115-reference-1.1.0.package.json
  ut115-reference-existing-upgrade-1.1.0.package.json
  ut115-reference-slice3-additions-1.0.0.package.json
  ut115.ref.item.resolve-article-exact.skill.json
  ut115.ref.item.resolve-code-exact.skill.json
  ut115.ref.item.resolve-barcode-exact.skill.json
  ut115.ref.item.resolve-name-contains.skill.json
  ut115.ref.item.details.skill.json
  ut115.ref.item-group.resolve-name-contains.skill.json
  ut115.ref.item-group.resolve-code-exact.skill.json
  ut115.ref.item.group-members.skill.json
  ut115.ref.partner.resolve-name-contains.skill.json
  ut115.ref.partner.resolve-code-exact.skill.json
  ut115.ref.partner.resolve-inn-exact.skill.json
  ut115.ref.customer.resolve-name-contains.skill.json
  ut115.ref.customer.resolve-code-exact.skill.json
  ut115.ref.customer.resolve-inn-exact.skill.json
  ut115.ref.supplier.resolve-name-contains.skill.json
  ut115.ref.supplier.resolve-code-exact.skill.json
  ut115.ref.supplier.resolve-inn-exact.skill.json
  ut115.ref.partner.details.skill.json
  ut115.ref.customer.details.skill.json
  ut115.ref.supplier.details.skill.json
  ut115.ref.warehouse.resolve.skill.json
  ut115.ref.organization.resolve-inn-exact.skill.json
  ut115.ref.organization.resolve-kpp-exact.skill.json
  ut115.ref.organization.resolve-name-contains.skill.json
  ut115.ref.cash-desk.enterprise.resolve.skill.json
  ut115.ref.cash-desk.pos.resolve.skill.json
  ut115.ref.price-type.resolve.skill.json
  ut115.ref.item-characteristic.resolve-name-contains.skill.json
  ut115.ref.item-series.resolve-name-contains.skill.json
  ut115.ref.item-series.resolve-number-exact.skill.json
  ut115.ref.inventory-purpose.resolve-name-contains.skill.json
```

Минимальный package включает перечисленные `code-exact`/`inn-exact` variants для
partner/customer/supplier и organization. Один skill означает одну semantic role
и одну фиксированную predicate shape. В один query нельзя помещать переключаемые
моделью exact/contains стратегии.

Logical R03 раскладывается на name/code item-group resolvers и
`ut115.ref.item.group-members`. Resolver variants разрешают только group
identities, consumer принимает exact group ref и пагинирует member rows с
`resolution=null`. Один
combined skill небезопасен: общий pagination `has_more` не различает неполное
множество group candidates и неполный список members, а core обязан принимать
решение о `select_one` только по полному candidate scope. `ut115.ref.item.details`
и три role-exact details variants также являются consumers с `resolution=null`;
они входят в reference package ради замкнутой reference composition, но сами
context selection не создают.

Это решение уточняет inventory `full_catalog_blueprint.md` для schema 1.1:

- R04 раскладывается минимум на три immutable protocol documents для
  `party.partner`, `party.customer` и `party.supplier`;
- R05 имеет отдельные exact consumer documents для тех же semantic entry points;
- R07 раскладывается на enterprise и POS protocol documents;
- R03 разделяет candidate resolution и member pagination, а R04/R07 не используют
  core-side retagging или typed union identity.

Следовательно, прежние числа `15 reference skills` и `57 atomic skills` нельзя
использовать как release gate после этой декомпозиции. Exact release manifest
считает concrete documents и явно отображает их в логические R02-R12; добавление
новых criterion variants увеличивает число документов, но не число semantic
roles. В частности, три R03 documents отображаются в один logical capability,
но проходят независимые resolver и consumer contract cases.

Package имеет:

- `schema_version = 1.1.0`;
- `package_id = ut115.reference`;
- `version = 1.1.0` для первой полной typed-context поставки, независимо от
  schema version;
- exact `target` первой поставки;
- все R01-R12 resolver и exact-consumer variants в `skills` как embedded roots;
- exact closed `dependency_lock` с одной записью на каждый embedded skill и его
  транзитивную skill dependency;
- portable provenance URI без local paths;
- package integrity по RFC 8785/SHA-256.

Публикуются три представления одного набора immutable skill documents:

| Artifact / `package_id@version` | Состав | Import intent |
| --- | --- | --- |
| `ut115-reference-1.1.0.package.json` / `ut115.reference@1.1.0` | Полный self-contained R01-R12 closure | `create` в clean catalog и перенос между clean instances |
| `ut115-reference-existing-upgrade-1.1.0.package.json` / `ut115.reference.existing-upgrade@1.1.0` | Только уже активные в starter R01A-D и R06 с новыми schema 1.1 versions/digests | `replace` с `If-Match` exact active snapshot digest |
| `ut115-reference-slice3-additions-1.0.0.package.json` / `ut115.reference.slice3-additions@1.0.0` | Новые R02-R05/R07-R12 variants; R01 exact versions/digests остаются external lock entries | `create` после успешного upgrade artifact |

Во всех пересекающихся artifacts одна `(skill_id, version, digest)` обязана иметь
byte-identical embedded skill document. Два upgrade imports создают две
целые catalog revisions: после первой старый рабочий catalog только получает
typed R01/R06, после второй добавляется остальной reference scope. Ни одна
revision не содержит половину одного package.

Разделение необходимо из-за текущего единого import mode: `create` отклоняет
active IDs, а `replace` отклоняет отсутствующие IDs. Если deployment требует
одной общей revision для одновременного replace существующих и create новых
skills, нужен generic mixed-intent catalog import; schema resolver-а это не
решает.

Resolver-bearing document producers остаются у владельца бизнес-семантики, а не
дублируются в reference package:

| Domain package | Resolver-bearing producer |
| --- | --- |
| `ut115.sales-purchases` | заказ клиента, реализация, заказ поставщику, поступление по exact number/typed criteria |
| `ut115.stock-logistics` | перемещение и другие складские документы по exact number/typed criteria |
| `ut115.cash-finance` | document/entity producer, если exact cash operation требует дальнейшего composition |

Например, `ut115.sales.order-header-status-by-number` может одновременно вернуть
проверенную шапку и объявить `resolution` для `document.sales_order`. Создавать
второй query только ради получения той же ссылки не нужно.

Текущие `ut.starter.slice-*.package.json` остаются bootstrap/test artifacts и не
являются production distribution topology. Builtin auto-import сейчас читает один
фиксированный starter filename. Production package переносится через
существующий web/CLI import либо задается оператором как `STARTER_PACKAGE_PATH`
для пустого каталога; обнаружение нового builtin filename не должно быть условием
переносимости.

## 3. Канонические semantic roles

Один semantic role имеет собственный identity fact и slot. Совпадение
физического `_objectRef` между ролями не дает semantic совместимость.

| Role | Semantic identity | Default slot | Обязательное доказательство сверх exact binding |
| --- | --- | --- | --- |
| item | `catalog.item` | `selection.item` | non-group boolean/singleton-enum proof |
| item group | `catalog.item.group` | `selection.item_group` | group boolean/singleton-enum proof |
| partner | `party.partner` | `selection.partner` | нет, если query действительно выбирает базового партнера |
| customer | `party.customer` | `selection.customer` | required non-null customer-role proof |
| supplier | `party.supplier` | `selection.supplier` | required non-null supplier-role proof |
| warehouse | `catalog.warehouse` | `selection.warehouse` | requested type/department должны быть query filters и output facts, но не выводятся из имени |
| organization | `party.organization` | `selection.organization` | own-organization producer proof |
| enterprise cash desk | `finance.cash_desk.enterprise` | `selection.cash_desk.enterprise` | singleton kind proof |
| POS cash desk | `finance.cash_desk.pos` | `selection.cash_desk.pos` | singleton kind proof |
| price type | `catalog.price_type` | `selection.price_type` | purpose/currency/VAT facts только при metadata proof |
| item characteristic | `catalog.item.characteristic` | `selection.item_characteristic` | exact selected item input, если связь обязательна для query |
| item series | `catalog.item.series` | `selection.item_series` | exact selected item и characteristic inputs, если они обязательны для query |
| inventory purpose | `inventory.purpose` | `selection.inventory_purpose` | exact metadata producer proof |
| sales order | `document.sales_order` | `selection.sales_order` | exact document kind |
| sales shipment | `document.sales_shipment` | `selection.sales_shipment` | exact document kind |
| purchase order | `document.purchase_order` | `selection.purchase_order` | exact document kind |
| purchase receipt | `document.purchase_receipt` | `selection.purchase_receipt` | exact document kind |
| stock transfer | `document.stock_transfer` | `selection.stock_transfer` | exact document kind |

Customer и supplier должны быть разными skills даже тогда, когда обе роли
физически представлены одной ссылкой партнера. Skill с параметром `role` и
identity fact `party.partner` не вправе на runtime переименовать его в
`party.customer` или `party.supplier`. Аналогично enterprise/POS cash desk не
являются одним typed union resolver, если downstream contracts различают их.

Массив из нескольких `accepted_mcp_types` допустим только когда все физические
варианты имеют одну и ту же business semantics и один consumer contract. Он не
является механизмом semantic union.

## 4. Atomic resolver contract

Каждый resolver skill обязан удовлетворять следующим правилам.

1. `schema_version` равен `1.1.0`; каждый parameter явно содержит
   `context_slot_keys`, включая пустой массив.
2. Operation равна immutable read-only `data_query`; producer cardinality всегда
   `many`, в том числе для article/code/barcode/number exact.
3. Один required, non-null `entity_ref` fact с `role=entity` является
   `resolution.identity_fact_id` и входит в `row_identity_fact_ids`.
4. Ровно один `column_binding` identity fact имеет `converter=object_ref` и exact
   case-sensitive `accepted_mcp_types`, подтвержденные metadata/query/live proof.
5. `candidate_label_fact_ids` содержит только required non-null безопасные
   scalar facts: наименование плюс различающие code/article/INN/number/date/type
   или parent. Entity refs, UUID, physical type и произвольный JSON не являются
   label facts.
6. `role_proof_fact_ids` содержит required non-null boolean facts, где `true`
   означает соответствие, либо enum facts с одним exact `allowed_values` member.
   Query одновременно фильтрует ту же роль; proof не заменяет filter.
7. `context_export_policy` содержит ровно один matching `selected_only` policy
   для identity fact/default slot, `lifetime=session`, `max_members<=100`.
8. Resolver не экспортирует другие entity dimensions. Дополнительные refs могут
   быть evidence facts, но не получают `selected_only` policy без собственного
   отдельного resolver protocol.
9. Exact search и contains search являются разными skill IDs. Exact skill не
   деградирует к contains при пустом или неоднозначном результате.
10. Для возможности `narrow` у name resolver есть ровно один изменяемый
    `string|normalized_text` argument. Parent refs, role/kind и закрытые filters
    остаются frozen typed arguments.

Identity кандидата равна точной тройке
`(semantic_type, ТипОбъекта, UUID)`. Presentation и label facts в identity не
входят. Повторные rows одной тройки дедуплицируются; одинаковая presentation у
разных UUID остается неоднозначностью.

## 5. Composition и dependencies

Skill dependency означает availability, а не скрытый вызов. Каждый resolver и
consumer остается отдельным `skill_call` в plan DAG; exact `StepBinding` связывает
identity fact producer с parameter consumer.

Consumer entity parameter обязан объявить:

- `value_type=entity_ref` или `entity_ref_list`;
- exact business `semantic_type` и тот же type в `entity_types`;
- только `previous_step`/`session_context` как `allowed_sources`;
- exact `context_slot_keys`, совместимые с semantic role;
- `normalization=object_ref`;
- `result_constraint`, когда consumer утверждает возврат того же объекта.

Иерархии semantic types нет. `party.customer` не удовлетворяет
`party.partner`, а `catalog.item.group` не удовлетворяет `catalog.item`, если это
не отдельный явно произведенный fact с собственным доказательством.

Schema dependencies не умеет выражать alternative producers. Поэтому для каждого
semantic role назначается один canonical availability producer, обычно
`resolve-name-contains`. Consumer зависит от него по `skill_id`, совместимому
version range и минимальному `required_fact_types`. Runtime coverage вправе
использовать специализированный exact resolver с той же exact fact signature.
Canonical edge никогда не разрешает name resolver для exact-article intent.

Обязательные reference edges первой поставки:

- item details и item-consuming business skills -> canonical item resolver;
- item group members -> canonical item-group resolver;
- partner details -> canonical partner resolver;
- item characteristic -> canonical item resolver;
- item series -> canonical item resolver + characteristic resolver;
- role-specific customer/supplier consumers -> соответствующий role-qualified
  resolver, не base partner resolver;
- document lines/details -> resolver-bearing header/list producer того же exact
  document semantic type.

Full reference package embeds собственное замыкание и устанавливается первым.
Delta package фиксирует уже active R01 как external lock. Business packages не
дублируют reference skills: их `dependency_lock` содержит exact external
`(skill_id, version, digest)`, который уже активен. Для переноса в чистый instance
используется full package либо существующий export selected roots with embedded
closure; никакого network dependency resolution или implicit upgrade нет.

В одном installation package допускается ровно одна версия каждого `skill_id`.
Разные predicate strategies имеют разные IDs, а не две версии одного ID.

## 6. Безопасная неоднозначность

Core выводит mode из validated DAG, а package не задает selection flag:

| Distinct valid candidates | `select_one` | `select_set` | `display_only` |
| --- | --- | --- | --- |
| 0 | confirmed not found, без export | confirmed not found, без export | обычный empty outcome |
| 1 | selected one | complete one-member set | только отображение |
| N > 1 | pending clarification, descendants blocked | только complete bounded set в `max_members` | обычный список без context export |
| incomplete/truncated | только `narrow`, без selectable partial choices | partial, без export | обычная pagination |

Ни порядок rows, ни score, ни первый exact match не разрешают `select_one`.
Customer/supplier/item-group candidates участвуют в state machine только после
успешных role proofs. Ambiguous, partial, failed и display-only facts не меняют
context ledger.

Для полного множества из `2..5` choices public label строится только из
`candidate_label_fact_ids`; choice хранит server-side exact origin facts. Если
две разные identities имеют одинаковый итоговый label tuple, они не должны
показываться как два неразличимых selectable choices: допустим только `narrow` с
тем же pinned resolver. При `has_more`, truncation или более пяти candidates
также разрешен только `narrow`; hidden drain запрещен.

Ответ `choose` возобновляет сохраненный DAG без нового planner call и повторного
resolver query. Pending связан с session/context version, exact catalog snapshot,
database marker и сроком; replace package или marker change делает checkpoint
непригодным. Выбранный context сохраняет исходный полный `_objectRef`, producer
version/digest и exact column proof.

## 7. Versioning, digests и replacement

Версии независимы и имеют разные назначения:

| Версия | Правило |
| --- | --- |
| `schema_version` | Всегда `1.1.0` для typed resolver; не следует повышать ради нового entity type или query |
| `skill.version` | SemVer одной semantic role + predicate strategy + physical implementation |
| `output_contract.contract_version` | SemVer semantic output/resolution contract; меняется при изменении facts, identity, role proof или slot policy |
| `package.version` | SemVer состава distribution package и exact lock closure |

Любое изменение canonical skill JSON требует новой `skill.version` и digest.
Одна пара `(skill_id, version)` имеет ровно один RFC 8785/SHA-256 digest навсегда.
Миграция существующих schema 1.0 resolver skills на typed resolution использует
следующую свободную версию; текущую пару с новым digest переиспользовать нельзя.

SemVer policy:

- patch: исправление query/provenance/test/display без изменения typed inputs,
  fact signatures, semantic role, identity, slot или ambiguity behavior;
- minor: backward-compatible optional typed criterion или дополнительный label
  fact, не меняющий identity/role/context contract;
- major: изменение semantic type, predicate meaning, identity fact, required
  parent, role proof, default slot, context policy, cardinality или допустимого
  physical object family.

Новая role-qualified сущность получает новый `skill_id`, а не major version
base-role resolver. Изменение target release или metadata/query binding создает
новые skill versions и package digest даже при неизменной semantic role.

Package digest считается по package без top-level `integrity`, но с integrity
всех embedded skills. `dependency_lock` точно равен embedded roots плюс
транзитивные dependencies; missing, extra, ambiguous version или digest conflict
отклоняет package целиком до catalog transaction.

Create/replace intent остается вне JSON. Multi-skill replace использует exact
active catalog snapshot digest; следующий turn видит новый immutable snapshot,
текущий turn заканчивает на старом. Existing durable context не пересобирается по
новому skill: он продолжает доказываться историческим producer document. Если
новая major version несовместима по semantic role/slot, она обязана использовать
новый semantic type или slot, чтобы старый context не подошел consumer-у.

## 8. Перенос без обновления приложения

Один и тот же application build может принять новый resolver package через
существующий web/CLI import, если соблюдены четыре условия:

1. package использует уже поддерживаемые schema `1.1.0`,
   `typed_entity_resolver_v1`, operation/converters и runtime contracts;
2. target точно совпадает с активным configuration profile;
3. metadata assertions, query, fixtures, provenance и dependency closure проходят
   общий import pipeline;
4. semantic types, slot keys и physical types находятся только в JSON contract,
   без ожидания, что core заранее знает их имена.

После atomic commit следующий turn видит package без restart. Exported canonical
bytes импортируются в чистый compatible `APP_DATA_DIR` с теми же skill/package
digests. Абсолютные пути, instance UUID, реальные business values и локальные
filenames в provenance запрещены.

Эта гарантия относится к поддерживаемому профилю первой поставки. Она не означает
перенос того же physical package на другую версию/редакцию УТ. Для другого target
нужен новый metadata-proven package; текущий core дополнительно фиксирует
`11.5.27.56` в Settings/Evidence/database marker, поэтому multi-release без
обновления приложения пока не поддержан.

## 9. Оценка изменений core

Новая resolver package topology, semantic roles и 1С bindings не требуют новой
schema, нового protocol или entity-specific application code. Synthetic schema
1.1 package уже доказывает импорт и выполнение неизвестной core semantic/physical
пары в двух чистых data directories.

При review baseline были выявлены следующие generic hardening changes core:

1. Package semantic validation должна отклонять второй embedded skill с тем же
   `skill_id` независимо от version. Сейчас проверяется только уникальность пары
   `(skill_id, version)`, тогда как active catalog хранит одну запись на ID.
2. `candidate_label_fact_ids` должны быть import-time ограничены безопасными
   non-entity scalar facts. Сейчас identity `entity_ref` может быть объявлен label
   fact и попасть в public label вместе с physical type/UUID.
3. Runtime должен сравнивать rendered label tuples разных identities: collision
   переводит clarification в `narrow` без choices. Это generic state-machine rule,
   не справочник конкретных сущностей.
4. Вывод resolver mode для final required entity должен соответствовать
   нормативному `one|zero_or_one`; оба варианта используют один строгий
   `select_one` protocol.

Пункты 1-2 закрыты generic semantic validation в commit `b2a16fc`; пункты 3-4
закрыты generic resolver state-machine в commit `de8d7d4`. Их тесты остаются
обязательными release gates. Ни один из них не потребовал semantic-to-physical
map или новой версии wire schema.

Единый import с mixed create/replace текущим CatalogService не поддержан. Для
принятой двухшаговой upgrade topology это не blocker. Он становится generic core
change только при требовании атомарно мигрировать весь starter catalog одной
catalog revision.

Отдельное расширение core потребуется только если product scope потребует
multi-release/multi-configuration operation одного binary: release в Settings,
Evidence и database marker должен стать profile-driven. Это не является частью
resolver package контракта для target `11.5.27.56`.

## 10. Release gates

Production package активируется только после следующих проверок.

- Schema/Pydantic/semantic validation всех skills и package `1.1.0`.
- Отсутствие duplicate `skill_id`, orphan/ambiguous lock entries и dependency
  cycles; exact digest equality embedded documents и lock.
- Metadata/query proof каждого physical object/attribute и каждого role filter;
  live positive, zero, duplicate-key, wrong-role и wrong-type cases.
- 0/1/N tests для `select_one`, complete/incomplete `select_set` и display-only;
  same presentation/different UUID, renamed presentation/same UUID и duplicate
  row/same identity.
- Public-label tests: no `_objectRef`, UUID или physical type; label collision и
  truncated candidates дают только `narrow`.
- Composition tests для previous-step и session context, exact full-ref transport,
  role mismatch, parent mismatch, `result_constraint` и context replacement.
- Package round trip через web и CLI в чистый compatible data dir с byte-stable
  export и одинаковыми digests; следующий turn видит package без restart.
- Worst-case shortlist полного каталога включает нужный role/strategy resolver и
  consumer в лимит 16 только по declarative manifest/dependency signals.
- Source scan подтверждает отсутствие entity names, slot keys, physical type maps,
  semantic-prefix inference и Q-specific branches в application code.

## 11. Последствия и риски

Решение сохраняет core малым: добавление customer, supplier, нового reference или
нового physical binding является выпуском JSON, а не application feature. Цена -
большее число atomic skills и обязательное независимое доказательство каждого
role/predicate pair.

Основные риски:

- role proof может быть формально валиден, но бизнес-семантически неверен без
  metadata/live/oracle evidence;
- canonical availability dependency гарантирует наличие одного producer, но не
  выбирает правильную exact/contains strategy;
- большое число strategy variants повышает нагрузку на bounded shortlist;
- label facts могут не различить реальные дубликаты, поэтому collision нельзя
  разрешать порядком choices;
- ошибочный lock/version discipline может заменить не тот active skill или
  оставить consumer без совместимого canonical producer;
- upgrade существующего starter виден как две последовательные целые revisions;
  требование одной общей revision несовместимо с текущим `create|replace` API;
- package signing отсутствует: SHA-256 доказывает целостность, но не автора;
- builtin starter с фиксированным filename может быть ошибочно принят за
  production delivery mechanism;
- переносимость пока ограничена одним configuration profile/release, несмотря на
  наличие release range в skill contract.

## 12. Addendum: resolver -> RankOperator(1) -> selection

### 12.1. Решение

`RankOperator` с `limit=1` является generic deterministic selector над полным
набором кандидатов typed resolver-а. Он не является новым producer-ом сущности и
не меняет semantic role. Resolver доказывает identity, role и физический
`query_column_binding`; rank доказывает только детерминированный выбор одной
строки из exact входного universe.

Для rank-mediated selection source resolver в собственном `StepResult` остается
candidate producer и не применяет к себе прямую ветку `select_one` 0/1/N. Иначе
его `N>1` clarification остановит DAG до rank. Единственным selector-ом в этой
ветке является rank step; downstream `StepBinding` ссылается на rank step и тот
же identity `fact_id`.

Rank-input является внутренним свойством validated edge, а не новым public
resolver mode. Оно разрешает повысить initial no-probe candidate result до
`complete_set` по тем же resolver completeness rules, но не разрешает source
step создать selection, clarification или context export.

Менять public Evidence 1.1, skill schema 1.1 или `RankOperator` plan model не
нужно. Нужны generic executor path и минимальное расширение private proof trace.

### 12.2. Reference-preserving output и provenance

Rank является row-preserving projection:

1. Source resolver создает обычные `Fact` с исходными `fact_instance_id`,
   `step_id`, `source_locator=query_column_binding` и pinned skill provenance.
2. Rank `StepResult(skill=None)` содержит ссылки на facts выбранной source row,
   не копии с новыми IDs. В частности, новый operator-produced `entity_ref` не
   создается.
3. В public Evidence rank имеет `source_kind=deterministic_operator`,
   `operation_ref=operator:rank` и перечисляет выбранные исходные fact IDs в
   `produced_fact_instance_ids`. Общая таблица `facts` содержит каждый Fact один
   раз; producer step и rank step могут ссылаться на один ID.
4. Такое повторное reference разрешено только row-preserving operator-у и только
   на facts его declared direct `input_step_id`. Обычный step не может заявить
   чужой fact своим output.
5. Downstream binding получает facts из rank `StepResult`, но entity origin
   восстанавливает по `fact.step_id` source resolver-а. `EntityFactOrigin`
   сохраняет source `(skill_id, version, digest, column, accepted_mcp_types)`.
6. `context_exports` ссылается на исходный identity fact winner-а. Поэтому
   `ContextFact.origin` остается exact query-column provenance, а rank остается
   видимым отдельным deterministic step.

Для final coverage принадлежность fact к rank output определяется membership в
rank `StepResult`/`StepEvidence.produced_fact_instance_ids`, а не равенством
`Fact.step_id == rank.step_id`. `Fact.step_id` остается владельцем provenance.
Это generic правило для row-preserving operators; skill outputs продолжают
проверяться по обычному owner step.

Копирование identity в новый Fact с `source_locator=operator_result` отклонено:
public Evidence не смог бы сам доказать его physical binding, а восстановление
provenance по совпадению `_objectRef` было бы неоднозначным. Перенос `skill` в
rank `StepResult` также отклонен, поскольку ошибочно представил бы operator как
`mcp_data`.

### 12.3. Минимальный private SelectionProof

Текущий `SelectionProof` достаточен для прямого resolver selection, но не может
однозначно связать winner с rank step и full input universe. Для rank branch это
реальная, а не удобная причина изменить private trace. Добавляются только два
optional поля:

```text
selector_step_id: StepId | null
selector_digest: Sha256 | null
```

Оба поля отсутствуют для существующего direct selection и обязательны вместе
для rank-one. `resolver.step_id` продолжает указывать source resolver,
`resolver.mode=select_one`, а `fact_instance_ids` и `identities` содержат ровно
один исходный identity fact winner-а. `selector_step_id` указывает validated
`RankOperator`; использовать здесь skill ID или operator-produced identity
запрещено.

При `selector_step_id=null` validator сохраняет старое правило: proof обязан
покрыть exact identity set прямого resolver result. При rank branch ожидаемым
set является только независимо пересчитанный winner. Rank-oriented
`resolver.mode=select_one` описывает mediated use и не передается в source
`_apply_resolver_state`.

`selector_digest` считается по RFC 8785/SHA-256 от:

- canonical RankOperator из pinned plan, включая source step, sort fact,
  direction, literal limit и ties policy;
- exact source `(skill_id, version, digest)`, resolver identity/role proof
  contract и selected-only slot policy;
- canonical full candidate records: identity tuple, source identity fact ID,
  rank value и required role-proof fact IDs/values;
- proven source order для `stable_first`, input completeness flags, catalog
  snapshot и database state marker;
- exact winner identity/fact ID.

Candidate records сортируются по canonical identity для order-independent
digest. Только `stable_first` дополнительно включает proven ordered identity
sequence. `proof_digest` включает новые selector fields и прежний selected-one
payload. Валидатор получает pinned plan вместе с Evidence: одного public
`operation_ref=operator:rank` недостаточно для повторного вычисления результата.

### 12.4. Proof validation

Rank-one `SelectionProof` валиден только если core независимо подтверждает все
условия:

1. `selector_step_id` называет required rank step, его direct input является
   ровно одним skill step с `typed_entity_resolver_v1`; chains через filter/join
   в этой минимальной версии запрещены.
2. `limit` является literal integer `1` (`bool`, decimal, string и dynamic
   binding не coerced). Другой limit может быть display computation, но не exact
   context selection.
3. Source skill имеет exact resolution identity, role proofs и matching
   `selected_only` policy; rank `sort_fact_id` является required non-null scalar
   fact каждой candidate row.
4. Все rank values имеют один declared comparable `value_type`. Сравнение
   выполняется generic type comparator-ом без object-specific conversion,
   presentation parsing или semantic map.
   `stable_first` при tie дополнительно требует declared source order: первый
   key совпадает с `sort_fact_id` и direction, а unique suffix заканчивается
   resolver identity. Наблюдаемый transport order доказательством не является.
5. Source universe имеет `collection_scope=complete_set`, `has_more=false`,
   `truncated=false`, не является continuation page и имеет подтвержденный
   initial completeness proof.
6. Rows реконструируются по `row_id`; identity и role proof проверяются до rank.
   Missing/null/non-comparable rank fact, неполный role proof и одна identity с
   конфликтующими rank values дают `contract_error`, а не silent row drop.
7. Executor повторно вычисляет winner из всего canonical universe. Rank
   `StepEvidence.produced_fact_instance_ids`, proof winner и единственный
   `context_export` должны ссылаться на тот же source identity; non-winners не
   входят в proof или context.
8. Downstream exact-ref parameter принимает source semantic type и physical type
   по обычной provenance validation. Rank не может retag identity или выбрать
   slot по имени semantic type.
9. Context mutation commit-ится только после sufficient terminal closure по
   общим правилам. Failed/partial/tied rank блокирует descendants и экспорт.

Exact duplicate rows одной identity с одинаковым rank value могут быть
дедуплицированы по существующей resolver identity. Та же identity с разными rank
values является противоречием producer evidence и всегда отклоняется.

### 12.5. Empty, ties и incomplete input

| Состояние | Rank result | Selection/context |
| --- | --- | --- |
| Complete input, 0 candidates | `success_empty/not_found`, 0 output refs | no proof, no export, descendants stop by declared empty policy |
| Complete input, unique top value | one reference-preserved row | `selected_one`, exact source identity export |
| Complete input, top tie, `stable_first` with proven total source order ending in identity tie-break | first row in that declared order | `selected_one` |
| Top tie, `stable_first` without proven total order | plan/contract error | no proof/export; transport row order is forbidden |
| Top tie, `include_all` | all top-tie rows, `clarification_required` | no proof/export; existing safe resolver labels drive choose/narrow |
| Any `visible_page`, continuation page, `has_more`, truncation or partial input | rank blocked/partial with 0 output refs | no proof/export/downstream |
| Invalid/mixed/null rank values | `contract_error` | no proof/export/downstream |

`include_all` never becomes `selected_set` when consumer requires one entity.
For `2..5` distinguishable top ties core may offer typed choices; more members,
label collision or an incomplete tie universe permits only `narrow` against the
pinned source resolver.

Global rank запрещен даже над последней page с `has_more=false`: она остается
частью paged set, а не всем universe. Rank не делает hidden drain и не склеивает
continuation pages. Допустим только непагинируемый complete producer либо
initial resolver request, где declared probe contract доказал отсутствие
следующей строки. Даже совпадение query order с requested rank не превращает
неполную first page в global top-one proof. Будущая оптимизация pushdown требует
отдельного декларативного `top_k_complete` proof; выводить его из `ORDER BY` или
первой строки нельзя.

### 12.6. Upgrade path и рекомендация реализации

1. До реализации executor-а сохранить fail-closed поведение
   `OPERATOR_NOT_IMPLEMENTED`; fallback к first/last row запрещен.
2. Добавить RankOperator в generic runtime dependency graph и реализовать один
   comparator/executor path с reference-preserving `StepResult`, strict
   completeness gate и explicit tie outcomes.
3. Научить Evidence builder дедуплицировать shared Fact IDs и публиковать rank
   как `deterministic_operator`; semantic validator разрешает shared references
   только для direct row-preserving operator lineage.
4. Добавить optional selector fields в private `SelectionProof`, rank-aware
   builder/validator и source-origin lookup для downstream/context. Старый direct
   payload и digest остаются читаемыми без миграции; rank payload использует
   selector fields как version discriminator.
5. Включать planner generation rank-one только после blocking tests для complete
   asc/desc, both tie policies, empty, invalid values, pagination/continuation,
   proof tampering, downstream binding, restart и двух разных semantic types.

Рекомендация для реализации: выбрать reference-preserving rank projection плюс
два optional private selector fields. Это минимальная модель, которая не меняет
Evidence 1.1, не ослабляет query-column provenance и не требует ни одного
object-specific branch. Модель с новым operator entity fact или выбором первого
transport row не должна реализовываться.
