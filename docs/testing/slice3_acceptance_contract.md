# Приемочный контракт slice 3

## 1. Назначение и граница

Документ является исполняемой архитектурной спецификацией entity/context slice
3 на baseline `2d40bd5` (`v0.1.0-alpha.3`). Он дополняет, но не ослабляет
`slice1_acceptance_contract.md`, `slice2_acceptance_contract.md`,
`skill_contract.md`, `request_lifecycle.md`, `integration_contracts.md`,
`persistence_and_observability.md` и продуктовые требования
`docs/requirements/slice3_entity_context_requirements.md`.

Slice 3 не реализует production code этим документом. Реализация считается
принятой только по black-box HTTP/SSE, наблюдаемому DeepSeek/MCP transport,
экспортированным skill/package bytes, Evidence 1.1, trace artifacts и restart
того же `APP_DATA_DIR`. Импорт production-модулей сам по себе не является
приемочным доказательством.

Нормативные ограничения предыдущих slices сохраняются:

- каждый новый EvidenceBundle записывается как `1.1.0` с явными
  `steps[*].collection_scope` и `coverage.requirements[*].required`;
- Evidence 1.0 остается только legacy read path, без rewrite;
- unbounded producers используют доказанный keyset; prefix допускается только
  с invariant proof и точной границей `M-1/M/M+1`;
- context selection не выполняет скрытый drain страниц и не превращает
  `visible_page` в `complete_set`;
- raw `EntityRef` из LLM, user slot или application dictionary запрещен.

## 2. Baseline audit и обязательные regression gates

На baseline подтверждены четыре production gap, которые являются обязательными
red tests до реализации slice 3:

| Gap | Наблюдаемое baseline behavior | Требуемое slice-3 behavior |
| --- | --- | --- |
| `S3-G01` | `application.execution._context_exports` экспортирует все entity facts всех rows | Экспортируется только entity selection, доказанный `SelectionProof`; candidates и обычные list/detail rows не экспортируются |
| `S3-G02` | `clarify` завершается без persisted pending state; следующий текст планируется по recent messages | Сохраняется typed one-use pending record, связанный с исходным turn/plan/requirements/candidates |
| `S3-G03` | `context_facts` читается append-only и completion только вставляет новые rows | Активный context задается versioned semantic slots с replace/expire/invalidate lifecycle |
| `S3-G04` | `ContextFact.exact_origin` принимает только `entity_ref`; retained moment/period/filter не имеет contract path | Generic typed scalar/filter slots хранят exact confirmed value с `value_type`, provenance и отдельным safe policy mode |

`PlannerRequest` внутри core и защищенный diagnostic trace вправе содержать
полный `ContextFact` для воспроизводимости. Это не baseline defect.
`DeepSeekPlanner._messages` уже строит внешний context из `handle`,
`semantic_type`, `presentation`, `origin_turn_id`. Slice 3 закрепляет это
black-box gate: фактический outbound HTTP body не содержит `value`, `_objectRef`,
`ТипОбъекта`, `УникальныйИдентификатор` или server-side member UUID.

## 3. Термины и неизменяемые инварианты

`ResolverSpec` есть декларативная часть переносимого skill contract. Core знает
только protocol version и общие поля spec; имен сущностей и физических типов в
application branches нет.

`ResolverUseProof` есть core-derived proof одного вызова resolver-а:
`select_one`, `select_set` или `display_only`. Planner не задает и не может
понизить этот mode.

`SelectionProof` доказывает, какие exact entity fact instances пользователь или
валидированный plan действительно выбрал. Наличие `role=entity`, попадание в row
или близость presentation само по себе selection не создает.

`FilterRetentionProof` доказывает, какой non-entity fact является подтвержденным
условием или координатой результата и может быть сохранен без повторного
вычисления. Он не является и не может подменять `SelectionProof`.

`ContextOriginProof` связывает два разных закрытых контракта: plan parameter
source (`user_slot|system|previous_step|session_context`) и Evidence fact locator
(`query_column_binding|operator_result|system_value`). Оба должны совпасть с
`allowed_sources` producer/consumer и restorable execution graph.

`Context slot` есть versioned server-side binding с декларативным `slot_key`,
semantic type, `value_type`, policy mode, cardinality и одним или несколькими
origin facts. `slot_key` приходит из portable contract, а не из core map.

`Context handle` адресует ровно одну slot generation. Handle opaque: в нем нет
UUID объекта, physical type, slot key, cursor или подписи клиентского payload.

Business identity одного объекта равна точной тройке:

```text
(semantic_type, EntityRef.ТипОбъекта, EntityRef.УникальныйИдентификатор)
```

Для set selection identity равна множеству уникальных троек. Membership digest
считается по RFC 8785 canonical JSON списка, отсортированного по
`semantic_type`, `ТипОбъекта`, canonical UUID. Порядок rows и presentation в set
identity не входят.

Identity scalar/filter generation равна точной тройке
`(semantic_type, value_type, SHA-256(canonical value bytes))`. Canonical value
фиксируется один раз при production fact; retention и follow-up не вызывают
`now()`, parser, normalization или повторный MCP query для получения значения.

## 4. Универсальный typed resolver protocol

### 4.1. Portable declaration

Skill schema следующей реализации добавляет к `output_contract` закрытые
`resolution` и `context_export_policy`. Нормативная форма:

```json
{
  "cardinality": "many",
  "resolution": {
    "protocol": "typed_entity_resolver_v1",
    "identity_fact_id": "item.ref",
    "candidate_label_fact_ids": ["item.name", "item.code"],
    "role_proof_fact_ids": [],
    "default_slot_key": "selection.item"
  },
  "context_export_policy": [
    {
      "fact_id": "item.ref",
      "slot_key": "selection.item",
      "mode": "selected_only",
      "lifetime": {"mode": "session"},
      "max_members": 100
    }
  ]
}
```

