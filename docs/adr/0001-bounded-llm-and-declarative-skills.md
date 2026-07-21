# ADR-0001: Ограниченная LLM и декларативные навыки

- Статус: accepted
- Дата: 2026-07-21

## Контекст

MVP должен понимать естественные русские вопросы, комбинировать возможности и
отвечать по живым данным 1С. При этом опыт v5 показал выдуманные metadata/query,
семантически неверные, но синтаксически успешные запросы, потерю контекста и
выдачу intermediate/empty/error как полного ответа.

Product capability IDs описывают поведение, но не должны создавать по классу,
handler или query на каждый вопрос. Новый skill должен переноситься без release
приложения.

## Решение

1. DeepSeek используется только для интерпретации, выбора из bounded catalog,
   построения typed plan/clarification и grounded формулировки.
2. Planner output закрыт `planner-output.schema.json`; в нем нет query text,
   metadata names, MCP args или executable code.
3. 1C query хранится только как immutable parameterized template data-query
   skill. Runtime не синтезирует и не интерполирует query text.
4. Atomic skills имеют typed inputs, exact physical-to-semantic mappings,
   semantic output contract, compatibility, tests, provenance и checksum.
5. Composite question исполняется DAG skills и allowlist deterministic operators.
6. Similarity используется только для shortlist. Core доказывает required-fact
   coverage/type/cardinality/unit/time до выполнения.
7. После выполнения evidence gate повторно проверяет достаточность. Renderer
   показывает authoritative facts; LLM summary ссылается на evidence IDs.
8. Никакого runtime skill creation, self-learning или candidate/draft lifecycle.
9. V1 documentation boundary принимает только встроенную справку; расхождения
   между ее фрагментами представлены typed evidence и показываются со всеми
   citations, а не разрешаются rank или свободным выводом LLM.
10. Acceptance воспроизводимость означает одинаковые результаты на одном
    `acceptance_observable_state` marker; это не утверждение о snapshot всей ИБ.

## Последствия

Положительные:

- LLM не может изменить scope доступа к 1С;
- фактический результат воспроизводим на том же catalog/marker;
- query и skill тестируются до пользовательского runtime;
- общие operators/typed parameters покрывают множество capability IDs;
- ошибки локализуются до planner, skill, MCP, contract или answer stage.

Отрицательные:

- каталог и query templates требуют предварительного authoring/testing;
- неподдержанный required fact приводит к честному capability gap;
- изменения metadata требуют нового skill/package version;
- полную семантическую корректность query доказывают live tests и независимый
  oracle, а не одна JSON Schema.

## Отклоненные альтернативы

### Runtime query synthesis DeepSeek

Отклонено: модель может выдумать таблицу/поле, выбрать неверный регистр или
вернуть правдоподобные строки не того бизнес-смысла. Metadata lookup перед
синтезом не устраняет семантический риск.

### Выбор skill только по embedding/text similarity

Отклонено: похожая формулировка не гарантирует required facts, единицы и
детализацию. Similarity остается вспомогательным recall signal.

### Кодовая ветвь на каждый capability/question

Отклонено: разрастается на параметры и комбинации, повторяет дефекты частных
fixes. Capability реализуются небольшим набором contracts/operators/skills.

### Полностью детерминированный классификатор

Отклонено для natural language: породит словари по объектам/длинам/опечаткам и
плохо покрывает composition. Детерминированность остается после interpretation.

### Свободный LLM answer из raw MCP

Отклонено: модель может перепутать меру или добавить вывод. Она получает только
validated evidence manifest; authoritative values рендерит core.

## Проверка решения

- Static test подтверждает отсутствие query/code fields в planner schema.
- Prompt snapshot не содержит query templates.
- Adversarial planner output с query/unknown skill/forged context ref отклоняется.
- Coverage property tests находят missing facts/wrong units/cardinality.
- Q042/Q076/Q095 не завершаются intermediate result.
- Q102/Q103/Q105/Q106 дают разные outcomes.
- Q001-Q116 выполняются и оцениваются на одном marker по порогам AC-015/016;
  Q107-Q116 закрывают FR-010 и девять capability IDs, ранее не имевших
  end-to-end сценария.
- External `source_kind` отклоняется schema/retrieval tests; built-in
  disagreement fixture показывает все grounded позиции с citations.
