# Каталог тест-кейсов ChatBot 1C v7

## 1. Правила каталога

- Ожидаемое пользовательское поведение берется из поля `expected_behavior`
  соответствующего сценария в `tests/corpus/user_questions.yaml`; таблица ниже
  его не дублирует.
- Data facts сравниваются с независимым control query при том же
  `acceptance_observable_state` marker.
- Один case может иметь несколько oracle: например, ranking требует
  `exact_order` и `semantic`.
- Статус `blocked_external` не преобразуется в pass.

## 2. Контрактные и системные кейсы

| Test ID | Уровень | Проверка | Трассировка |
| --- | --- | --- | --- |
| TC-REQ-001 | Requirements | 116 unique contiguous Q IDs и declared scenario count | AC-014 |
| TC-REQ-002 | Traceability | 55 FR, 14 NFR, 59 AC без пропусков | FR/NFR/AC all |
| TC-REQ-003 | Capability | 87 catalog IDs равны corpus capability set | AC-001, AC-059 |
| TC-SCH-001 | Schema | Все schemas валидны Draft 2020-12 | FR-037, AC-011 |
| TC-SCH-002 | Schema | Relative ref package -> skill работает через registry | FR-038..041 |
| TC-SCH-003 | Schema | Valid data/doc/plan/evidence fixtures приняты | AC-002, AC-014 |
| TC-SCH-004 | Schema | Extra fields и decision/result mismatch отклонены | ADR-0001 |
| TC-SCH-005 | Schema | Каждый data query содержит `execution`/`invariant_constants`; nested skill, lock, package и catalog digest согласованы по RFC8785 | ADR-0003 |
| TC-SEM-001 | Semantic | Required output fact без exact binding отклонен | FR-022, AC-019 |
| TC-SEM-002 | Semantic | Placeholder и hardcoded concrete query value отклонены | FR-036, AC-005 |
| TC-SEM-003 | Semantic | Forged/wrong entity ref отклонен | FR-008, FR-023 |
| TC-SEM-004 | Semantic | sufficient=true несовместим с missing requirement | FR-022, AC-020 |
| TC-SEM-005 | Semantic | One-row zero не классифицируется как empty | FR-021, AC-018 |
| TC-ADR-001 | Semantic | `single_select` принимает полный набор допустимых typed constants | ADR-0003 |
| TC-ADR-002 | Semantic | Sequential и branching `linked_temp_batch` имеют один final result и замкнутый backward graph | ADR-0003 |
| TC-ADR-003 | Semantic | Independent/orphan/forward/self/duplicate temp и final `ПОМЕСТИТЬ` отклонены | ADR-0003 |
| TC-ADR-004 | Semantic | DML/DDL/internal empty statement отклонены; misleading tokens в comments/strings не меняют lexer classification | ADR-0003 |
| TC-ADR-005 | Semantic | Undeclared/mismatched/business literals и numeric `IN/BETWEEN/CASE` bypass отклонены | ADR-0003 |
| TC-ADR-006 | Semantic | Parameters валидируются по всем statements, column bindings только по final projection | ADR-0003 |
| TC-SEM-006 | Semantic | Unmet FactRequirement, unknown final fact и semantic/cardinality/unit/time mismatch различимы | ADR-0003 P1 |
| TC-SEM-007 | Semantic | `provides.fact_types` точно равно output semantic types | ADR-0003 P1 |
| TC-SEM-008 | Semantic | Package lock точный и замкнутый; same id/version с другим available digest конфликтует | ADR-0003 P1 |
| TC-SEM-009 | Semantic | Disagreement отклоняет duplicate citation, unknown fact и subject mismatch | ADR-0003 P2 |
| TC-SEM-010 | Semantic | Required/all closure детерминирован; unused rejected; optional failure не меняет достаточный required result | Slice 2 SB-02 |
| TC-SEM-011 | Evidence | `required` exact-copy обязателен; missing optional reported и исключен из `sufficient`; covered required partial page остается insufficient по collection obligation | Slice 2 SB-10/SB-13 |
| TC-SEM-012 | Semantic | Page-scoped CountOperator не закрывает total; Q031 20/22 не становится `Всего 20` | Slice 2 SB-08 |
| TC-PAG-001 | Semantic/integration | Unbounded skills keyset; prefix proof и `M-1/M/M+1` boundary проверены | Slice 2 SB-09 |
| TC-PAG-002 | Semantic import | Keyset sort/cursor bijection, parameter uniqueness, encoding, non-null coordinates, identity suffix и AST query contract fail closed независимо от package ID | Slice 2 SB-12 |
| TC-PAG-003 | Semantic import | `keyset|prefix` с aggregate/exact cardinality rejected; zero aggregate использует отдельный `none` complete-set producer | Slice 2 SB-14 |
| TC-EVD-001 | Schema/runtime | Frozen 1.0 omissions читаются только через legacy defaults; 1.1 omissions/unknown version rejected; новые bundles explicit 1.1 | Slice 2 SB-11 |
| TC-LIM-001 | Semantic loader | Bytes/depth/node/array и embedded-skill ceilings дают отдельные error codes | ADR-0003 P2 |
| TC-MCP-001 | MCP contract | Port публикует только execute_query/get_metadata | FR-011, FR-012 |
| TC-MCP-002 | MCP contract | structuredContent и single text JSON приняты | lessons 3.1 |
| TC-MCP-003 | MCP contract | malformed, nested и ambiguous wrappers отклонены | AC-018, AC-046 |
| TC-MCP-004 | MCP contract | rows/empty/zero/success=false дают разные outcomes | FR-021, AC-018 |
| TC-DS-000 | External smoke | DeepSeek JSON mode: HTTP 200, valid JSON, secrets not recorded; evidence `docs/source_inventory.md#deepseek` | Integration prerequisite |
| TC-DS-001 | DeepSeek contract | Valid planner response извлечен и schema-valid | FR-013 |
| TC-DS-002 | DeepSeek contract | Malformed JSON идет только в bounded repair | NFR-004 |
| TC-DS-003 | DeepSeek contract | query/code/MCP injection запрещены schema | ADR-0001 |
| TC-INT-001 | Live UT | Каждый fixed template проходит metadata/schema/semantic proof | AC-001..004 |
| TC-INT-002 | Live UT | До/после suite нет изменений данных | FR-012 |
| TC-INT-003 | Live UT | Profile подтверждает `supports_linked_temp_batch=true`; producer/final выполняются одним call, envelope содержит только final projection | ADR-0003 |
| TC-COR-001 | Corpus | Q001-Q090 first-attempt >=81 | AC-015 |
| TC-COR-002 | Corpus | Follow-up >=10/11 и exact entity retention | AC-016 |
| TC-COR-003 | Corpus | Negative Q098-Q106 9/9 | AC-017 |
| TC-DOC-001 | Documentation | External source hard rejected | AC-029, AC-030 |
| TC-DOC-002 | Documentation | Две расходящиеся позиции показаны с двумя citations | AC-031 |
| TC-UI-001 | Browser | Sticky composer, Enter/Shift+Enter, timestamps, SSE | AC-033..038 |
| TC-UI-002 | Browser | Catalog view/import/export/replace/delete feedback | AC-039, AC-040 |
| TC-DIAG-001 | Diagnostics | Trace и ZIP позволяют offline replay | AC-041..044 |
| TC-SEC-001 | Security | Secret/path canary отсутствует в Git/UI/log/ZIP | AC-045 |
| TC-FAIL-001 | Failure injection | MCP/DeepSeek/query/contract failures различимы | AC-046, AC-049 |
| TC-PERF-001 | Performance | p100 <=30 s ровно для `Q001,Q011,Q031,Q041,Q051,Q062,Q071,Q081,Q102,Q107` | AC-047 |
| TC-PERF-002 | Performance | p95 <=90 s для всех `Q001-Q116` | AC-048 |
| TC-PORT-001 | Portability | Web/CLI transfer между clean data dirs сохраняет digests | AC-007..013 |
| TC-WIN-001 | Windows | Same release/package проходит обязательный smoke | AC-053 |

