# Приемочный контракт slice 2

## 1. Назначение и граница

Документ фиксирует black-box решения для outcome, pagination, failure handling
и maintenance slice 2 на baseline `93f12a3`. Он дополняет, но не ослабляет
`slice1_acceptance_contract.md` и `test_strategy.md`. Проверки работают через
HTTP/SSE и наблюдаемый fixture transport; импорт production-модулей не является
приемочным доказательством.

Нормативные источники: `request_lifecycle.md`, `skill_contract.md`,
`persistence_and_observability.md`, `integration_contracts.md`,
`full_catalog_blueprint.md`, `implementation_slices.md` и
`schemas/evidence.schema.json`. Общий API prefix: `/api/v1`.

## 2. SB-01: effective empty и one-row null

Классификатор применяет проверки в таком порядке:

1. Transport/provider status.
2. MCP envelope и declared schema/column bindings.
3. Cardinality, row identity, MCP type и nullability.
4. Effective empty либо exact numeric zero.
5. Final coverage/completeness.

Null-sentinel существует только при всех условиях:

- envelope имеет `success=true` и ровно одну data row;
- declared schema и column bindings валидны;
- ни один `row_identity_fact_id` не имеет ненулевого значения;
- все row-projected facts каждого candidate `required_fact_set` равны `null`;
- каждый такой fact объявлен `nullable=true`.

Pinned input facts вроде period/moment не превращают sentinel в factual row.
Строка с валидной identity и пустым optional/nullable реквизитом не является
sentinel и остается `success_with_rows`.

| Input после structural validation | `empty_semantics` | Terminal outcome | Stable reason/code |
| --- | --- | --- | --- |
| 0 rows или null-sentinel | `confirmed_not_found` | `success_empty` | `not_found` |
| 0 rows или null-sentinel | `confirmed_no_rows` | `success_empty` | `no_rows` |
| 0 rows или null-sentinel | `not_applicable` | `contract_error` | `RESULT_EMPTY_SEMANTICS_NOT_APPLICABLE` |
| 0 rows или null-sentinel | `error_if_empty` | `contract_error` | `RESULT_EMPTY_FORBIDDEN` |
| Required fact равен `null` при `nullable=false` | любое | `contract_error` | `RESULT_REQUIRED_FACT_NULL` |
| Import-valid non-paginated aggregate fact содержит типизированное `0` и fact входит в `zero_fact_ids` | любое | `zero_aggregate` | zero fact ID сохраняется; scope `complete_set` |

`null` никогда не coerced в `0`. Ни 0 rows, ни null-sentinel не дают
`partial`. Для `contract_error` factual row rendering и context export
запрещены.

## 3. SB-02: exact `partial`/`contract_error` boundary

### 3.1. Schema decision and step criticality

`planner-output.schema.json` remains `1.0.0`; no `required/optional` property is
added to `SkillCall` or operators. Criticality is core-derived and the model
cannot override it. `interpretation.required_facts[*].required` is the only
criticality source. `required_output_fact_ids` is a per-call response demand
set, not a step flag.

`PlanValidator` computes one immutable `CoverageProof` before external calls:

1. Build the canonical dependency DAG from every `binding.source=step` and
   every operator input. `result.steps` order is topological and is the stable
   tie-break.
2. Resolve every `final_outputs[*]` to exactly one fact requirement by full
   semantic/value/cardinality/unit/time/identity signature from the pinned
   producer contract.
3. Require exactly one final output for every `required=true` requirement;
   allow zero or one for `required=false`. Reject unclaimed finals, duplicate
   providers, mixed-criticality duplicate requirements and ambiguous matches.
4. Let `required_roots` be steps owning required final outputs. Their reverse
   transitive closure, including roots, is `required_closure`.
5. Reverse closure from all required and optional final outputs is
   `all_closure`. `required_closure` steps are required; the set difference
   `all_closure - required_closure` is optional.
6. Reject every step outside `all_closure`. A self-declared
   `required_output_fact_ids` cannot make an otherwise unused step live.
7. Recompute each skill call demand from final refs, downstream fact reads and
   required identity/unit/time/support facts; it must match
   `required_output_fact_ids`.

Stable semantic rejects are:

