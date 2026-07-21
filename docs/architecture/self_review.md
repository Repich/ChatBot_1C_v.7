# Саморевью архитектуры

Статус: все обязательные пункты `docs/lessons_from_v5.md`, FR/NFR и 116
сценариев просмотрены. «Покрыто» означает наличие конкретного архитектурного
механизма и тестового пути, а не уже реализованный продукт.

## 1. Уроки v5

| Урок | Ответ архитектуры | Статус |
| --- | --- | --- |
| 1.1 classifiers под слова/объекты разрастаются | Typed intent/facts/slots, declarative aliases и общий coverage graph | Покрыто |
| 1.2 нельзя подгонять к ближайшему skill | Similarity только shortlist; required-fact proof решает applicability | Покрыто |
| 1.3 parameter не identity skill | Skill ID lint и typed parameters, включая characteristic/series/purpose | Покрыто |
| 1.4 технический объект не бизнес-сущность | `semantic_type` поверх exact `_objectRef`, type-safe bindings | Покрыто |
| 1.5 неоднозначный показатель требует вопроса | Missing/ambiguous fact requirement -> один `clarify` | Покрыто |
| 2.1 LLM выдумывает metadata | Query text отсутствует в planner schema/prompt; только fixed template | Покрыто |
| 2.2 metadata validation ошибалась | Import assertions + lexer/shallow parser + live query/schema contract test, не heuristic denylist | Покрыто |
| 2.3 частные fixes по полю | Mechanism/contract tests, запрет Q-ID/object branches в app code | Покрыто |
| 2.4 синтаксис не означает правильный смысл | Semantic output facts/cardinality/unit/time + independent baseline | Покрыто |
| 2.5 query error становился empty | Разные normalized outcomes | Покрыто |
| 2.6 повтор partial зацикливался | DAG без dynamic retry/replan; максимум один shortlist expansion | Покрыто |
| 2.7 особенности языка запросов | Fixed query-package tests на датах/LIKE/virtual tables/refs/tabular sections/linked temporary tables и invariant literals | Покрыто |
| 3.1 wrappers разбирались неодинаково | Две явно принятые MCP envelope forms; иное `contract_error` | Покрыто |
| 3.2 intermediate выдавался final | Final required-fact coverage по type/cardinality/unit/time после всех required steps | Покрыто |
| 3.3 sufficiency по словам колонок | Exact column bindings + exact provides/output + semantic contract, без keyword lookup | Покрыто |
| 3.4 follow-up терял объект | Context ledger с opaque handle и exact ref identity | Покрыто |
| 3.5 renderer делал вывод без данных | Authoritative renderer manifest; LLM claims с evidence refs | Покрыто |
| 3.6 empty aggregate смешивался с zero | `success_empty`, `zero_aggregate`, null semantics | Покрыто |
| 4.1 повтор менял skill_gap | Catalog/context pinned до планирования, нет runtime side effects | Покрыто |
| 4.2 узкие skills | Atomic business operation + parameter/value lint | Покрыто |
| 4.3 skill без executable contract | Closed operation/execution oneOf, typed invariants, exact bindings, tests и output contract | Покрыто |
| 4.4 reuse по text similarity | Multi-signal shortlist + fact/type/unit/time proof | Покрыто |
| 4.5 сложный draft lifecycle | Только accepted revision или rejected import | Покрыто |
| 4.6 непонятная карточка | Public projection purpose/params/output/compatibility/examples | Покрыто |
| 4.7 skill не появлялся | Transactional revision + atomic snapshot swap + watcher recovery | Покрыто |
| 5.1 передавалось последнее сообщение | Structured confirmed context и active filters | Покрыто |
| 5.2 clarification терял исходную задачу | Persisted pending clarification bound to original turn | Покрыто |
| 5.3 UI показывал internals | IDs/query/raw только details/diagnostics | Покрыто |
| 5.4 admin вытеснял chat/без feedback | Chat-first layout, separate admin view, visible result/status | Покрыто |
| 5.5 history/times не сохранялись | SQLite messages/timestamps/reload | Покрыто |
| 6.1 большие prompts/timeouts | Bounded skill cards/context, max shortlist, stage budgets | Покрыто |
| 6.2 сбои зависимостей смешивались | Distinct LLM/MCP/query/contract outcomes | Покрыто |
| 6.3 diagnostics были неполны | Prompt/query/params/raw response в bundle с hashes | Покрыто |
| 6.4 обновление оставляло процессы | Один process, no restart hot reload, startup recovery | Покрыто для scope MVP |
| 6.5 env требовал скрытого restart | Startup-validated config; runtime changes явно требуют process restart, catalog не требует | Покрыто |