`parameters[*]`, если принимает context, объявляет закрытый
`context_slot_keys`. Это semantic roles, не physical 1C names. Новый field и
resolver fields участвуют в canonical skill digest и package lock.

Scalar/filter fact использует отдельный, явно безопасный policy mode:

```json
{
  "context_export_policy": [
    {
      "fact_id": "balance.moment",
      "slot_key": "filter.balance_moment",
      "mode": "confirmed_filter",
      "semantic_type": "time.moment",
      "value_type": "datetime",
      "lifetime": {"mode": "session"}
    }
  ]
}
```

`selected_only` допустим только для `entity_ref|entity_ref_list` и требует
`SelectionProof`. `confirmed_filter` допустим только для non-entity fact и
требует `FilterRetentionProof`; объединять режимы или выводить один из другого
запрещено. В slice 3 обязательны закрытые portable instances:

| Semantic type | Exact `value_type` | Required canonical value |
| --- | --- | --- |
| `time.moment` | `datetime` | timezone-aware RFC 3339 instant, serialized once |
| `time.period` | `period` | closed object with exact start/end/boundary/timezone fields |
| declared business option | `enum` | one exact member of fact/parameter `allowed_values` |
| `presentation.detail` | `enum` | one declared detail/grouping preference value |

Свободная строка, LLM literal, user text, nullable/defaulted value и произвольный
JSON не являются retainable scalar fact. Другой scalar type добавляется только
portable schema declaration с теми же generic checks, без core branch.

Resolver query возвращает candidate rows и поэтому имеет producer cardinality
`many`, даже если exact key обычно дает одну строку. Logical cardinality после
resolver state machine выводится отдельно. Это исключает contract error на
законном duplicate key и не позволяет назвать первый row выбранным объектом.

`identity_fact_id` обязан ссылаться на required `entity_ref` fact с
`role=entity`, exact `query_column_binding`, `converter=object_ref` и непустым
`accepted_mcp_types`. `row_identity_fact_ids` содержит этот fact. Все label facts
только отображаются и не участвуют в identity.

### 4.2. Обязательные protocol instances

Точные physical 1C types не фиксируются этой таблицей. Они принадлежат exact
producer column bindings и проверяются по metadata/query/live proof.

| Logical resolver | Canonical semantic type | Default slot key | Дополнительный hard proof |
| --- | --- | --- | --- |
| item | `catalog.item` | `selection.item` | object is not an item group |
| item-group | `catalog.item.group` | `selection.item_group` | exact group flag/relation |
| partner | `party.partner` | `selection.partner` | none beyond producer contract |
| customer | `party.customer` | `selection.customer` | explicit customer role fact |
| supplier | `party.supplier` | `selection.supplier` | explicit supplier role fact |
| warehouse | `catalog.warehouse` | `selection.warehouse` | requested warehouse type/department facts when used |
| organization | `party.organization` | `selection.organization` | own-organization producer proof |
| enterprise cash-desk | `finance.cash_desk.enterprise` | `selection.cash_desk.enterprise` | exact cash-desk kind fact |
| POS cash-desk | `finance.cash_desk.pos` | `selection.cash_desk.pos` | exact cash-desk kind fact |
| price-type | `catalog.price_type` | `selection.price_type` | currency/VAT/purpose facts declared as applicable |
| order | `document.sales_order` | `selection.sales_order` | exact document kind |
| shipment | `document.sales_shipment` | `selection.sales_shipment` | exact document kind |
| receipt | `document.purchase_receipt` | `selection.purchase_receipt` | exact document kind |
| purchase-order | `document.purchase_order` | `selection.purchase_order` | exact document kind |
| transfer | `document.stock_transfer` | `selection.stock_transfer` | exact document kind |
| characteristic | `catalog.item.characteristic` | `selection.item_characteristic` | selected item binding when contract requires it |
| series | `catalog.item.series` | `selection.item_series` | selected item/characteristic binding when required |
| purpose | `inventory.purpose` | `selection.inventory_purpose` | none beyond producer contract |

Partner is the base business object. Customer and supplier bindings may point to
the same physical ref/UUID, but are distinct role-qualified semantic slots and
require hard role facts. Slice 3 uses separate exact resolver contract instances
or a schema-proven variant that emits the exact role-qualified semantic type.
Core-side retagging from a requested role, name or presentation is forbidden.

### 4.3. Import-time validation

Import rejects the whole atomic package before catalog revision changes when:

| Condition | Stable code |
| --- | --- |
| Unknown protocol/extra field/missing declaration | `RESOLVER_CONTRACT_INVALID` |
| Identity fact is absent, nullable, non-entity or not `entity_ref` | `RESOLVER_IDENTITY_FACT_INVALID` |
| Exact `object_ref` column binding/physical allowlist is absent | `RESOLVER_PHYSICAL_PROOF_MISSING` |
| Identity is absent from row identity | `RESOLVER_ROW_IDENTITY_INVALID` |
| Label or role proof fact is unknown/nullable where proof is required | `RESOLVER_PROOF_FACT_INVALID` |
| Context policy names another fact/semantic slot or allows arbitrary export | `CONTEXT_EXPORT_POLICY_INVALID` |
| `selected_only` targets non-entity or `confirmed_filter` targets entity/list | `CONTEXT_EXPORT_MODE_INVALID` |
| Retained scalar lacks exact supported type, canonical form, source provenance or allowed source | `CONTEXT_FILTER_CONTRACT_INVALID` |
| Unbounded resolver lacks the complete slice-2 keyset proof | existing keyset import code |

`role_proof_fact_ids` cannot be inferred from name text, query alias, physical
type prefix or requested role literal alone. A customer/supplier result without
hard role evidence is a contract error, not a base partner with a new label.