| Condition | Code |
| --- | --- |
| Execute has no required requirement or required requirement has no final provider | `PLAN_FACT_REQUIREMENT_UNMET` |
| Final ref names no producer fact | `PLAN_FINAL_FACT_UNKNOWN` |
| Final ref covers no declared requirement | `PLAN_FINAL_OUTPUT_UNCLAIMED` |
| Final/requirement mapping is duplicate or ambiguous | `PLAN_FINAL_OUTPUT_AMBIGUOUS` |
| Step is outside closure of all finals | `PLAN_STEP_UNUSED` |
| `required_output_fact_ids` differs from computed producer demand | `PLAN_REQUIRED_OUTPUT_FACTS_MISMATCH` |
| Page-scoped count is mapped to a total-count requirement | `PLAN_COUNT_SCOPE_MISMATCH` |

A predecessor shared by required and optional finals is required. Any producer
feeding a required parameter/operator is required even if its own final fact is
optional. Both sides of a required `left`/`full` join are required; best-effort
enrichment must be a separate optional final branch. `on_empty` never changes
criticality.

`CoverageProof`/turn details records for each step expose `step_id`,
`criticality=required|optional`, `required_by_requirement_ids`, execution state
and optional `blocked_by_step_id`. This is diagnostics, not ordinary chat text.

Evidence coverage preserves requirement criticality one-to-one. Every planner
`requirement_id` appears exactly once with the same mandatory boolean
`required` in newly generated evidence. Optional requirement with no
final/provider remains `status=missing` and empty `fact_instance_ids`.

Coverage status and collection completeness are different claims:

- `status=covered` means that referenced facts validly cover the requirement's
  semantic type, value type, cardinality, unit and time coordinates;
- valid facts from a page remain `covered` and keep their `fact_instance_ids`;
  pagination incompleteness is never rewritten as `missing` or
  `wrong_cardinality`;
- immutable `PlanCoverageProof` records for every requirement its exact final
  provider and `collection_obligation=fact|visible_page|complete_set`;
- `partial_until_all_pages`, global count/rank/aggregate and every downstream
  operation requiring all input rows have `complete_set`; `page_is_complete`
  has `visible_page`; non-collection scalar/exact facts have `fact`.

The normative formula is:

`sufficient = all(status == covered and collection_obligation_satisfied for entries where required == true)`.

`visible_page` is satisfied by a valid mapped page even when it has a
continuation. `complete_set` requires complete-set evidence across every
collection-producing ancestor and no `partial`, `truncated` or `has_more` state
in that required path. A standalone `partial_until_all_pages` page, including
the last continuation page, remains incomplete unless core explicitly
materialized and evidenced the whole same-marker set. Optional
missing/ambiguous/incompatible/incomplete entries remain observable but do not
change `sufficient`, outcome or required context exports.

Exact validation is a cross-artifact turn gate over the pinned PlannerOutput,
immutable PlanCoverageProof, catalog snapshot and EvidenceBundle. It compares
the exact requirement-ID set and multiplicity, `semantic_type` and `required`,
checks fact refs against the mapped final provider, recomputes collection
obligations from the plan/catalog, and compares `sufficient` in both directions.
Stable rejects are `EVIDENCE_COVERAGE_ID_MISMATCH`,
`EVIDENCE_COVERAGE_CRITICALITY_MISMATCH`,
`EVIDENCE_COLLECTION_COMPLETENESS_MISMATCH` and
`EVIDENCE_SUFFICIENT_MISMATCH`. An EvidenceBundle-only validator may enforce
necessary internal conditions, but must not infer planner criticality or reject
`sufficient=false` merely because all required statuses say `covered`.

Evidence versioning decision is normative:

| Version | Read contract | Write contract |
| --- | --- | --- |
| `1.0.0` | Legacy read compatibility only. Missing `steps[*].collection_scope` is normalized in memory to `complete_set`; missing `coverage.requirements[*].required` is normalized to `true`. | No new runtime bundle may be emitted as 1.0 after slice-2 activation. Frozen fixtures and stored payloads are not rewritten. |
| `1.1.0` | Both fields are mandatory. Omission is a schema/DTO contract error; no default or inference is applied. | Required version for every newly generated evidence bundle; producers emit both fields explicitly. |

