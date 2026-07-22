# Требования Slice 4: универсальные операторы и композиция

## 1. Цель

Slice 4 превращает подтвержденные отношения из atomic skills в полный ответ с
помощью общего allowlist операторов. Он не добавляет классификаторы вопросов и
не генерирует запросы 1С в runtime.

Нормативный exit-набор:

`Q016,Q023-Q024,Q032-Q040,Q043-Q050,Q053-Q055,Q061,Q067,Q071-Q077,Q084-Q090`.

## 2. Продуктовые инварианты

- `S4-INV-001`. Число строк, distinct документов, distinct товаров и сумма
  количества единиц являются разными meanings.
- `S4-INV-002`. Сумма документа, выручка, себестоимость, валовая прибыль,
  дебиторская и кредиторская задолженность не взаимозаменяемы.
- `S4-INV-003`. Остаток относится к моменту; оборот и расход - к half-open
  периоду `[from,to)`.
- `S4-INV-004`. Money сравнивается и агрегируется только в одной валюте;
  quantity - в одной единице. Неявной конвертации в MVP нет.
- `S4-INV-005`. Null, typed zero, empty result, partial result и dependency
  error приводят к разным outcomes.
- `S4-INV-006`. Ни один operator не выполняет hidden pagination drain.
- `S4-INV-007`. Новый semantic/physical type работает через импорт контракта
  без изменения application source.
- `S4-INV-008`. Intermediate resolver/list fact не закрывает final measure.

## 3. Матрица

| Механизм | Основные Q-ID | Требуемый факт | Готовность |
| --- | --- | --- | --- |
| count distinct | Q016,Q032,Q044,Q061 | integer + exact distinct key | page count подписан как visible; total только complete |
| aggregate | Q033-Q034,Q038-Q039,Q045,Q053,Q071,Q075,Q084-Q085 | typed measure, unit, time, denominator для average | функция и grain доказаны, mixed units отклонены |
| filter | Q074,Q089; regression Q026-Q027,Q058 | исходная relation с теми же facts | zero/sign/null не смешаны, scope сохранен |
| rank | Q035,Q038-Q039,Q043,Q048,Q050,Q067,Q072,Q074,Q076-Q077,Q090 | exact identity + comparable measure | полный universe, direction/limit/ties доказаны |
| equijoin | Q024,Q040,Q076-Q077 | typed coordinates и projections | presentation join и undeclared fan-out отклонены |
| calculate | Q023 | declared semantic rule + Decimal operands | unit algebra и rounding policy доказаны |
| grouped/timeline | Q032,Q038-Q040,Q045,Q061,Q086,Q088-Q090 | dimensions + measure + stable order | exact grain, no synthetic missing buckets |

## 4. Обязательные уточнения

- Что считать: строки, документы, distinct сущности или количество единиц,
  если это не однозначно из формулировки.
- Показатель, направление, лимит или tie policy rank, если они не определены.
- Конкретный объект при entity ambiguity.
- Период для Q086, Q089 и Q090.
- В Q077: сумма документа или фактическая задолженность.
- При разных валютах/единицах: выбрать разрез либо сообщить
  несопоставимость; молчаливый пересчет запрещен.

## 5. Первый вертикальный набор

Первый инкремент обязан пройти полные планы:

1. `Q032`: distinct count + calendar grouping + timeline.
2. `Q033`: sum money по документам.
3. `Q034`: average money с denominator.
4. `Q038`: quantity group -> generic rank.
5. `Q089`: clarification периода -> negative filter.
6. `Q023`: declared VAT calculation.
7. `Q024`: typed price comparison/join.

Fixtures доказывают механизмы и отрицательные границы. Числовая бизнес-
семантика skills подтверждается только live MCP и независимыми control queries
на одном marker.

## 6. Явные ограничения alpha.5

- rank сейчас принимает только direct typed resolver producer;
- count сейчас ошибочно запрещает безопасный visible-page count;
- aggregate/filter/join/calculate runtime отсутствуют;
- catalog не содержит всех skills Slice 4;
- customer metric producer отсутствует и не подменяется суммой документа.

Эти ограничения являются входной точкой Slice 4, а не допустимым финальным
поведением.