### 4.4. Core-derived use mode

PlanValidator derives one mode before MCP:

1. `select_one`: identity feeds a required downstream `entity_ref` parameter or
   is a required final entity requirement with `one|zero_or_one` cardinality.
2. `select_set`: identity feeds a required downstream `entity_ref_list`
   parameter.
3. `display_only`: identity is only a final `many` list and is not consumed as a
   selected filter.

Mixed incompatible uses of one resolver step are rejected as
`PLAN_RESOLVER_MODE_AMBIGUOUS`. Planner cannot set a `selection=true` flag and
cannot turn a display list into context.

### 4.5. Exact 0/1/N state machine

Structural schema/type/identity validation precedes this table. Invalid rows are
`contract_error`, never candidates.

| Valid distinct candidates | Mode | Resolver outcome | Context/pending action |
| --- | --- | --- | --- |
| `0` | any | `success_empty`, reason `not_found` | no export, no pending |
| `1` | `select_one` | `selected_one` | stage one selection |
| `1` | `select_set` | `selected_set` of one | stage one-member set |
| `1` | `display_only` | `success_with_rows` | display only, no export |
| `N>1` | `select_one` | `clarification_required` | persist candidates, export none, block descendants |
| `N>1` | `select_set`, complete set, `N<=max_members` | `selected_set` | stage one slot with N members |
| `N>1` | `select_set`, incomplete/too large | `partial` or continuation according to existing pagination policy | no export and no hidden drain |
| `N>1` | `display_only` | normal list outcome | display only, no export |

For `select_one`, a valid first page containing at least two candidates proves
ambiguity. Exactly `2..5` complete candidates may be shown as choices. If there
are at least six candidates or `has_more=true`, no candidate from the truncated
page is selectable: pending permits only a typed `narrow` action that adds one
declared search criterion and reruns the same pinned resolver. Core never treats
the first five as a complete candidate universe.

## 5. SelectionProof, FilterRetentionProof и context export eligibility

Core builds `SelectionProof` from the validated plan, CoverageProof and runtime
facts. An entity fact is eligible only if all conditions hold:

1. It is the identity fact of `selected_one`/`selected_set`, or the exact entity
   winner of a validated deterministic selection such as rank-one.
2. It has explicit `context_export_policy` and an exact slot key accepted by the
   relevant consumer/final requirement.
3. It belongs to required closure and contributes to a sufficient terminal
   outcome. An already selected resolver entity remains eligible when its
   downstream detail/price/line/balance query validly returns empty.
4. A selected set is `complete_set`, has no duplicate identities and is within
   `max_members<=100`.
5. Every fact has restorable origin evidence, pinned skill digest and exact
   column binding physical proof.

The following never creates entity `context_exports`:

- resolver candidates before user choice;
- every row of `display_only` item/partner/warehouse/document list;
- line item refs from order/receipt/transfer lines;
- entity dimensions merely present in header/balance/debt rows;
- an empty resolver, `partial`, `clarification_required`, dependency failure or
  `contract_error` before a valid selection is proved;
- optional evidence outside required sufficient closure;
- any visible keyset page that was not proved as the complete selected set.

For a selected set Evidence 1.1 contains one `context_exports` row per selected
fact, all with the same random `context_handle`. Grouping by handle therefore
proves one slot generation with N members without changing Evidence schema.
Every member fact remains independently traceable.

A scalar/filter fact is eligible only through `FilterRetentionProof`, which
proves all of the following:

1. Pinned skill policy names the exact fact, slot, semantic type, `value_type`
   and separate `confirmed_filter` mode.
2. The non-null fact has a `ContextOriginProof` and was produced by an exact
   declared `query_column_binding`, deterministic `operator_result` or trusted
   `system_value`. An operator result additionally traces every input binding and
   checks its producer parameter `allowed_sources`; a `user_slot` input must pass
   exact typed normalization/closed enum validation. Raw user text, LLM output
   and untyped literal are never direct retained origins.
3. The fact is in required sufficient closure and was actually used as the
   request filter, result coordinate or confirmed interpretation. Merely being
   present in a row or default definition is insufficient.
4. Canonical value validates against the exact closed type. Enum membership,
   period boundaries/timezone and datetime offset are checked without coercion.
5. Every future consumer explicitly allows source `session_context`, the exact slot,
   semantic type and `value_type`; enum domains and structured shapes are exactly
   compatible, not merely convertible.

Scalar policy never grants entity export and never bypasses entity producer
physical proof. Each scalar generation has one origin fact and contributes one
unchanged Evidence 1.1 `context_exports` row.

## 6. Opaque handles и exact binding

Context and clarification handles are generated from 24 CSPRNG bytes and
base64url without padding:

```text
ctx_<base64url>
clar_<base64url>
```

Handles are server-side records, not signed/encoded client state. They are not
derived from `fact_instance_id` and cannot be reused across sessions.

Before an entity value reaches MCP, core restores the original confirmed fact,
producer step, exact `(skill_id, version, digest)`, fact definition and
`query_column_binding`. It then checks:

- active handle belongs to the same session and expected slot generation;
- semantic type equals the consumer parameter exactly, without prefix/subtype
  inference;
- every actual `ТипОбъекта` is a case-sensitive member of producer
  `accepted_mcp_types`;
- cardinality and `context_slot_keys` match the consumer parameter;
- stored value digest equals the origin fact value digest;
- optional result constraint such as `fact_equals_parameter` is present when
  the consumer claims same-object output.

The full stored `_objectRef` is passed to MCP without reconstruction. Acceptance
compares RFC 8785 canonical JSON of stored and outbound values; property order is
irrelevant, while UUID, physical type and all value fields are unchanged.
Returned identity comparison uses the exact triple. A renamed presentation with
the same triple is accepted and may refresh display text; same presentation with
another UUID/type is another entity.

