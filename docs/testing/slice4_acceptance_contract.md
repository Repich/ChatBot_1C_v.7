# Slice 4 acceptance contract

## 1. Правило приемки

Приемка проверяет полные typed plans и evidence, а не прямой вызов внутренней
функции. Ожидания выводятся из требований и ADR-0004, не из текущего runtime.

## 2. Минимальный красный набор

До production-кода должны существовать следующие параметризованные тесты:

1. `test_s4_closed_operator_signatures_and_allowlist`.
2. `test_s4_typed_coverage_mutations_fail_before_mcp`.
3. `test_s4_whole_set_operators_reject_visible_page_without_hidden_calls`.
4. `test_s4_count_distinct_uses_declared_composite_identity`.
5. `test_s4_decimal_aggregate_and_average_expose_denominator`.
6. `test_s4_null_zero_empty_and_divide_by_zero_matrix`.
7. `test_s4_generic_rank_n_and_ties_are_permutation_stable`.
8. `test_s4_sign_null_filter_preserves_scope_shape_and_provenance`.
9. `test_s4_join_checks_typed_keys_multiplicity_and_fanout`.
10. `test_s4_calculate_rejects_unit_currency_and_semantic_mismatch`.
11. `test_s4_group_uses_half_open_calendar_grain_and_stable_order`.
12. `test_s4_operator_evidence_replay_and_tamper_detection`.
13. `test_s4_unseen_semantics_package_roundtrip_two_clean_instances`.
14. `test_s4_representative_full_plans` для Q032,Q033,Q034,Q038,Q089,Q023,Q024.

## 3. Нормативные оракулы

### 3.1 Collection scope

- count над `visible_page` возвращает только visible count;
- total count, aggregate, rank и полная group требуют `complete_set`;
- `has_more=false` у отдельной continuation page не повышает scope;
- ни один operator не вызывает MCP и не получает следующую страницу скрыто;
- page count не удовлетворяет total requirement.

### 3.2 Count

- distinct key непустой и существует в каждой строке;
- составной key канонизируется с сохранением declared type;
- count empty complete set равен typed zero;
- duplicate rows не меняют distinct count;
- document count не может использовать line/item identity.

### 3.3 Aggregate/group

- input complete, без truncated/partial/continuation;
- каждая группа возвращает все group dimensions и один result measure;
- sum/average/min/max используют Decimal;
- average возвращает denominator и его identity definition;
- mixed/unresolved units отклоняются до публикации derived facts;
- empty average/min/max дает empty, не zero;
- отсутствующие calendar buckets не создаются.

### 3.4 Filter

- zero, positive, negative, null и not-null проверяются отдельно;
- sign predicates отклоняют нечисловой fact;
- `is_null` принимает только explicit null observation;
- retained rows сохраняют исходные facts и provenance;
- output scope равен input scope.

### 3.5 Rank

- generic rank принимает полный output другого operator;
- permutation входных строк не меняет stable result;
- include-all сохраняет всю boundary tie;
- stable-first без полного tie key отклоняется;
- mixed units/currencies и null sort отклоняются;
- exact-one tie не экспортирует context без выбора.

### 3.6 Join/calculate

- entity key сравнивает semantic type, physical type и UUID;
- scalar key требует exact semantic/value/unit/time shape;
- join по presentation запрещен;
- declared multiplicity проверяется до материализации;
- output projections не имеют colliding fact IDs;
- multi-input join требует общий read epoch;
- add/subtract требуют exact unit, divide by zero отклоняется;
- planner не может свободно назначить result semantic type.

### 3.7 Evidence

- каждый operator-step имеет `source_kind=deterministic_operator`;
- operation ref, plan/shape/input/output digests и member counts совпадают;
- derived facts имеют operator-result locator и parent/source-set proof;
- replay verifier получает те же rows, values, units и order;
- tampered plan, input fact, unit, group key или derivation digest дает
  `contract_error`.

## 4. Mutation matrix

Обязательные mutations:

- page scope заменен на complete;
- distinct identity заменена с document на line;
- currency одного fact изменена;
- balance moment заменен period;
- revenue semantic type переименован в profit/debt;
- nullable measure удален или заменен нулем;
- join key заменен presentation;
- multiplicity one-to-one получает duplicate key;
- denominator average удален;
- tie key сокращен;
- derivation input digest изменен;
- старый plan 1.0 с ранее неисполнявшимся оператором подан после обновления.

Каждая mutation должна отклоняться до необоснованного ответа. Если нарушение
видно до MCP, число MCP calls равно нулю.

## 5. Portability

Синтетический package с неизвестными semantic/physical types импортируется в
два clean data dirs. Web и CLI получают одинаковые digests. Следующий turn без
restart использует package. Source scan запрещает Q-ID/object-name/semantic-
prefix branches в core.

## 6. Live MCP gates

Fixtures не заменяют:

- реальную компиляцию fixed 1C queries и linked temp batch;
- physical MCP types и metadata release 11.5.27.56;
- смысл регистров revenue/cost/profit, AR/AP, stock и cash;
- реальные currency/unit/time coordinates и join cardinality;
- independent control queries на том же observable marker;
- read-only audit, same-marker repeatability, latency и recovery;
- перенос package во второй совместимый live instance.

До доступности live MCP эти пункты имеют статус `blocked_external`, но fixture
contract, negative matrix и regression suite должны быть зелеными.