`schema_version` is read before compatibility handling and is never inferred
from field presence. Only the 1.0 parser branch may materialize the two legacy
values, into an in-memory copy; 1.1 and unknown versions receive no fallback.
`schemas/evidence.schema.json` accepts both versions and conditionally requires
the fields for 1.1, without JSON Schema `default` annotations.

Developer handoff is exact: new builders emit 1.1, copy `required` from
immutable PlanCoverageProof, pass core-derived `collection_scope` at every
step, emit all optional rows even without a final provider, and run the
cross-artifact gate before persistence/rendering. The legacy adapter is
read-only compatibility, not an executor-specific exception.

If a future planner must carry two same-signature finals with distinct business
roles, the minimal next schema change is `requirement_id` on `stepFactRef` with
a version bump. A planner-supplied step-criticality flag remains forbidden.

### 3.2. Execution and reduction

`contract_error` означает, что producer response нельзя считать evidence.
Весь step response discarded. Outcome применяется к required step даже тогда,
когда другой step уже вернул валидные данные.

Contract violations:

- malformed/ambiguous MCP wrapper или invalid envelope;
- missing/unexpected required column либо incompatible MCP type;
- forbidden null, cardinality или row-identity mismatch;
- binding/result constraint violation;
- truncation при `truncation_policy=error_if_truncated`.

`partial` означает, что каждый отображаемый fact уже прошел producer contract,
но полного final coverage нет. Допустимые причины ограничены:

- `partial_until_all_pages` и не получены все страницы;
- safety cap или общий deadline остановил чтение после валидной страницы;
- query/dependency failure другого required step при наличии хотя бы одного
  independently valid final fact.

Executor performs required steps first in canonical topological order. A
required query/MCP failure blocks only its descendants; independent required
branches still run. Optional closure runs only after complete required coverage.
Required contract failure starts no new steps. Optional failure of any type
discards that response, blocks only optional descendants and does not change a
sufficient required result.

Final reduction order:

1. Any required-step `contract_error` -> terminal `contract_error`; normal facts
   and all context exports are suppressed.
2. All required requirements have `status=covered` and satisfied collection
   obligations -> success/empty/zero determined only by required evidence;
   missing optional outputs never mean `partial`.
3. Some required requirement missing after query/dependency failure and at
   least one other required final requirement valid -> `partial`.
4. No required final requirement valid -> typed outcome of the earliest
   canonical root failure. Blocked descendants do not add failures. A required
   contract violation dominates regardless of its position.

For a fully covered data plan, success subtype is deterministic: any non-empty
required row set -> `success_with_rows`; otherwise any required exact-zero
aggregate -> `zero_aggregate`; otherwise `success_empty`. Optional evidence
cannot promote the subtype.

Valid optional or intermediate facts do not satisfy rule 3. `llm_unavailable`
before validated planning is outside the DAG and remains terminal directly.
`page_is_complete` permits `success_with_rows` plus continuation; continuation
alone does not mean `partial`.

### 3.3. Acceptance cases

