# ADR-0004: Типизированная реляционная композиция

- Статус: accepted
- Дата: 2026-07-21
- Область: Slice 4, механизмы M06-M09

## Контекст

В `0.1.0-alpha.5` модели плана уже называют `count`, `aggregate`, `rank`,
`filter`, `join` и `calculate`, но runtime исполняет только первые два базовых
механизма и специализированный `rank`. Простое включение остальных классов
опасно: grouped aggregate теряет измерения, join не объявляет проекции и
кратность, calculate может свободно назвать результат долгом или прибылью, а
nullable-значение сейчас не сохраняется как наблюдаемый факт.

Slice 4 должен реализовать небольшое число общих механизмов, а не ветви по
Q-ID, объектам УТ или префиксам semantic type. При этом ядро обязано различать
строки, документы и товары; сумму документа, выручку, прибыль и долг; момент и
период; валюту и единицу количества.

## Решение

### 1. RelationShape

Каждый шаг плана получает вычисляемый до MCP закрытый `RelationShape`:

```text
FieldShape:
  fact_id, semantic_type, value_type, role, nullable
  unit_expression, time_semantics

RelationShape:
  fields
  row_identity_fact_ids
  candidate_key_fact_sets
  grain_fact_ids
  cardinality
  collection_scope: visible_page | complete_set
```

Форма data-skill выводится из его output contract. Форма operator-step
выводится только из формы входов и закрытой сигнатуры оператора. Runtime не
угадывает тип по имени fact, таблице 1С или содержимому значения.

### 2. Версии plan и evidence

- frozen planner contract `1.0.0` продолжает читаться без изменения;
- новые реляционные operator plans записываются как planner `1.1.0`;
- ранее неисполнявшиеся `aggregate/filter/join/calculate` из сохраненного plan
  `1.0.0` не переинтерпретируются и завершаются fail-closed с предложением
  повторить вопрос;
- Evidence `1.0/1.1` остается читаемым byte-for-byte;
- новые operator derivations записываются в Evidence `1.2.0`.

Plan `1.1` добавляет к requirement точное обязательство коллекции и meaning:

```text
collection_obligation: fact | visible_page | complete_set
meaning: direct | count | sum | average | minimum | maximum | calculation
```

Meaning не заменяет semantic type. Он доказывает преобразование одного и того
же типизированного показателя и не разрешает назвать сумму документа долгом.

### 3. Числа

Новые numeric facts нормализуются в `Decimal` из лексического представления
MCP-значения. Evidence `1.2` хранит каноническую десятичную строку без
экспоненты и отрицательного нуля. Бинарный `float` не участвует в арифметике.

Округление запрещено по умолчанию. Оно допустимо только через объявленную
numeric policy с mode, scale и rounding. Проценты хранятся как доля: `0.20`
означает 20 процентов. Старые numeric facts Evidence `1.0/1.1` читаются как
legacy и не переписываются.

### 4. Null

Evidence `1.2` различает `presence=value` и `presence=null`. Null observation
сохраняет fact ID, row ID, declared type и query-column provenance, но не
получает числового значения. Только такой факт может участвовать в `is_null`.
Отсутствующий fact, пустой набор и null не взаимозаменяемы.

### 5. Сигнатуры операторов

`count`:

- `distinct_by_fact_ids` имеет длину 1..20;
- может считать текущую видимую страницу, сохраняя `visible_page`;
- total requirement удовлетворяется только результатом `complete_set`;
- никогда не листает вход скрыто.

`filter`:

- сохраняет строки, исходные facts, provenance и scope;
- sign predicates разрешены только для numeric facts;
- `is_null/is_not_null` используют explicit presence;
- operand predicates требуют exact value/unit/time compatibility.

`aggregate` и `group`:

- принимают только полный, непрерванный набор;
- group dimensions явно проецируются в результат;
- money/quantity агрегируются только внутри одной resolved unit на группу;
- average публикует denominator и его distinct identity definition;
- empty `average/minimum/maximum` не превращается в ноль;
- отсутствующие временные buckets не синтезируются.

`rank`:

- работает над любой полной relation, а не только resolver;
- existing resolver-selection proof сохраняется как отдельный частный случай;
- stable-first требует доказанный полный tie key;
- include-all сохраняет всю boundary tie.

`join`:

- ключи сравниваются по exact field shapes;
- entity identity равна `(semantic_type, physical_type, UUID)`;
- план объявляет multiplicity, проекции и identity результата;
- runtime отвергает недекларированный fan-out и коллизии fact ID;
- два MCP-входа должны иметь один доказанный read epoch.

`calculate`:

- использует только catalog-declared semantic rule;
- свободное `result_semantic_type` из planner запрещено;
- add/subtract требуют одинаковые resolved units;
- divide/multiply не создают compound unit без отдельного правила;
- деление на ноль отклоняет весь operator-step.

### 6. Read epoch

Одинаковый catalog/database marker в envelope сам по себе не доказывает
согласованность двух последовательных MCP-запросов. Multi-input join разрешен,
только если adapter предоставляет общий snapshot token либо проверенный
before/after marker одного acceptance-observable-state. До появления такого
proof production join двух независимых MCP calls остается fail-closed.

### 7. Evidence derivation

Evidence `1.2` содержит plan digest, relation-shape digest и
`OperatorDerivation`: algorithm version, input step IDs, input/output relation
digests, canonical source-set digest, group/tie keys, member counts и parent
fact IDs. Cross-artifact validator повторяет derivation и сравнивает результат;
строка `operation_ref=operator:*` доказательством не является.

### 8. Rendering

Grouped-table и timeline являются только projections validated relation. Они
не вычисляют показатели. Timeline требует одну calendar dimension, ascending
order и сопоставимые units внутри series. Пропуски не заполняются нулями.

## Последствия

Положительные:

- core остается независимым от конфигурации и формулировок вопросов;
- ошибка planner не может переименовать выручку в долг;
- page count, total, zero, null и empty имеют разные доказательства;
- арифметика воспроизводима и проверяема offline.

Отрицательные:

- Slice 4 требует версии plan/evidence и более строгого тестового harness;
- некоторые join-сценарии останутся заблокированы до read-epoch proof;
- старые неисполнявшиеся operator plans нельзя продолжить после обновления.

## Порядок поставки

1. RelationShape, planner 1.1 и красные contract tests.
2. Evidence 1.2, decimal/null и derivation verifier.
3. Scope-aware count и filter.
4. Aggregate/group и generic rank.
5. Typed join, calculation rules и calculate.
6. Grouped/timeline renderer и business E2E.