Before a scalar/filter value reaches MCP, the same generic path restores its
origin fact and pinned policy, verifies `FilterRetentionProof`, exact semantic
type, `value_type`, `context_slot_keys`, consumer `allowed_sources` and canonical
value digest. No entity physical check is invented for scalar facts, but scalar
mode can never carry `_objectRef`. Consumer binding receives the stored canonical
value, not a parser result or current system value.

For `time.moment`, storage records both canonical JSON bytes and digest. Q091,
Q092 and Q093 fixture transports must observe byte-for-byte identical parameter
bytes. In particular Q092/Q093 cannot refresh from turn time, `now()`, a new MCP
response or LLM output. Planner sees only handle, semantic type, `value_type`,
safe presentation and origin turn; raw scalar value remains server-side exactly
like an entity ref.

## 7. Context slot ledger

### 7.1. Logical records

Slice 3 separates immutable origin facts from active bindings:

| Record | Required state |
| --- | --- |
| `context_facts` | immutable origin turn/fact pointers, semantic type, value type, canonical full server-side value bytes and digest |
| `context_slots` | session, slot key, generation, random handle, semantic/value type, policy mode, cardinality, membership/value digest, lifetime, status and reason |
| `context_slot_members` | slot generation to ordered origin facts; unique exact entity identities or one scalar origin |
| `pending_clarifications` | one-use typed state defined in section 8 |

At most one generation is `active` for `(session_id, slot_key)`. Historical
generations remain for traceability with status `replaced`, `expired` or
`invalidated`; they are not returned to planner.

### 7.2. Atomic mutation rules

`ContextMutationSet` is staged after Evidence plus `SelectionProof` or
`FilterRetentionProof` validation and
committed in the same transaction as terminal answer/evidence:

1. New selected identity/set with no active slot inserts generation 1.
2. New selected identity/set for the same slot marks old generation `replaced`,
   links `replaced_by`, and inserts the next generation.
3. Re-selection or re-confirmation creates a new `refreshed` generation so origin
   provenance/presentation is current; the previous handle becomes `replaced`.
   Equality never depends on presentation and never suppresses provenance update.
4. Slots not named by the mutation set remain byte-for-byte active. Changing
   item cannot replace warehouse/moment/detail slots.
5. A renderer/detail-only follow-up may create or replace only its declared
   `presentation.detail` preference slot; it never mutates entity, period or
   moment slots.
6. If final outcome is not eligible for context commit, the entire mutation set
   is discarded and prior active slots remain unchanged.

No last-write scan by semantic type and no object-specific replacement branch is
allowed. Replacement key is only the portable exact `slot_key`.

### 7.3. Expiry and invalidation

Lifetime is declared by portable context policy:

| Lifetime | Deterministic rule |
| --- | --- |
| `session` | `expires_at=null`; active until replace/invalidate/session clear |
| `until` | exact UTC `expires_at` is obtained from a validated fact named by policy |
| `turn` | expires when the creating turn transaction completes; never sent to a later planner call |

All listed entity resolver slots use `session` in slice 3. Synthetic portability
tests exercise `until` and `turn`; core contains no semantic-type exception.
At load, `now >= expires_at` atomically changes active `until` slot to `expired`.

Invalidation is fail closed and uses closed reasons:

| Trigger | Status/reason |
| --- | --- |
| Origin turn/evidence/fact is missing or corrupt | `invalidated/provenance_missing` |
| Historical skill digest or exact binding cannot be restored | `invalidated/producer_contract_missing` |
| Explicit session/context clear | row removed by existing clear closure |
| Explicit generic remove-filter action | `invalidated/user_removed` for the named handle only |
| Successful replacement | `replaced/new_selection` or `replaced/refreshed` |
| Declared time reached | `expired/policy_time_reached` |

Catalog revision or database marker change alone does not retag or silently
re-resolve a durable entity. The next exact consumer call validates its active
contract and result. Forged, cross-session or semantically incompatible use is
rejected but does not invalidate an otherwise valid slot.

## 8. Persisted one-use pending clarification

### 8.1. Two typed origins

`resolver_choice` is created after valid `N>1` in `select_one`. It stores the
validated plan/coverage proof checkpoint, resolver step, candidate origin facts,
choice labels, blocked descendants and original turn IDs.

`interpretation_choice` is created from a valid planner `clarify` result for a
typed enum/metric/period/other material choice. It stores original question,
interpretation, exact requirement IDs/signatures, typed choice bindings and
resume constraints.

Both store session/context version, exact catalog snapshot, database marker,
`issued_at`, `expires_at=issued_at+30 minutes`, `consumed_at`,
`superseded_at` and optional claim turn. Full refs and typed literal values remain
server-side; public choices contain only `choice_id` and `label_ru`.

Only one pending record may be active per session. Creating another or accepting
an ordinary message without a clarification response atomically marks the old
record `superseded` before the new turn is planned.

### 8.2. Public response and claim

Terminal clarification adds this closed DTO to `GET /turns/{id}`:

```json
{
  "outcome": "clarification_required",
  "clarification": {
    "handle": "clar_<base64url>",
    "question_ru": "Какой склад выбрать?",
    "choices": [
      {"choice_id": "c1", "label_ru": "Розничный склад 1"},
      {"choice_id": "c2", "label_ru": "Розничный склад 2"}
    ],
    "has_more_candidates": false,
    "expires_at": "2026-07-21T12:30:00Z"
  }
}
```

The existing message endpoint accepts one optional closed member. A candidate
choice is:

```json
{
  "text": "Второй",
  "client_message_id": "018f...",
  "expected_context_version": 8,
  "clarification_response": {
    "handle": "clar_<base64url>",
    "action": "choose",
    "choice_id": "c2"
  }
}
```