| Case | Expected |
| --- | --- |
| Required final `s3` depends on `s2`, which depends on `s1` | `s1,s2,s3` all required |
| Independent optional final branch `s4 -> s5` | only `s4,s5` optional; it runs after required coverage |
| Producer is shared by required and optional roots | shared producer is required |
| Shared required producer misses an optional-branch demanded column | terminal required `contract_error`; isolate best-effort enrichment in a separate optional call |
| Optional-looking producer feeds required parameter | producer is required by reverse closure |
| Required left-join result has left/right producers | both producers and join are required |
| Final output covers no requirement | pre-execution `PLAN_FINAL_OUTPUT_UNCLAIMED` |
| Two finals can cover one requirement | pre-execution `PLAN_FINAL_OUTPUT_AMBIGUOUS` |
| Step is referenced only by its own `required_output_fact_ids` | pre-execution `PLAN_STEP_UNUSED` |
| Optional requirement has no final output and no step | valid plan; no missing-output error |
| Evidence for omitted optional final | exact coverage row `required=false,status=missing`; `sufficient=true` only when every required row is covered and its collection obligation is satisfied |
| Optional is missing and evidence says `sufficient=false` | valid when a required collection obligation is incomplete; otherwise `EVIDENCE_SUFFICIENT_MISMATCH` and cross-validator recomputes true |
| Evidence omits/changes planner requirement criticality | evidence `contract_error` |
| Required `partial_until_all_pages` keyset page has valid final facts | requirement remains `status=covered` with fact refs; `complete_set` obligation false, `sufficient=false`, outcome `partial` |
| Terminal continuation page has `has_more=false` but prior pages are not materialized in this bundle | still `collection_scope=visible_page`; insufficient for required `complete_set` |
| All requirements are optional | pre-execution `PLAN_FACT_REQUIREMENT_UNMET` |
| Valid rows of required step miss a demanded column | required `contract_error`, normal rows/context hidden |
| Required branch has schema mismatch; independent required branch succeeds | terminal `contract_error`, not `partial` |
| Required branch returns `success=false`; independent required final succeeds | `partial`; failed branch descendants blocked, independent branch called |
| Required branch returns `success=false`; only optional final succeeds | `query_error`, not `partial`; optional branch is not run after required failure |
| Two required non-contract root failures, no required fact | typed outcome of earliest root in `result.steps` order |
| Optional branch has query/MCP/contract failure after required success | required success unchanged; optional response discarded, no optional context export |
| One optional branch fails while another optional branch is independent | independent optional branch still runs |
| Required empty uses `stop_not_found` | terminal `success_empty`; optional-only closure is not run |
| Required empty is allowed to continue and optional final has rows | required empty semantics still controls outcome; optional data cannot turn it into success-with-rows |
| Valid page, `page_is_complete`, `has_more=true` | `success_with_rows` plus continuation |
| Valid pages stop at cap under `partial_until_all_pages` | `partial` plus continuation/rerun guidance |

## 4. Q031: visible count versus total

### 4.1. Slice decision

Для slice 2 выбрана option (a): SP04 доказывает только list/keyset pagination и
visible count. `Q031.list` может стать `pass`; `Q031.total` и полный corpus Q031
остаются `not_run`. Это не partial pass полного сценария. Если полный Q031 был
запущен и не вернул distinct total, result равен `fail` относительно corpus
oracle, независимо от корректности первой страницы.

Full-release path использует option (c), но вне slice 2: отдельный SP06
aggregate producer возвращает distinct shipment-document total на тех же
normalized period/filter coordinates и marker. Option (b), скрытый drain SP04
ради downstream count, в slice 2 запрещена.

Это общее правило затрагивает и Q015: `Q015.list` может проверяться в slice 2,
но `Q015.total` и full Q015 остаются `not_run` до отдельного item aggregate
producer. CountOperator над одной keyset page номенклатуры не дает количество
всех найденных позиций.

Фраза corpus `общим количеством` создает required total-count requirement.
Planner не может понизить его до optional. SP04 и MCP envelope `count` дают
только page evidence. `CountOperator` читает facts одного StepResult, не
запрашивает continuation и получает core-derived scope:

| Input contract | Count scope | Может закрыть total requirement |
| --- | --- | --- |
| SP04 initial/continuation keyset page, любое `has_more` | `visible_page` | Нет |
| Любой `page_is_complete` StepResult | `visible_page` | Нет |
| Fully materialized proved-prefix set | `complete_set` | Да |
| Непагинируемый full-set или отдельный aggregate producer | `complete_set` | Да |

Scope хранится в CoverageProof/evidence semantics и не является planner flag;
`planner-output.schema.json` не меняется. Page-to-total mapping отклоняется до
MCP с `PLAN_COUNT_SCOPE_MISMATCH`. Document count uses distinct
`shipment.ref`; line/row count запрещен.

Evidence 1.1 требует explicit
`steps[*].collection_scope=visible_page|complete_set`; default запрещен.
Omitted/changed scope is evidence `contract_error`, потому что absence нельзя
безопасно трактовать как complete set. Исключение существует только при чтении
legacy 1.0: omission нормализуется в `complete_set` до общей semantic validation
и не разрешает выпуск нового 1.0 evidence. Runtime builder обязан передавать
CoverageProof-derived value при каждом создании StepEvidence.

### 4.2. Q031 acceptance cases