## 3. Oracle для каждого вопроса

Допустимые обозначения: `exact_scalar`, `exact_set`, `exact_order`,
`semantic`, `citation`, `clarification`, `refusal`,
`dependency_error`.

| ID | Type | Oracle | Независимый источник | Ожидание |
| --- | --- | --- | --- | --- |
| Q001 | documentation | `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q001.expected_behavior` |
| Q002 | documentation | `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q002.expected_behavior` |
| Q003 | documentation | `exact_order`, `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q003.expected_behavior` |
| Q004 | documentation | `exact_order`, `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q004.expected_behavior` |
| Q005 | documentation | `exact_order`, `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q005.expected_behavior` |
| Q006 | documentation | `exact_order`, `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q006.expected_behavior` |
| Q007 | documentation | `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q007.expected_behavior` |
| Q008 | documentation | `exact_order`, `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q008.expected_behavior` |
| Q009 | documentation | `exact_order`, `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q009.expected_behavior` |
| Q010 | documentation | `citation`, `semantic` | built-in help 11.5.27.56 + citation resolver | corpus `Q010.expected_behavior` |
| Q011 | data | `exact_set`, `semantic` | independent control.q011.v1 + same marker | corpus `Q011.expected_behavior` |
| Q012 | data | `exact_set`, `semantic` | independent control.q012.v1 + same marker | corpus `Q012.expected_behavior` |
| Q013 | data | `exact_scalar`, `semantic` | independent control.q013.v1 + same marker | corpus `Q013.expected_behavior` |
| Q014 | data | `exact_set`, `semantic` | independent control.q014.v1 + same marker | corpus `Q014.expected_behavior` |
| Q015 | data | `exact_set`, `exact_scalar`, `semantic` | independent control.q015.v1 + same marker | corpus list plus found-position total from `Q015.expected_behavior` |
| Q016 | data | `exact_scalar`, `semantic` | independent control.q016.v1 + same marker | corpus `Q016.expected_behavior` |
| Q017 | data | `exact_set`, `semantic` | independent control.q017.v1 + same marker | corpus `Q017.expected_behavior` |
| Q018 | data | `exact_set`, `semantic` | independent control.q018.v1 + same marker | corpus `Q018.expected_behavior` |
| Q019 | data | `exact_set`, `semantic` | independent control.q019.v1 + same marker | corpus `Q019.expected_behavior` |
| Q020 | data | `exact_set`, `semantic` | independent control.q020.v1 + same marker | corpus `Q020.expected_behavior` |
| Q021 | data | `exact_set`, `semantic` | independent control.q021.v1 + same marker | corpus `Q021.expected_behavior` |
| Q022 | data | `exact_order`, `semantic` | independent control.q022.v1 + same marker | corpus `Q022.expected_behavior` |
| Q023 | data | `exact_order`, `semantic` | independent control.q023.v1 + same marker | corpus `Q023.expected_behavior` |
| Q024 | data | `exact_set`, `semantic` | independent control.q024.v1 + same marker | corpus `Q024.expected_behavior` |
| Q025 | data | `exact_order`, `semantic` | independent control.q025.v1 + same marker | corpus `Q025.expected_behavior` |
| Q026 | data | `exact_set`, `semantic` | independent control.q026.v1 + same marker | corpus `Q026.expected_behavior` |
| Q027 | data | `exact_set`, `semantic` | independent control.q027.v1 + same marker | corpus `Q027.expected_behavior` |
| Q028 | data | `exact_scalar`, `semantic` | independent control.q028.v1 + same marker | corpus `Q028.expected_behavior` |
| Q029 | data | `clarification`, `exact_set`, `semantic` | independent control.q029.v1 + same marker | corpus `Q029.expected_behavior` |
| Q030 | data | `exact_order`, `semantic` | independent control.q030.v1 + same marker | corpus `Q030.expected_behavior` |
| Q031 | data | `exact_set`, `exact_scalar`, `semantic` | independent control.q031.v1 + same marker | corpus list plus distinct total from `Q031.expected_behavior` |
| Q032 | data | `exact_order`, `semantic` | independent control.q032.v1 + same marker | corpus `Q032.expected_behavior` |
| Q033 | data | `exact_scalar`, `semantic` | independent control.q033.v1 + same marker | corpus `Q033.expected_behavior` |
| Q034 | data | `exact_scalar`, `semantic` | independent control.q034.v1 + same marker | corpus `Q034.expected_behavior` |
| Q035 | data | `exact_order`, `semantic` | independent control.q035.v1 + same marker | corpus `Q035.expected_behavior` |
| Q036 | data | `exact_scalar`, `semantic` | independent control.q036.v1 + same marker | corpus `Q036.expected_behavior` |
| Q037 | follow_up | `exact_set`, `semantic` | independent control.q037.v1 + same marker | corpus `Q037.expected_behavior` |
| Q038 | data | `exact_order`, `semantic` | independent control.q038.v1 + same marker | corpus `Q038.expected_behavior` |
| Q039 | data | `exact_order`, `semantic` | independent control.q039.v1 + same marker | corpus `Q039.expected_behavior` |
| Q040 | data | `exact_order`, `semantic` | independent control.q040.v1 + same marker | corpus `Q040.expected_behavior` |
| Q041 | data | `exact_order`, `semantic` | independent control.q041.v1 + same marker | corpus `Q041.expected_behavior` |
| Q042 | follow_up | `exact_set`, `semantic` | independent control.q042.v1 + same marker | corpus `Q042.expected_behavior` |
| Q043 | data | `exact_order`, `semantic` | independent control.q043.v1 + same marker | corpus `Q043.expected_behavior` |
| Q044 | data | `exact_scalar`, `semantic` | independent control.q044.v1 + same marker | corpus `Q044.expected_behavior` |
| Q045 | data | `exact_order`, `semantic` | independent control.q045.v1 + same marker | corpus `Q045.expected_behavior` |
| Q046 | data | `exact_scalar`, `semantic` | independent control.q046.v1 + same marker | corpus `Q046.expected_behavior` |
| Q047 | data | `exact_scalar`, `semantic` | independent control.q047.v1 + same marker | corpus `Q047.expected_behavior` |
| Q048 | data | `exact_order`, `semantic` | independent control.q048.v1 + same marker | corpus `Q048.expected_behavior` |
| Q049 | data | `exact_set`, `semantic` | independent control.q049.v1 + same marker | corpus `Q049.expected_behavior` |
| Q050 | data | `exact_order`, `semantic` | independent control.q050.v1 + same marker | corpus `Q050.expected_behavior` |
| Q051 | data | `exact_set`, `semantic` | independent control.q051.v1 + same marker | corpus `Q051.expected_behavior` |
| Q052 | data | `exact_set`, `semantic` | independent control.q052.v1 + same marker | corpus `Q052.expected_behavior` |
| Q053 | data | `exact_set`, `semantic` | independent control.q053.v1 + same marker | corpus `Q053.expected_behavior` |
| Q054 | data | `exact_set`, `semantic` | independent control.q054.v1 + same marker | corpus `Q054.expected_behavior` |
| Q055 | data | `exact_scalar`, `semantic` | independent control.q055.v1 + same marker | corpus `Q055.expected_behavior` |
| Q056 | data | `clarification`, `exact_order`, `semantic` | independent control.q056.v1 + same marker | corpus `Q056.expected_behavior` |
| Q057 | data | `clarification`, `exact_order`, `semantic` | independent control.q057.v1 + same marker | corpus `Q057.expected_behavior` |
| Q058 | data | `exact_set`, `semantic` | independent control.q058.v1 + same marker | corpus `Q058.expected_behavior` |
| Q059 | data | `exact_scalar`, `semantic` | independent control.q059.v1 + same marker | corpus `Q059.expected_behavior` |
| Q060 | data | `exact_order`, `semantic` | independent control.q060.v1 + same marker | corpus `Q060.expected_behavior` |
| Q061 | data | `exact_order`, `semantic` | independent control.q061.v1 + same marker | corpus `Q061.expected_behavior` |
| Q062 | data | `exact_scalar`, `semantic` | independent control.q062.v1 + same marker | corpus `Q062.expected_behavior` |
| Q063 | follow_up | `exact_scalar`, `semantic` | independent control.q063.v1 + same marker | corpus `Q063.expected_behavior` |
| Q064 | follow_up | `exact_set`, `semantic` | independent control.q064.v1 + same marker | corpus `Q064.expected_behavior` |
| Q065 | data | `clarification`, `exact_set`, `semantic` | independent control.q065.v1 + same marker | corpus `Q065.expected_behavior` |
| Q066 | data | `exact_scalar`, `semantic` | independent control.q066.v1 + same marker | corpus `Q066.expected_behavior` |
| Q067 | data | `exact_order`, `semantic` | independent control.q067.v1 + same marker | corpus `Q067.expected_behavior` |
| Q068 | data | `exact_set`, `semantic` | independent control.q068.v1 + same marker | corpus `Q068.expected_behavior` |
| Q069 | data | `exact_scalar`, `semantic` | independent control.q069.v1 + same marker | corpus `Q069.expected_behavior` |
| Q070 | data | `exact_order`, `semantic` | independent control.q070.v1 + same marker | corpus `Q070.expected_behavior` |
| Q071 | data | `exact_scalar`, `semantic` | independent control.q071.v1 + same marker | corpus `Q071.expected_behavior` |
| Q072 | data | `exact_order`, `semantic` | independent control.q072.v1 + same marker | corpus `Q072.expected_behavior` |
| Q073 | follow_up | `exact_set`, `semantic` | independent control.q073.v1 + same marker | corpus `Q073.expected_behavior` |
| Q074 | data | `exact_order`, `semantic` | independent control.q074.v1 + same marker | corpus `Q074.expected_behavior` |
| Q075 | data | `exact_scalar`, `semantic` | independent control.q075.v1 + same marker | corpus `Q075.expected_behavior` |
| Q076 | data | `exact_order`, `semantic` | independent control.q076.v1 + same marker | corpus `Q076.expected_behavior` |
| Q077 | data | `clarification`, `exact_set`, `semantic` | independent control.q077.v1 + same marker | corpus `Q077.expected_behavior` |
| Q078 | data | `exact_scalar`, `semantic` | independent control.q078.v1 + same marker | corpus `Q078.expected_behavior` |
| Q079 | data | `exact_set`, `semantic` | independent control.q079.v1 + same marker | corpus `Q079.expected_behavior` |
| Q080 | data | `exact_order`, `semantic` | independent control.q080.v1 + same marker | corpus `Q080.expected_behavior` |
| Q081 | data | `clarification`, `exact_set`, `semantic` | independent control.q081.v1 + same marker | corpus `Q081.expected_behavior` |
| Q082 | follow_up | `exact_set`, `semantic` | independent control.q082.v1 + same marker | corpus `Q082.expected_behavior` |
| Q083 | data | `exact_set`, `semantic` | independent control.q083.v1 + same marker | corpus `Q083.expected_behavior` |
| Q084 | data | `exact_scalar`, `semantic` | independent control.q084.v1 + same marker | corpus `Q084.expected_behavior` |
| Q085 | data | `exact_scalar`, `semantic` | independent control.q085.v1 + same marker | corpus `Q085.expected_behavior` |
| Q086 | data | `clarification`, `exact_order`, `semantic` | independent control.q086.v1 + same marker | corpus `Q086.expected_behavior` |
| Q087 | data | `exact_scalar`, `semantic` | independent control.q087.v1 + same marker | corpus `Q087.expected_behavior` |
| Q088 | data | `exact_order`, `semantic` | independent control.q088.v1 + same marker | corpus `Q088.expected_behavior` |
| Q089 | data | `clarification`, `exact_set`, `semantic` | independent control.q089.v1 + same marker | corpus `Q089.expected_behavior` |
| Q090 | data | `clarification`, `exact_order`, `semantic` | independent control.q090.v1 + same marker | corpus `Q090.expected_behavior` |
| Q091 | data | `exact_set`, `semantic` | independent control.q091.v1 + same marker | corpus `Q091.expected_behavior` |
| Q092 | follow_up | `exact_set`, `semantic` | independent control.q092.v1 + same marker | corpus `Q092.expected_behavior` |
| Q093 | follow_up | `exact_set`, `semantic` | independent control.q093.v1 + same marker | corpus `Q093.expected_behavior` |
| Q094 | data | `exact_order`, `semantic` | independent control.q094.v1 + same marker | corpus `Q094.expected_behavior` |
| Q095 | follow_up | `exact_scalar`, `semantic` | independent control.q095.v1 + same marker | corpus `Q095.expected_behavior` |
| Q096 | data | `exact_order`, `semantic` | independent control.q096.v1 + same marker | corpus `Q096.expected_behavior` |
| Q097 | follow_up | `exact_set`, `semantic` | independent control.q097.v1 + same marker | corpus `Q097.expected_behavior` |
| Q098 | negative | `clarification` | closed planner decision + no forbidden external call | corpus `Q098.expected_behavior` |
| Q099 | negative | `clarification` | closed planner decision + no forbidden external call | corpus `Q099.expected_behavior` |
| Q100 | negative | `refusal` | closed planner decision + no forbidden external call | corpus `Q100.expected_behavior` |
| Q101 | negative | `refusal` | closed planner decision + no forbidden external call | corpus `Q101.expected_behavior` |
| Q102 | negative | `exact_set`, `semantic` | independent control.q102.v1 + same marker | corpus `Q102.expected_behavior` |
| Q103 | negative | `exact_set`, `semantic` | independent control.q103.v1 + same marker | corpus `Q103.expected_behavior` |
| Q104 | negative | `refusal` | closed planner decision + no forbidden external call | corpus `Q104.expected_behavior` |
| Q105 | negative | `dependency_error` | injected dependency state + trace | corpus `Q105.expected_behavior` |
| Q106 | negative | `dependency_error` | injected dependency state + trace | corpus `Q106.expected_behavior` |
| Q107 | interaction | `semantic` | active catalog summary + read-only configuration | corpus `Q107.expected_behavior` |
| Q108 | follow_up | `exact_set`, `semantic` | independent control.q108.v1 + same marker | corpus `Q108.expected_behavior` |
| Q109 | data | `exact_set`, `semantic` | independent control.q109.v1 + same marker | corpus `Q109.expected_behavior` |
| Q110 | data | `exact_order`, `semantic` | independent control.q110.v1 + same marker | corpus `Q110.expected_behavior` |
| Q111 | data | `exact_set`, `semantic` | independent control.q111.v1 + same marker | corpus `Q111.expected_behavior` |
| Q112 | data | `exact_scalar`, `semantic` | independent control.q112.v1 + same marker | corpus `Q112.expected_behavior` |
| Q113 | data | `exact_set`, `semantic` | independent control.q113.v1 + same marker | corpus `Q113.expected_behavior` |
| Q114 | data | `exact_order`, `semantic` | independent control.q114.v1 + same marker | corpus `Q114.expected_behavior` |
| Q115 | data | `exact_scalar`, `semantic` | independent control.q115.v1 + same marker | corpus `Q115.expected_behavior` |
| Q116 | data | `exact_scalar`, `semantic` | independent control.q116.v1 + same marker | corpus `Q116.expected_behavior` |

## 4. Оценка результата

Для `exact_set` строки canonicalize по типизированной identity; UI order не
учитывается. Для `exact_order` дополнительно сравниваются sort fact,
направление и tie policy. `semantic` всегда проверяет outcome, entity,
measure, period/moment, unit/currency, filters, completeness и отсутствие
fabricated facts. Documentation pass требует citation, даже если prose
семантически похож.

Data oracle values отсутствуют до live capture. Их нельзя заполнять из shipped
skill fixture или из ответа приложения.