The two other closed forms are
`{"handle":"clar_<base64url>","action":"narrow"}` with `text` as the new
declared search criterion, and
`{"handle":"clar_<base64url>","action":"cancel"}`. `narrow` is accepted only
for truncated `N>5|has_more` resolver pending, reruns the same pinned resolver
with all frozen bindings plus that criterion, and creates a replacement pending
when ambiguity remains. `cancel` consumes pending with no planner/MCP call and no
context mutation.

UI choice buttons always send `handle+choice_id`. Text-only deterministic match
is allowed only for exactly one normalized `choice_id`, ordinal or full label;
zero/multiple matches repeat the same question and do not consume pending.
Model similarity/confidence never selects a candidate.

Claim checks syntax, existence, session, active state, exact context version,
`now < expires_at`, catalog snapshot and database marker, then sets
`consumed_at/claim_turn_id` and creates the response turn in one transaction.
At `now >= expires_at` the record is expired.

| Reject | HTTP | Stable code | External calls |
| --- | --- | --- | --- |
| Invalid syntax/DTO | 422 | `CLARIFICATION_HANDLE_INVALID` | none |
| Unknown/forged | 404 | `CLARIFICATION_NOT_FOUND` | none |
| Other session | 409 | `CLARIFICATION_SESSION_MISMATCH` | none |
| Consumed/superseded | 409 | `CLARIFICATION_CONSUMED` / `CLARIFICATION_SUPERSEDED` | none |
| Expired | 410 | `CLARIFICATION_EXPIRED` | none |
| Context version differs | 409 | `CONTEXT_VERSION_CONFLICT` | none |
| Catalog/marker differs | 409 | `CLARIFICATION_CATALOG_CHANGED` / `CLARIFICATION_MARKER_CHANGED` | none |
| Action not allowed by pending kind/state | 422 | `CLARIFICATION_ACTION_INVALID` | none |
| Choice absent from record | 422 | `CLARIFICATION_CHOICE_INVALID` | none |

### 8.3. Resume without guessing

For `resolver_choice`, core adds a generic deterministic
`resolver://select/v1` proof, binds the exact chosen origin fact and resumes only
the stored blocked descendants of the same validated plan. No DeepSeek call and
no second resolver search is allowed.

For `interpretation_choice`, one planner composition call is allowed because the
initial `clarify` result has no DAG. Its input is the original question plus the
server-resolved typed choice and frozen interpretation, not recent-message
replanning. PlanValidator requires exact requirement IDs/signatures, use of the
selected binding in required closure and no replacement by another literal or
handle. The LLM may compose skills but cannot choose or reinterpret the answer.

The clarification response turn emits Evidence 1.1. A selected entity passes the
original candidate fact through the generic selection proof with unchanged
identity/provenance and may enter `context_exports` only if the resumed final
outcome passes section 5. A failed resume consumes pending but commits no staged
slot replacement.

## 9. Deterministic follow-up rules

1. Planner sees only active, non-expired slot handles, exact semantic type,
   cardinality and presentation. It never sees server-side ref values.
2. A `ContextBinding` is admissible only for a consumer-declared slot key and
   exact semantic/cardinality contract.
3. If zero active compatible slots exist, core returns one clarification before
   MCP. If more than one compatible slot role can satisfy the same binding,
   "latest" and message order are not tie-breaks; clarification is required.
4. An explicit new entity mention remains a text/enum input and must run a
   compatible resolver. It shadows the old slot for that turn: `0`, ambiguity or
   error preserves old stored context but must not silently use it for the
   current request. The model cannot convert the mention into `EntityRef`.
5. A valid retained handle is resolved server-side and passed unchanged. Number,
   name or presentation lookup is forbidden unless the user explicitly requested
   a new object.
6. Unmentioned active slots remain byte-for-byte. Only successful selected entity
   or confirmed-filter facts with the same declarative slot key replace them.
7. An explicit remove action invalidates exactly its named active handle. Natural
   language never authorizes core to guess which unrelated filter to drop.

## 10. Deterministic pre-MCP validation

All checks below finish before the first MCP call of the affected turn:

- planner echo, pinned catalog and shortlist checks from previous slices;
- resolver declaration/import proof and core-derived use mode;
- pending claim/resume constraints;
- handle syntax, session ownership, active generation, expiry and membership;
- exact semantic type, accepted slot key, policy mode, value type and cardinality;
- origin turn/fact/evidence, skill digest, fact definition, source locator and
  `allowed_sources`; entity bindings additionally require exact physical column
  binding, while scalar bindings require exact canonical type/domain proof;
- no raw/ref literal and no semantic/physical prefix inference;
- complete-set obligation for selected entity lists;
- required result equality constraint for exact-ref consumers.

Stable runtime failures reuse existing codes where defined:

| Condition | Outcome/code |
| --- | --- |
| Missing origin chain | `contract_error / CONTEXT_PROVENANCE_MISSING` |
| Semantic/physical/value/cardinality mismatch | `contract_error / ENTITY_REF_CONTRACT_MISMATCH` |
| Entity parameter source is user/literal/model | `contract_error / ENTITY_REF_SOURCE_UNPROVEN` |
| Scalar source/value/domain/consumer is unproved or incompatible | `contract_error / CONTEXT_FILTER_CONTRACT_MISMATCH` |
| Replaced handle selected by plan | `contract_error / CONTEXT_HANDLE_REPLACED` |
| Expired/invalidated handle selected by plan | `contract_error / CONTEXT_HANDLE_EXPIRED` / `CONTEXT_HANDLE_INVALIDATED` |
| Candidate/list row requested for export without SelectionProof | `contract_error / CONTEXT_EXPORT_NOT_SELECTED` |
| Incomplete page requested as selected set | `contract_error / CONTEXT_SELECTION_INCOMPLETE` |