| Fixture/plan | Expected |
| --- | --- |
| Oracle has 22 shipments; SP04 returns first 20 plus probe | `shown=20`, `has_more=true`, label `Показано 20`; no `Всего 20` and no total fact |
| Planner maps CountOperator(SP04) to required total | pre-execution `PLAN_COUNT_SCOPE_MISMATCH`; no MCP call |
| First SP04 page contains 20 and `has_more=false` | count remains page-scoped by producer contract; it is not promoted to total |
| Final continuation contains 2 and `has_more=false` | visible count is 2, never total 2 or accumulated total 22 |
| SP04 page 20 plus SP06 aggregate 22 with identical coordinates/marker | after SP06 activation, full Q031 `success_with_rows`, `Показано 20`, `Всего 22` |
| SP04/SP06 period, filters or marker differ | `contract_error`; core does not choose either value |
| Full Q031 omitted from slice-2 run | `Q031.list=pass`, `Q031.total=not_run`, full Q031 `not_run`, reason recorded |
| Full Q031 returns page count as total | full Q031 `fail`; never accepted as partial/not_run |
| Q015 keyset page is counted by CountOperator | only `Показано N`; `Q015.total` remains uncovered and full Q015 cannot pass |

## 5. Pagination boundedness and exact boundary

### 5.1. Prefix proof and boundary

`prefix.maximum_total=M` is a claim, not a safety cap or proof. Prefix is valid
only when a digest-pinned source cited by `provenance.source_references` proves
`cardinality(filtered result) <= M <= 1000` independently of current database
contents. Accepted proof classes are metadata/config-enforced cardinality,
closed finite domain, or bounded input with proved one-row-per-input mapping.
Not accepted: `ПЕРВЫЕ M`, MCP/query/UI limit, current fixture/live count,
expected-small catalog, exact text predicate, or XML that only lists fields.

No pagination schema field is added: `maximum_total` and the existing
`provenance.source_references` carry the claim/citation, while semantic import
validation proves that the cited artifact actually enforces the bound. A bare
XML URI with no cardinality assertion fails that check.

Prefix execution materializes the entire ordered set once into immutable source
evidence and serves display pages locally. Boundary rules are exact:

| Actual/provided state | Expected |
| --- | --- |
| `M-1` rows, no provider truncation | complete set; local pages end with `has_more=false` |
| Exactly `M` rows, no provider truncation | complete set; M is not treated as an inferred continuation |
| `M+1` rows or provider `truncated=true/has_more=true` at M | required `contract_error`, code `RESULT_PREFIX_BOUND_EXCEEDED`; all rows discarded |
| Adapter cannot distinguish complete M from truncation | skill import/activation rejected; use keyset |

Prefix continuation stores an offset into immutable evidence and performs no
MCP call. The 30-minute/single-use/session/catalog/marker checks still apply.

### 5.2. Unbounded keyset rule

Catalogs, documents, tabular sections and registers are unbounded unless the
prefix proof above exists. They use keyset with `page_size+1`, exact
lexicographic after-predicate and stable total order ending in an immutable
unique row identity. Sort direction, null ordering, cursor encoding and query
predicate must agree. The probe row is not displayed; cursor is built from the
last displayed row. User continuation has no hard total cap 1000.

These are deterministic import/activation invariants for every transferable
keyset skill, not package-specific tests:

1. `keyset|prefix` requires output `cardinality=many`; aggregate/exact
   cardinalities require `strategy=none`. Mismatch is
   `PAGINATION_CARDINALITY_MISMATCH`.
2. `sort[*].fact_id` is unique. `cursor_bindings[*].fact_id` is the exact same
   ordered list; neither a subset nor a permutation is accepted.
3. Cursor query-parameter names are case-insensitively unique. The
   `has_cursor_query_parameter`, cursor parameters and ordinary
   `parameter_bindings[*].query_parameter` are pairwise disjoint.
4. Every sort fact exists, has exactly one column binding, is `required=true`
   and `nullable=false`. Cursor encoding matches its fact value type exactly.
5. `row_identity_fact_ids` is non-empty. A suffix of the declared sort contains
   every full row-identity fact exactly once and contains no non-identity fact;
   therefore equal business sort values still have a unique terminal order.
6. The final query statement uses the has-cursor and every cursor parameter and
   orders by the bound projection expressions in exactly declared order and
   direction. Omitted query direction means `asc` only.
7. Its cursor filter is AST-equivalent to one guarded strict lexicographic
   after-predicate: `>` for an `asc` coordinate, `<` for `desc`, with equality
   prefixes for every later coordinate. A non-strict comparator, missing guard,
   reordered coordinate or extra cursor-dependent branch is rejected.