## 2. Десять обязательных архитектурных проверок

1. Полнота всех required и final facts доказывается сравнением
   `FactRequirement` с declared output contracts по semantic type, cardinality,
   identity, unit и time до выполнения и с fact instances после выполнения.
2. Назначение skill находится в ID/display/provides/contract, значения - только
   в typed parameters/bindings.
3. Идентичность между steps сохраняется exact `_objectRef`; presentation не
   участвует в равенстве.
4. Empty/zero/error/partial имеют разные enum outcomes; external error
   локализован stage/dependency/error ID и не повреждает session.
5. Query package проверяется lexer/shallow parser: linked temporary tables
   разрешены только как замкнутый граф к одному final SELECT; business values
   параметризованы, допустимые literals имеют typed invariant declarations.
6. Смысл результата проверяется semantic types, cardinality, unit/time и
   independent oracle, не синтаксисом/alias words.
7. Import сверяет exact provides/output, closed dependency lock и все digests,
   затем создает immutable revision одной transaction и atomic swap.
8. Turn pin-ит snapshot; import не меняет его и не запускается как side effect.
9. Evidence disagreement сохраняет отдельные facts/provenance и никогда не
   разрешается молча по rank, порядку или confidence.
10. LLM ограничена plan/grounded wording; execution/coverage/evidence/core
    полностью детерминированы.

## 3. Саморевью 116 сценариев