Every row records `mcp_call_count=0`. No fallback may search by presentation.

## 11. API, Evidence 1.1 and trace contracts

### 11.1. Session/details API

`GET /sessions/{id}` adds an opaque context summary:

```json
{
  "context_version": 9,
  "context": {
    "slots": [
      {
        "slot_key": "selection.item",
        "handle": "ctx_<base64url>",
        "semantic_type": "catalog.item",
        "value_type": "entity_ref_list",
        "policy_mode": "selected_only",
        "cardinality": "many",
        "member_count": 2,
        "presentation": "2 выбранных товара",
        "expires_at": null
      }
    ],
    "pending_clarification": null
  }
}
```

Public session/turn DTO never contains member UUID, physical type or full ref.
For scalar/filter slots it also never contains the raw retained value; only safe
presentation, semantic/value type and opaque handle are exposed.
`GET /turns/{id}/details` exposes `resolver_proofs` and `context_mutations` with
step/fact IDs, counts, modes, slot keys, handles and reason codes, but no raw ref.

`POST /sessions/{id}/messages` additionally accepts one closed generic action:

```json
{"context_action":{"kind":"remove","handle":"ctx_<base64url>"}}
```

It atomically invalidates only that active session-owned handle, increments
context version and writes a system/audit turn. It cannot be combined with text
or clarification response and makes no DeepSeek/MCP call.

### 11.2. Evidence 1.1

`schemas/evidence.schema.json` remains `1.1.0`; no Evidence 1.2 or permissive
default is introduced by slice 3. Existing context export object remains exactly:

```json
{
  "context_handle": "ctx_<base64url>",
  "fact_instance_id": "<uuid>",
  "semantic_type": "catalog.item"
}
```

Cross-artifact validation adds these requirements without changing wire schema:

- group exports by handle and compare exact entity member set with
  `SelectionProof`, or the single scalar fact with `FilterRetentionProof`;
- every exported fact is present in Evidence, covered by restorable provenance
  and allowed by the pinned skill context policy;
- grouped exports have one semantic/value type, policy mode, slot/cardinality and
  unique entity identities or exactly one scalar origin;
- `many` group comes only from `complete_set`; its producer/ancestor scopes keep
  exact Evidence 1.1 values;
- `coverage.sufficient`, requirement `required` and collection obligations are
  recomputed exactly as in slice 2;
- `context_exports=[]` for ambiguity, display-only list, partial, empty resolver
  and contract/dependency failure. A selected entity may still export when a
  valid downstream query returns empty.

### 11.3. Trace

Mandatory events, in addition to the current trace model:

```text
resolver.validated
resolver.completed
context.retention_validated
clarification.persisted
clarification.claimed / clarification.rejected / clarification.consumed
context.mutation_staged
context.slot_replaced / context.slot_expired / context.slot_invalidated
context.committed
```

Normal event payload has protocol/mode, step/slot/handle IDs, candidate/member
counts, status/reason and artifact hashes. It has no full ref.

Diagnostic ZIP adds:

```text
planner/http-request.json
resolver-proofs.json
filter-retention-proofs.json
pending-clarification.json
context-mutations.json
```

`context.json` and internal `planner/request.json` may retain protected full
ContextFact for replay. `planner/http-request.json` must equal the redacted body
captured by fixture transport and contain only opaque context fields. Secret and
absolute-path redaction from previous slices remains mandatory.

## 12. Portability and no object branches

Acceptance imports a synthetic resolver/consumer pair with previously unseen:

```text
semantic_type = synthetic.asset
physical type = СправочникСсылка.СинтетическийАктив
slot_key = selection.synthetic_asset
```

The pair must pass 0/1/N, pending choice, restart, exact follow-up and wrong-type
tests in a clean second `APP_DATA_DIR` without changing application source.
The same package also declares `synthetic.snapshot` as a supported
`confirmed_filter` scalar and proves retain/replace/consume without source change.

Core source must not contain:

- `if/match` by item/order/warehouse/customer/etc.;
- map `semantic_type -> ТипОбъекта` or `slot_key -> class`;
- prefix logic for `catalog.*`, `document.*`, `СправочникСсылка.*` or
  `ДокументСсылка.*` as identity proof;
- Q IDs or corpus values in selection/replacement logic;
- hardcoded physical types outside portable skill/profile test data.

Hardcoded application-level lexical helpers such as baseline `_DOC_SIGNALS`,
`_DATA_SIGNALS` and `_intent` are not permitted. Shortlist recall must be derived
from portable catalog declarations, skill descriptions/contracts, typed planner
requirements and active context. A synthetic entity with unseen vocabulary must
execute after package import without adding words, prefixes or branches to
application source; otherwise `S3-INV-008` fails.

Different fixed query skills for exact article/code/barcode/name or different
business-role proofs are allowed. They are portable producer contracts, not
core branches.

Keyset contract is unchanged. Resolver `display_only` uses normal page/continue
behavior. `select_one` may stop after proving `N>1`; it does not claim a complete
set. `select_set` may commit only materialized `complete_set<=100`; core never
silently drains keyset pages or reports page count as total.

## 13. Migration impacts

### 13.1. Contract versions

- `skill.schema.json` gains a strict 1.1 branch for `resolution`, separate
  `selected_only|confirmed_filter` context policies, semantic/value types and
  `parameters[*].context_slot_keys`; legacy 1.0
  remains readable/exportable with no inferred resolver/context policy.
- `skill-package.schema.json` gains a matching 1.1 branch and may contain only
  schema-valid embedded skill versions. Existing 1.0 package bytes/digests are
  never rewritten.