Rule 6 is a query-contract check over the parsed 1C-query AST. Validator maps
`fact_id -> column binding -> final projection expression`, normalizes harmless
parentheses/aliases and compares predicate/order nodes. It must not search for
operator substrings or trust a duplicated JSON declaration. If the bounded
parser cannot prove equivalence, import fails closed with
`PAGINATION_QUERY_CONTRACT_UNPROVEN`; no runtime sampling fallback is allowed.
The existing keyset DTO is sufficient, so no planner field is added.

Stable semantic import codes are:

| Violation | Code |
| --- | --- |
| Paged strategy with aggregate/exact cardinality | `PAGINATION_CARDINALITY_MISMATCH` |
| Duplicate sort fact | `PAGINATION_SORT_FACT_DUPLICATE` |
| Sort/cursor ordered bijection differs | `PAGINATION_CURSOR_BIJECTION_MISMATCH` |
| Duplicate/colliding keyset parameter | `PAGINATION_CURSOR_PARAMETER_DUPLICATE` / `PAGINATION_PARAMETER_COLLISION` |
| Missing fact/binding or incompatible encoding | `PAGINATION_FACT_MISSING` / `PAGINATION_CURSOR_ENCODING_MISMATCH` |
| Optional or nullable sort coordinate | `PAGINATION_SORT_COORDINATE_INVALID` |
| Declared keyset parameter absent from final query | `PAGINATION_QUERY_CONTRACT_UNPROVEN` |
| Sort does not end in full row identity | `PAGINATION_IDENTITY_SUFFIX_MISMATCH` |
| Query ORDER BY differs | `PAGINATION_QUERY_ORDER_MISMATCH` |
| Guarded strict after-predicate differs | `PAGINATION_QUERY_PREDICATE_MISMATCH` |
| Parser cannot prove query semantics | `PAGINATION_QUERY_CONTRACT_UNPROVEN` |

The baseline `93f12a3` audit found no cardinality proof for six
`prefix:1000` declarations. They are the mandatory migration inventory below;
acceptance inspects the exported/activated candidate and requires each to be
keyset with cursor parameters/query predicates:

| Skill | Required stable keyset basis |
| --- | --- |
| `ut115.ref.item.resolve-article-exact` | item presentation/name plus immutable `item.ref`; duplicate articles remain pageable |
| `ut115.ref.item.resolve-code-exact` | item presentation/name plus immutable `item.ref`; no uniqueness assumption without proof |
| `ut115.ref.item.resolve-barcode-exact` | distinct `item.ref` result, or full register-row identity if duplicates are retained |
| `ut115.ref.item.resolve-name-contains` | normalized declared name order plus immutable `item.ref` |
| `ut115.sales.order-lines` | exact `order.ref` plus metadata-proven unique line number/row identity |
| `ut115.stock.balance` | every output grain identity coordinate, projected as non-null, plus an immutable unique tie-breaker |

`ut115.ref.warehouse.resolve` already declares keyset over warehouse name/ref,
and `ut115.sales.shipment-list` already declares keyset over date/immutable
shipment identity; both must retain those contracts.
`ut115.sales.order-header-status-by-number` may remain `none` only while its
exact zero-or-one/ambiguity contract is proved; documentation retrieval is
outside data pagination.

Acceptance covers equal sort values across page boundaries, 1001+ rows,
forward continuation to terminal page without duplicate/skip, cursor tampering,
and marker/catalog change. Import mutation cases independently remove/reorder a
cursor binding, duplicate IDs/parameters, collide the guard, alter encoding,
make a sort fact nullable, omit one identity coordinate, reverse ORDER BY or
change one strict comparator. Each is rejected before activation with the code
above. Any fallback from failed keyset proof to prefix is an import failure,
not a runtime heuristic.

Zero-aggregate acceptance uses a dedicated non-paginated aggregate skill/query.
A mutation that changes only a keyset list's cardinality to `aggregate` must be
rejected atomically with `PAGINATION_CARDINALITY_MISMATCH`; it is not a valid
fixture for the eight-outcome matrix.

## 6. SB-03: continuation DTO и lifecycle

### 6.1. Public DTO