| ID | Проверяемый архитектурный путь | Результат ревью |
| --- | --- | --- |
| Q001 | Doc term role + built-in citation | Путь есть |
| Q002 | Два semantic terms, не смешивать partner/counterparty | Путь есть |
| Q003 | Procedure chunks/order/prerequisites + citation | Путь есть |
| Q004 | Procedure retrieval для supplier order | Путь есть |
| Q005 | Procedure retrieval для transfer | Путь есть |
| Q006 | Procedure + запрет неподтвержденных steps | Путь есть |
| Q007 | Error causes/actions as cited alternatives | Путь есть |
| Q008 | Navigation/procedure role + release source | Путь есть |
| Q009 | Ordered procedure renderer | Путь есть |
| Q010 | Status meaning distinct from execution fact | Путь есть |
| Q011 | Item resolver exact article, empty state | Путь есть |
| Q012 | Contains parameter, list/full pagination | Путь есть |
| Q013 | Item ambiguity then barcode detail | Путь есть |
| Q014 | Unit fact, not stock quantity | Путь есть |
| Q015 | Empty article predicate + count/list limit | Путь есть |
| Q016 | Group resolver + distinct item count/nesting rule | Путь есть |
| Q017 | Customer semantic type + details | Путь есть |
| Q018 | Supplier role hard fact + substring | Путь есть |
| Q019 | Warehouse type fact and evidence | Путь есть |
| Q020 | Organization ref retained, cash desk types | Путь есть |
| Q021 | Item + price type refs -> current price/unit/currency/date | Путь есть |
| Q022 | Last purchase rank -> price/document/date | Путь есть |
| Q023 | VAT rate evidence + deterministic calculation | Путь есть |
| Q024 | Same item/date/unit/currency compatibility | Путь есть |
| Q025 | Period normalization + price timeline | Путь есть |
| Q026 | Missing price distinct from zero | Путь есть |
| Q027 | Exact zero filter distinct from no row | Путь есть |
| Q028 | Exact price type, zero remains value | Путь есть |
| Q029 | Missing price type -> clarification then group prices | Путь есть |
| Q030 | Point-in-time current-price contract | Путь есть |
| Q031 | Calendar period + list count/page message | Путь есть |
| Q032 | Count documents grouped month | Путь есть |
| Q033 | Sum documents + currency/inclusion rule | Путь есть |
| Q034 | Average + denominator document count | Путь есть |
| Q035 | Comparable money + rank maximum | Путь есть |
| Q036 | Exact order ref/status facts | Путь есть |
| Q037 | Context order ref -> lines | Путь есть |
| Q038 | Quantity measure/group/rank, not rows/revenue | Путь есть |
| Q039 | Revenue measure/group/rank | Путь есть |
| Q040 | Join year/revenue/cost/profit facts | Путь есть |
| Q041 | Receipt date rank + header facts | Путь есть |
| Q042 | Context receipt ref -> all lines | Путь есть |
| Q043 | Earliest receipt ref then lines, not arbitrary row | Путь есть |
| Q044 | Distinct receipt document count/year | Путь есть |
| Q045 | Purchase amount grouped supplier/unit | Путь есть |
| Q046 | Explicit incomplete status/execution fact | Путь есть |
| Q047 | Expected quantity distinct from received | Путь есть |
| Q048 | Item ref -> receipt lines -> latest document | Путь есть |
| Q049 | Return documents within half-open year | Путь есть |
| Q050 | Comparable purchase money rank | Путь есть |
| Q051 | All matching items + current stock moment | Путь есть |
| Q052 | Item/warehouse row identity preserved | Путь есть |
| Q053 | Item+warehouse refs, sum and per-item detail | Путь есть |
| Q054 | Retail warehouse fact + pagination | Путь есть |
| Q055 | Available/reserved separate facts, same moment | Путь есть |
| Q056 | Ambiguous retail warehouse -> one clarification + rank | Путь есть |
| Q057 | Missing comparable measure/unit -> clarification | Путь есть |
| Q058 | Assortment scope + zero/negative filter criterion | Путь есть; skill contract must expose assortment semantics |
| Q059 | End-of-date moment normalization | Путь есть |
| Q060 | Relative period + movement in/out/doc refs | Путь есть |
| Q061 | Relative period + distinct transfer count/day | Путь есть |
| Q062 | Transfer exact number/ref or empty | Путь есть |
| Q063 | Context transfer ref -> status | Путь есть |
| Q064 | Same context transfer ref -> lines | Путь есть |
| Q065 | Warehouse as sender + period clarification | Путь есть |
| Q066 | Unaccepted status fact and basis | Путь есть |
| Q067 | Last inventory ref + accounting/actual/difference | Путь есть |
| Q068 | Current month + consumption lines/docs | Путь есть |
| Q069 | Order ref -> delivery status/date or confirmed empty | Путь есть |
| Q070 | Planned date past + completion false filter | Путь есть |
| Q071 | AR measure, not shipment sums | Путь есть |
| Q072 | Group by customer ref then max AR | Путь есть |
| Q073 | Context customer ref -> details | Путь есть |
| Q074 | Positive filter then minimum rank | Путь есть |
| Q075 | AP measure, not receipt sums | Путь есть |
| Q076 | Last receipt ref -> supplier + linked AP; document sum separate | Путь есть |
| Q077 | Ambiguous amount/debt -> clarify, same shipment ref | Путь есть |
| Q078 | Due date + overdue proof or explicit limitation | Путь есть |
| Q079 | Customer remains parent, objects of settlement details | Путь есть |
| Q080 | Customer universe anti-join sales period | Путь есть |
| Q081 | Organization ambiguity -> cash balances by currency | Путь есть |
| Q082 | Retain organization/moment, split desk types | Путь есть |
| Q083 | Bank account/organization/currency row identity | Путь есть |
| Q084 | Cash-only receipts sum/year/currency | Путь есть |
| Q085 | Cash-only expenses sum/year/currency | Путь есть |
| Q086 | Missing period clarify + monthly in/out/net | Путь есть |
| Q087 | Revenue/cost/profit named separately | Путь есть |
| Q088 | Comparable yearly trend | Путь есть |
| Q089 | Period clarification + negative profit filter | Путь есть |
| Q090 | Period clarification + rank exactly five | Путь есть |
| Q091 | Item+retail warehouse+moment context exports | Путь есть |
| Q092 | Replace only item slot, preserve warehouse/moment | Путь есть |
| Q093 | Change renderer/detail only, preserve filters | Путь есть |
| Q094 | Last receipt exact ref context export | Путь есть |
| Q095 | Reuse receipt ref, no independent search | Путь есть |
| Q096 | Max AR customer exact ref export | Путь есть |
| Q097 | Reuse customer, reject contract/settlement object | Путь есть |
| Q098 | Ambiguous turnover fact/metric/period clarification | Путь есть |
| Q099 | Warehouse/scope clarification, no unbounded silent query | Путь есть |
| Q100 | Out-of-scope refusal | Путь есть |
| Q101 | Read-only refusal; no write port exists | Путь есть |
| Q102 | Successful empty item search with echoed article | Путь есть |
| Q103 | Successful empty shipment query, not error | Путь есть |
| Q104 | Out-of-scope payroll refusal | Путь есть |
| Q105 | MCP transport unavailable distinct outcome/trace ID | Путь есть |
| Q106 | DeepSeek timeout distinct outcome/session survives | Путь есть |
| Q107 | FR-010 product-meta answer from active catalog summary | Путь есть |
| Q108 | Common typed entity detail without object-specific branch | Путь есть |
| Q109 | Customer sales history by confirmed customer ref | Путь есть |
| Q110 | Supplier rank on declared comparable purchase measure | Путь есть |
| Q111 | Sales order header by exact document ref | Путь есть |
| Q112 | Sales profit with revenue/cost/unit/period evidence | Путь есть |
| Q113 | Sales return documents/facts in half-open period | Путь есть |
| Q114 | Shipment lines by exact shipment ref | Путь есть |
| Q115 | Stock consumption measure by typed dimensions/period | Путь есть |
| Q116 | Expected stock fact distinct from available/current balance | Путь есть |