- all slice-3 resolver/context-producing skills are republished with new SemVer,
  digest and exact dependency locks; unrelated skills need no rewrite.
- `planner-output.schema.json` remains 1.0.0. Pending resume constraints are
  server-side; the model cannot supply pending state.
- Evidence writes remain exactly 1.1.0. Slice-2 keyset fields, validation and
  compatibility branches are unchanged.

### 13.2. SQLite/Alembic

One forward Alembic revision creates `context_slots`, `context_slot_members` and
`pending_clarifications`, plus indexes/uniqueness needed for one active slot and
one active pending record per session. `context_facts` becomes immutable origin
storage rather than the active-list API and is generalized from entity-only
validation to the closed typed entity/scalar model above. This is a deliberate
model/API migration; non-entity values are not accepted unless their safe policy,
origin source and exact consumer compatibility validate.

Legacy baseline context rows cannot be assumed selected because old evidence
exported every entity row. Migration preserves turns/evidence/history but marks
all pre-slice3 handles inactive with `invalidated/migration_selection_unproven`.
It does not guess winners from presentation, creation order or last row. User
must rerun the selecting question. This is a deliberate fail-closed compatibility
break for active context, not loss of chat history.

Restart/reload must preserve new active slots, replaced/expired/invalidated
history and pending state. Maintenance scope `sessions` deletes the whole new
graph; `traces` and `raw_payloads` retain the existing slice-2 closure semantics.

## 14. Executable acceptance suite

Required suite name: `tests/acceptance_slice3`. It uses only public HTTP/SSE,
fixture DeepSeek/MCP control, exported trace ZIP and a second process after
restart. Mandatory groups:

| Test group | Required assertions |
| --- | --- |
| `test_resolver_protocol.py` | every protocol instance has 0/1/N; exact role/identity; display/select mode matrix |
| `test_context_exports.py` | N candidates/list/detail rows export zero; selected one/set exports exact proof only |
| `test_pending_clarification.py` | persist/restart/claim/consume/expire/supersede/concurrent claim and resume rules |
| `test_context_slots.py` | replace one slot, retain others, refresh, expiry, invalidation and stale-handle rejects |
| `test_entity_binding.py` | exact canonical ref reaches MCP; renamed presentation passes; UUID/type/semantic/provenance mutations fail before MCP |
| `test_scalar_filter_context.py` | safe scalar types/provenance/allowed sources/consumer compatibility; Q091 moment bytes are unchanged through Q092/Q093 with no refresh |
| `test_planner_opaque_context.py` | captured outbound HTTP has no full ref fields/canary; internal diagnostic replay remains complete |
| `test_portability.py` | synthetic pair imports into clean instance and works without source change |
| `test_slice2_non_regression.py` | Evidence 1.1 required/scope fields and all keyset/continuation gates remain green |
| `test_corpus_exit_matrix.py` | section 15 scenarios and exact context edges/outcomes |

Property/mutation matrix includes at least:

- same presentation/different UUID;
- same UUID/different physical type;
- same physical UUID/different semantic type;
- same exact triple/new presentation/property order;
- datetime/period/enum/detail exact type and allowed-domain mutations;
- Q091 canonical moment bytes/digest versus Q092/Q093 outbound MCP parameters;
- duplicate candidate identity in two rows;
- wrong customer/supplier role proof;
- forged/cross-session/replaced/expired/invalidated handle;
- missing origin turn/fact/skill digest/column binding;
- N candidates, N display rows and N selected-set rows;
- six candidates/`has_more` reject visible-page choice and allow only typed narrow;
- selected set with `visible_page`, duplicate member and 101 members;
- restart before clarification response and before follow-up;
- two concurrent claims of one pending handle;
- pending cancel and explicit single-filter removal with zero external calls;
- catalog/marker/context version change between question and clarification answer;
- synthetic semantic/physical/slot names absent from baseline source.

## 15. Exit matrix

`Pass` below means fixture black-box pass with exact Evidence/trace/API proof and
live pass when MCP acceptance environment is available. Missing execution is
`not_run`/`blocked_external`, never inferred pass.