A terminal paged turn includes:

```json
{
  "pagination": {
    "shown": 20,
    "page_size": 20,
    "has_more": true,
    "continuation": {
      "handle": "page_<base64url-of-24-random-bytes>",
      "expires_at": "2026-07-21T12:30:00Z"
    }
  }
}
```

When `has_more=false`, `continuation` is `null`.

```http
POST /api/v1/sessions/{session_id}/continuations
Content-Type: application/json

{"continuation_handle":"page_<base64url>"}
```

Success is HTTP 202 with `status=accepted`, `turn_id`, `trace_id`. It creates a
new turn but performs no DeepSeek call and no replanning. Skill/version/digest,
normalized params, pagination mode, keyset cursor/sort tuple or prefix evidence
offset, source turn, exact catalog snapshot and database marker come only from
the server-side record.

For keyset, accepted turn calls the pinned producer with the saved cursor. For
proved-prefix, it reads the next slice of immutable source evidence and makes
no MCP call. In both modes `shown` is current public-page size, never total.

### 6.2. TTL and state

- TTL is exactly 30 minutes: valid while `now < expires_at`, expired when
  `now >= expires_at`.
- TTL is not sliding. A next-page handle receives its own 30-minute TTL.
- Claim and accepted-turn creation are atomic. Exactly one concurrent claim can
  receive HTTP 202.
- Handle is consumed at acceptance. A later query/dependency failure does not
  reactivate it.
- Consumed/expired records remain until source-session clear, so those states
  do not degrade to `CONTINUATION_NOT_FOUND`.
- No handle is issued for `has_more=false`.
- Session/turn/trace retention has no automatic TTL because of this handle TTL.

### 6.3. Reject contract

Reject body is
`{status:"rejected",trace_id,error:{code,message_ru,retryable:false}}` and does
not create a turn or invoke DeepSeek/MCP.

| Condition, checked in this order | HTTP | Code |
| --- | --- | --- |
| Invalid syntax | 422 | `CONTINUATION_HANDLE_INVALID` |
| Well-formed forged/unknown handle | 404 | `CONTINUATION_NOT_FOUND` |
| Other session | 409 | `CONTINUATION_SESSION_MISMATCH` |
| Already consumed | 409 | `CONTINUATION_CONSUMED` |
| Expired | 410 | `CONTINUATION_EXPIRED` |
| Active catalog snapshot differs | 409 | `CONTINUATION_CATALOG_CHANGED` |
| Catalog matches, database marker differs | 409 | `CONTINUATION_MARKER_CHANGED` |

Catalog change invalidates the handle even if its historical immutable skill is
still readable; continuation never silently migrates to a new skill or executes
the old snapshot after a new active revision. Catalog is checked before marker,
because the acceptance marker includes a catalog component. Marker-only change
also requires rerunning the original question. No HMAC, signed client payload,
auth or key rotation is required for this local prototype.

## 7. SB-04: maintenance clear preview/confirm

### 7.1. Scope closure

Allowed scopes are a non-empty unique subset of `sessions`, `traces`,
`raw_payloads`; response order is always that canonical order.

- `sessions`: session roots and owned messages/turns/context/clarifications/
  continuations plus associated trace/evidence/raw graph.
- `traces`: trace/evidence/raw graph; session messages and terminal turn summary
  remain, while trace export becomes unavailable.
- `raw_payloads`: diagnostic raw blobs only.

Overlapping scopes form one union; rows are counted/deleted once. Counts always
contain integer keys `sessions`, `traces`, `raw_payloads`, including zeros.
Catalog, help index, database profile and marker are outside every closure.

### 7.2. DTO

```json
{"mode":"preview","scopes":["sessions","traces"]}
```

HTTP 200:

```json
{
  "status": "preview",
  "scopes": ["sessions", "traces"],
  "counts": {"sessions": 2, "traces": 7, "raw_payloads": 11},
  "confirmation_token": "clear_<base64url-of-24-random-bytes>",
  "expires_at": "2026-07-21T12:05:00Z"
}
```

```json
{
  "mode": "confirm",
  "scopes": ["sessions", "traces"],
  "confirmation_token": "clear_<base64url>"
}
```

HTTP 200 success:

```json
{
  "status": "cleared",
  "scopes": ["sessions", "traces"],
  "deleted": {"sessions": 2, "traces": 7, "raw_payloads": 11}
}
```