## 4. FR/NFR и acceptance

Все `FR-001..055` и `NFR-001..014` имеют отдельную строку в
`requirements_mapping.md`. `AC-001..059` сопоставлены с release gates. Ни один
требуемый outcome не опирается на случайный текст модели.

## 5. Закрытые несостыковки и решения руководителя

### 5.1. Покрытие corpus

Acceptance corpus расширяется до `Q001-Q116`. `Q107` проверяет `FR-010`,
`Q108-Q116` дают end-to-end путь всем девяти ранее неиспользованным capability
IDs. Конкретная связь приведена в `requirements_mapping.md` и
`implementation_slices.md`; contract/composition tests остаются дополнительным,
а не заменяющим доказательством.

### 5.2. Сила database marker

Для MVP принят `acceptance_observable_state`: digest набора независимых
контрольных проекций/агрегатов acceptance suite и revisions/digests
configuration profile, active catalog и встроенного documentation index. Marker
не доказывает неизменность данных вне наблюдаемых проекций. MCP revision token и
полный snapshot не требуются.

### 5.3. Documentation-only v1

`FR-031/032` и `AC-030/031` являются активными v1 gates. Проверки включают
schema/import/retrieval hard-reject внешнего `source_kind` и fixture с двумя
расходящимися фрагментами встроенной справки. Typed evidence обязано связать
каждую позицию с fact/citation IDs, renderer показывает обе и не выбирает одну
по rank.

### 5.4. Read-only query packages и literals

ADR-0003 заменяет blanket-запрет `ПОМЕСТИТЬ` закрытым execution contract:
`single_select` либо связанный `linked_temp_batch` до 16 statements с одним
final result. Разрешение зависит от доказанного producer/consumer graph, а не от
наличия `;`. Blanket concrete-literal regex заменяется typed declarations
инвариантов; изменяемые business-instance values остаются только параметрами.
Это закрывает риск неполного покрытия сложных capabilities без ослабления
read-only boundary.

## 6. Неблокирующие внешние проверки следующего этапа

- Запустить MCP и закрепить реальные MCP Streamable HTTP envelope fixtures.
- Проверить DeepSeek `response_format=json_object` и planner schema, поскольку
  подтвержденный smoke проверял endpoint/model/choices/usage.
- Live-валидировать каждый query package и metadata assertion, включая минимум
  один `linked_temp_batch` с общим менеджером временных таблиц.
- Подтвердить SQLite FTS5 и package wheel на Windows.

Это implementation/integration work, а не нерешенные продуктовые defaults.
Открытых вопросов руководителю по разделу 5 не осталось.