| Scenario | Required slice-3 proof | Immediate failure |
| --- | --- | --- |
| Q013 | item `select_one`; N creates pending; selected exact ref feeds details/barcode | first candidate chosen, candidate handles exported, second item search after choice |
| Q014 | same item ref feeds unit details; unit fact is not stock quantity | quantity rendered as unit or item identity lost |
| Q015 | item result is `display_only`, keyset behavior unchanged, no row context exports | page rows enter context or page count is labeled total; full Q015 remains `not_run` until its separate aggregate producer |
| Q016 | item-group exact semantic/role proof; same group ref drives members/count with explicit descendants rule | ordinary item accepted as group or group silently changes |
| Q017 | customer exact semantic plus hard role proof feeds details | partner contract/settlement object substitutes customer |
| Q018 | supplier hard role proof and substring result is `display_only` | customer-only row included or supplier list exported to context |
| Q019 | exact warehouse type fact proves retail; list is `display_only` | name heuristic, fictional organization field or all list rows exported |
| Q020 | one selected organization constrains separately typed enterprise/POS cash-desk producers; exact organization equality | cash desk of another organization, kinds merged by label or physical type core branch |
| Q029 | selected group survives; N price types persist one pending; choice resumes original request once | recent-message replan loses group, first price type chosen, duplicate claim executes |
| Q037 | active sales-order handle resolves exact ref; no number search; returned order identity constraint | changed UUID/type/semantic or any second order resolver call |
| Q042 | selected purchase-receipt handle feeds lines; final facts are all receipt lines | independent latest-receipt search or line item refs exported |
| Q056 | N retail warehouses create pending; chosen warehouse alone feeds rank | latest/first warehouse tie-break or unchosen candidates exported |
| Q057 | typed comparable metric/unit pending; exact choice is frozen on resume | LLM guesses quantity/value/unit or changes requirement |
| Q062 | transfer resolver 0/1/N; only selected exact transfer gets active slot | empty exports handle or N silently selects one |
| Q063 | same transfer slot feeds status with exact equality | transfer search/replacement or stale handle accepted |
| Q064 | same transfer slot feeds lines; line refs do not replace transfer | different transfer or line entities exported as selections |
| Q073 | exact selected customer slot feeds details | recent partner or same-name entity selected |
| Q081 | organization ambiguity persists; one choice feeds cash balance; cash rows remain display facts | organization guessed or candidate/cash-row context pollution |
| Q082 | exact organization entity and `time.moment/datetime` filter are retained by separate proofs; only requested cash-desk kind/detail changes | moment refreshed/recomputed, organization replaced or one kind silently omitted |
| Q091 | item and warehouse resolver sets are complete selected filters; `filter.balance_moment` stores its one confirmed canonical datetime value and bytes | arbitrary stock row entities exported, visible page committed as set or moment left only in answer text |
| Q092 | only `selection.item` is replaced; warehouse and moment handles remain exact; MCP receives moment bytes byte-for-byte equal to Q091; old item handle is rejected | append-only item ambiguity, collateral replacement, `now()`/turn-time/MCP refresh or value reserialization |
| Q093 | detail preference may replace only `presentation.detail`; item/warehouse/moment generations are unchanged and MCP moment bytes still equal Q091 | entity/filter slot replaced, moment refreshed, or filters re-resolved from text |
| Q094 | rank-one yields one selected receipt; non-winning rows export none | all ranked receipt rows exported or incomplete rank accepted |
| Q095 | exact receipt handle feeds header; no independent receipt lookup | latest/same-number receipt substituted or intermediate fact treated final |
| Q096 | rank-one yields one selected `party.customer`; other debt rows export none | contract/partner ref exported as winner or N rows enter context |
| Q097 | selected customer exact ref feeds details; wrong semantic/physical type fails pre-MCP | contract/settlement object or same presentation accepted |
| Q108 | prior item handle feeds portable detail skill; synthetic entity pair proves same core path | item-specific core branch or new item lookup by presentation |

## 16. Противоречивый self-review и принятые решения

| Challenge | Rejected shortcut | Normative decision |
| --- | --- | --- |
| Как отличить candidate от selection? | Export every entity fact/final row | Core-derived SelectionProof plus portable allowlist |
| Что означает N? | Always clarify or always save list | Mode derived from downstream cardinality: one/set/display |
| Можно ли сохранить keyset page как set? | Hidden drain or `has_more=false` last-page inference | Only materialized `complete_set<=100`; keyset semantics unchanged |
| Можно ли хранить retained moment как entity? | Retag scalar or recompute moment in follow-up | Separate `confirmed_filter` plus exact value type/provenance/consumer proof; bytes are reused unchanged |
| Может ли scalar policy ослабить entity proof? | Put `_objectRef` under generic JSON/scalar mode | Modes are disjoint; every entity still requires exact producer physical proof |
| Как выбрать из двух context objects? | Latest turn/presentation similarity | Exact accepted slot; otherwise clarification |
| Достаточны ли semantic type и UUID? | Ignore physical type | Exact semantic + physical + UUID triple |
| Должен ли LLM видеть ref для follow-up? | Send `_objectRef` in prompt | Opaque outbound view; full ref only server-side/diagnostics |
| Можно ли полностью убрать full ref из trace? | Redact replay-critical server state | Internal protected trace keeps it; actual HTTP artifact remains opaque |
| Можно ли retag partner по role param? | Core map/switch or label inference | Fact arrives with exact semantic type and hard producer role proof |
| Что делать с clarification answer? | Replan the recent transcript and let model choose | Persist one-use state; exact candidate resume or frozen typed composition |
| Что делать при шести кандидатах? | Let user choose from truncated first five | Only typed narrowing of the same pinned resolver until complete `2..5` choices or one result |
| Что делать при failed resumed execution? | Commit new slot before evidence success | Consume claim, discard staged mutation, retain prior active slots |
| Можно ли активировать legacy context? | Treat last append-only row as selected | Invalidate as migration-unproven; preserve history |
| Требуется ли Evidence 1.2? | Add permissive fields/defaults | Keep Evidence 1.1 exact; group exports by shared handle and validate cross-artifact |
| Можно ли считать hard word signals resolver-ом? | Add every entity name/prefix to core lists | Таких application-level signals нет; unseen synthetic entity работает из portable contract |

## 17. Slice exit gate и result record

Slice 3 can close only when:

- every mandatory test group in section 14 passes on fixture transport;
- all section 15 rows pass except the explicitly isolated `Q015.total/full`
  component, which remains `not_run` without its aggregate producer;
- AC-016 is evaluated over all accepted 11 follow-ups and reaches at least
  10/11, while every wrong retained identity is a hard fail;
- restart preserves active slots and pending clarification;
- outbound DeepSeek capture remains opaque while diagnostic replay is complete;
- synthetic resolver portability passes in a clean second instance;
- synthetic scalar retention and Q091-to-Q093 byte-stable moment gates pass;
- Evidence 1.1/keyset/continuation regression suite from slice 2 is green;
- source mutation checks find no object/Q-ID/physical-type branches in core.

The result record stores app/package/schema versions, catalog snapshot/marker,
request/session/turn/trace IDs, resolver spec digest/use mode/candidate count,
SelectionProof or FilterRetentionProof digest, pending transition, context
mutation before/after handles and value digests, Evidence digest/coverage/
collection scope, MCP call count and assertion status.
Missing or blocked evidence is recorded explicitly and cannot be promoted to
pass by another scenario.