Token TTL is exactly 5 minutes with the same `< expires_at` boundary. Token is
server-side, bound to canonical scopes, preview counts and an exact target-set
fingerprint. Confirm recomputes the fingerprint and deletes in one transaction;
`deleted` must equal preview `counts`. Token becomes consumed only after commit.
Preview and confirm reject a target containing a non-terminal turn.

| Reject | HTTP | Code |
| --- | --- | --- |
| Invalid/empty/duplicate/unknown scopes or wrong DTO form | 422 | `CLEAR_SCOPES_INVALID` |
| Forged/unknown token | 404 | `CLEAR_CONFIRMATION_NOT_FOUND` |
| Expired token | 410 | `CLEAR_CONFIRMATION_EXPIRED` |
| Already committed token | 409 | `CLEAR_CONFIRMATION_CONSUMED` |
| Confirm scopes differ from preview | 409 | `CLEAR_SCOPE_MISMATCH` |
| Target changed after preview | 409 | `CLEAR_PREVIEW_STALE` |
| Target has a non-terminal turn | 409 | `CLEAR_TARGET_ACTIVE` |

Every reject is atomic and deletes zero rows. The local-only boundary remains
`127.0.0.1`; no auth/HMAC layer is added in slice 2.

## 8. SB-05: AC-024 reporting

AC-024 has two independently reported components:

| Component | Slice 2 evidence | Status rule |
| --- | --- | --- |
| `AC-024.list` | Q012/`Q015.list`/`Q031.list`/Q054 canonical row composition, no duplicate/skip across continuation, same-marker oracle | May become `pass` in slice 2; does not imply full Q015/Q031 pass |
| `AC-024.rank` | M07 identity/measure composition, exact order, direction and ranking measure | `not_run` until M07 suite executes |
| global `AC-024` | conjunction of list and rank components | `not_run` while rank is `not_run`; never inherited from list pass |

Stable keyset order is tested in slice 2 to prove lossless pagination, but it is
not evidence that ranking semantics passed. M07 must separately prove rank
composition, order, direction, measure and stable tie policy. A fixture-only
list result cannot make global AC-024 pass.

Count reporting is orthogonal: Q015/Q031 total and full scenarios remain
`not_run` until their aggregate producers run, even when `AC-024.list=pass`.

Status aggregation is deterministic: any component `fail` makes global
AC-024 `fail`; otherwise `blocked` dominates `not_run`, any remaining
`not_run` keeps global AC-024 `not_run`, and only two `pass` components produce
global `pass`.

## 9. R06 warehouse metadata contract

Direct inspection of
`ut-config://11.5.27.56/Catalogs/Склады.xml` proves standard warehouse ref/name,
`ТипСклада` and `Подразделение`. It does not prove direct attributes named
`Организация` or `Назначение`.

Therefore R06 v1:

- accepts name, `ТипСклада` and optional `Подразделение` only;
- returns warehouse ref plus the matched direct selection attributes;
- Q054 selects retail warehouses by exact `ТипСклада`, never by name text;
- must not bind/project fictional direct `Организация` or `Назначение` fields.

`Поклажедержатель` is a separate union attribute and is not silently renamed to
organization. `Назначение` occurrences inside choice parameters do not define a
warehouse attribute. Organization filtering is allowed only through a separate
skill/composition with exact metadata and query-semantics proof of the join;
inventory purpose remains R12/a proved stock or document dimension. Without
that proof the assistant clarifies a concrete warehouse or returns a capability
gap instead of guessing.

R06 acceptance inspects the portable skill/proof assertions and recorded MCP
request, then runs positive and confirmed-empty fixtures. Live success remains
separate evidence under `test_strategy.md`; synthetic R06 evidence never marks
a live gate pass.

## 10. Required result record

The slice report records for every case: app/package/schema version, fixture or
live profile, catalog snapshot, marker, request/turn/trace IDs, terminal outcome,
public error code where applicable, normalized fact digest, pagination state
and assertion result. It also records exact requirement ID/`required`/status
rows, recomputed `coverage.sufficient`, per-step criticality, collection/count
scope and pagination strategy/proof reference. Missing execution is `not_run`
or `blocked` with reason, never implicit pass.
