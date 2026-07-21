# Интеграционные контракты

## 1. Общие правила

- Все внешние вызовы получают `trace_id`, stage deadline и attempt number.
- Retry выполняет application policy, а не SDK по умолчанию; скрытые retries
  отключены.
- Секреты редактируются до записи любого event.
- Raw request/response хранится только как diagnostic payload.
- Transport error, provider error и business/query error имеют разные codes.
- Один общий request deadline - 90 секунд; stage не начинает retry, если
  оставшегося бюджета недостаточно.

## 2. MCP 1C

### 2.1. Транспорт и allowlist

Основной transport: MCP Streamable HTTP по `MCP_URL`, default
`http://127.0.0.1:6003/mcp`, optional `?channel=<id>`. Используется MCP client,
а не собственный JSON-RPC. На startup выполняются `initialize` и `tools/list`.

Application port публикует ровно два метода:

```text
ReadOnly1CPort.execute_query(ExecuteQueryRequest) -> ExecuteQueryEnvelope
ReadOnly1CPort.get_metadata(GetMetadataRequest) -> MetadataEnvelope
```

`execute_code`, object write/read tools, event log, restart/close session и все
прочие tools не представлены в port и не могут быть вызваны planner/executor.
Startup health требует наличие `execute_query` и `get_metadata`, но игнорирует
дополнительные tools сервера. Любая попытка вызвать имя вне allowlist завершается
локально `MCP_TOOL_FORBIDDEN` до сети.

### 2.2. execute_query request

Фактический внешний контракт подтвержден исходниками MCP toolkit:

```json
{
  "query": "<non-empty fixed 1C query text>",
  "params": {"Параметр": "typed value or _objectRef"},
  "limit": 21,
  "include_schema": true
}
```

Ограничения:

- `limit` integer `1..1000`;
- `include_schema` всегда `true` для runtime validation;
- query берется по `(skill_id, version, template_id, digest)` из pinned snapshot;
- parameters берутся только из declared bindings;
- `_objectRef` передается с true, UUID, object type и presentation;
- runtime query synthesis, concatenation и arbitrary MCP arguments запрещены.

Read-only обеспечивается одновременно четырьмя границами: tool allowlist,
отсутствием BSL port, immutable reviewed templates и static query lint. Запросы с
неразрешенными placeholders/несколькими независимыми statements отклоняются при
импорте. Фактический query language template проверяется live contract test.

### 2.3. execute_query response normalization

Внутренний envelope:

```json
{
  "success": true,
  "data": [{"Колонка": "..."}],
  "schema": {"columns": [{"name": "Колонка", "types": ["Строка"]}]},
  "count": 1,
  "truncated": false,
  "has_more": false
}
```

MCP adapter принимает только зафиксированные контрактными тестами формы:

1. `structuredContent` содержит объект envelope;
2. ровно один `content[type=text]` содержит JSON object envelope.

Несколько неоднозначных text blocks, non-JSON, отсутствие boolean `success` или
неожиданная вложенная оболочка дают `MCP_ENVELOPE_INVALID`, а не попытку угадать
данные. Поля `truncated/has_more` при отсутствии выводятся только по явно
проверенному сочетанию `count`, requested probe limit и skill pagination policy.

Классификация:

| Условие | Internal outcome |
| --- | --- |
| connect/DNS/protocol/read timeout | `mcp_unavailable` |
| валидный envelope `success=false` | `query_error` |
| `success=true`, 0 rows | `success_empty` |
| aggregate required fact равен 0 | `zero_aggregate` |
| rows есть и contract выполнен | `success_with_rows` |
| rows есть, но required facts/types/cardinality не выполнены | `partial` или `contract_error` |

### 2.4. get_metadata

Используются только summary, list и detail modes. Request fields ограничены
локальным DTO: `filter`, `meta_type`, `name_mask`, `attribute_mask`, `sections`,
`limit 1..1000`, `offset`, `extension_name`. Пользователь и DeepSeek напрямую их
не задают.

Применение:

- startup database profile: platform, configuration metadata/name/version;
- import compatibility assertions для объектов/полей;
- health/diagnostics;
- offline authoring/test tooling.

`get_metadata` не используется для того, чтобы DeepSeek сочинил query во время
диалога. Query template создается и проверяется до импорта package.

### 2.5. Timeout и retry MCP

| Параметр | Default |
| --- | --- |
| connect timeout | 2 s |
| read timeout basic query | 12 s |
| read timeout composite/marker operation | до 25 s из общего budget |
| attempts | максимум 2 |
| backoff | 250 ms + jitter |

Повтор разрешен только для connect reset, 502/503/504 transport bridge и read
timeout read-only операции. `success=false`, invalid envelope, schema mismatch и
4xx validation не повторяются. Оба attempts сохраняются в trace. Повтор не
создает новый logical step/evidence.

## 3. DeepSeek

### 3.1. Проверенный профиль

- base endpoint: `https://api.deepseek.com`;
- model: `deepseek-chat`;
- OpenAI-compatible Chat Completions;
- подтвержденный smoke 2026-07-21: HTTP 200 за 1378 ms, response содержит
  `choices` и `usage`;
- API key только `DEEPSEEK_API_KEY` environment, `Authorization: Bearer ...`.

Фактический request path формирует OpenAI-compatible client как
`/chat/completions`. Readiness smoke отдельно проверяет structured JSON mode,
потому что базовый smoke подтвердил transport/model, но не planner schema.

### 3.2. Planner call

Planner request содержит:

- system policy с запретами query/code/fabricated refs;
- user message и turn time;
- compact typed context с opaque handles;
- bounded skill cards без query text;
- `planner-output.schema.json` и требование вернуть только JSON object;
- expected request/context/catalog IDs для echo.

Параметры: temperature `0`, streaming off, ограниченный `max_tokens`,
`response_format={"type":"json_object"}`. Response проходит JSON parse,
Draft 2020-12 validation и domain validation. `choices[0].message.content` -
единственный используемый model output; `usage` сохраняется как metrics.

Один repair call разрешен только при parse/schema error. В него передаются
исходный JSON и компактные validation errors, но не расширенный catalog. Repair
не может добавить skill вне исходного shortlist. Вторая ошибка -
`DEEPSEEK_STRUCTURED_OUTPUT_INVALID`.

### 3.3. Answer call

Answer writer получает только validated evidence manifest, public labels и doc
chunks/citations. Он не получает raw query/MCP response. Его JSON:

```json
{
  "summary_ru": "Краткое резюме",
  "sections": [
    {
      "text_ru": "Утверждение, основанное на evidence",
      "evidence_ids": ["<fact-instance-uuid>"],
      "citation_ids": ["<citation-uuid>"]
    }
  ]
}
```

Closed Pydantic DTO запрещает дополнительные поля. Core проверяет references и
fact-like tokens; authoritative table/scalar/citations строятся без LLM. При
ошибке answer call verified renderer остается доступен и помечает отсутствие
текстового summary.

### 3.4. Timeout и retry DeepSeek

| Stage | Read timeout | Attempts |
| --- | --- | --- |
| planner | 12 s | 2 только для connect/429/5xx, если есть budget |
| repair | 10 s | 1 |
| answer | 10 s | 1, deterministic fallback |
| health smoke | 5 s | 1 |

Backoff для retry: server `Retry-After`, иначе 500 ms + jitter. 400/401/403,
invalid JSON и schema errors не считаются transport retry. Planner timeout до
выполнения означает `llm_unavailable`; MCP не вызывается. Все prompts имеют hash,
model, timings и usage; API key/header редактируются.

## 4. Индекс встроенной справки

### 4.1. Build contract

Индексатор читает только файлы `Ext/Help/ru.html`, для которых рядом есть
`Help.xml` с page `ru`. Из относительного пути выводятся metadata kind/object/form.
HTML разбирается DOM parser, scripts/styles исключаются, headings/anchors и
порядок list items сохраняются.

Chunk boundary: heading section, затем абзацы/list items до configurable size с
overlap только внутри одного source. Для каждого source/chunk сохраняются:

```text
corpus_id, release, source_kind, relative_path, metadata_kind, metadata_object,
title, heading_path, anchor, ordinal, plain_text, normalized_ru_text,
source_sha256, chunk_sha256
```

Source URI: `ut-help://11.5.27.56/<relative_path>#<anchor>`. Абсолютный путь не
попадает в evidence или UI. Index revision - SHA-256 canonical manifest всех
`relative_path + source_sha256 + parser_version + tokenizer_version`.

### 4.2. Retrieval contract

Search принимает `query`, fixed filters, top-k, max chunks/source и expected
roles из documentation skill. Ranking объединяет FTS5 BM25, exact title/heading
match и metadata path boost; source kind/release являются hard filters. Model не
может повысить внешний источник: skill schema принимает только
`source_kind=built_in_help`, adapter повторно проверяет это значение и hard-reject
происходит до чтения index.

`documentation_found` требует минимум один chunk и citation, прошедшие skill
output contract. `documentation_empty` означает успешный поиск без достаточного
chunk. Data facts и documentation claims хранятся в разных source boundaries.

Для проверки согласованности answer adapter может вернуть только группы
позиций, ссылающиеся на уже созданные fragment fact IDs и citation IDs. Core
отклоняет forged/missing refs и группы менее чем с двумя разными citations.
Валидная группа записывается в `documentation_disagreements`; deterministic
renderer выводит нейтральное сообщение о расхождении и все позиции с отдельными
ссылками. BM25 rank не используется для молчаливого разрешения расхождения.

## 5. Web/API boundary

Все routes versioned `/api/v1`; HTML UI вызывает те же routes.

### 5.1. Chat и сессии

| Method/path | Назначение |
| --- | --- |
| `POST /sessions` | Создать локальную сессию |
| `GET /sessions` | Список сохраненных сессий |
| `GET /sessions/{id}` | История, context version, timestamps |
| `DELETE /sessions/{id}` | Ручная очистка сессии после подтверждения |
| `POST /sessions/{id}/messages` | Создать turn, `202` |
| `GET /turns/{id}` | Текущее/финальное состояние |
| `GET /turns/{id}/events` | SSE progress |
| `GET /turns/{id}/details` | Plan/evidence summary без raw payload |

Enter отправляет, Shift+Enter вставляет перевод строки. UI показывает время
вопроса/ответа, progress, version и dependency status. Skill/capability IDs не
видны в обычном message block.

### 5.2. Skill administration

| Method/path | Назначение |
| --- | --- |
| `GET /skills` | Карточки purpose/params/output/compatibility/examples |
| `GET /skills/{id}` | Карточка активной версии без raw template |
| `GET /skills/{id}/export` | Portable JSON skill download |
| `POST /skill-packages/import?mode=create` | Multipart package import |
| `PUT /skill-packages/import?mode=replace` | Explicit replace + `If-Match` |
| `GET /skill-packages/export` | Export selected/all active skills |
| `DELETE /skills/{id}` | Delete from next revision + `If-Match` |

Import response: `accepted`, new revision, IDs/versions/digests. Reject response:
HTTP 422/409/503 с массивом `{code, json_pointer, message_ru}` и неизменной
revision. UI не содержит JSON editor.

### 5.3. Health и diagnostics

| Method/path | Назначение |
| --- | --- |
| `GET /health/live` | Процесс/event loop, без внешних calls |
| `GET /health/ready` | DB migrations, catalog, help index, database profile |
| `GET /diagnostics/dependencies` | Separate DeepSeek/MCP status, latency, last success |
| `GET /traces/{trace_id}/export` | Diagnostic ZIP |
| `POST /maintenance/clear` | Явная ручная очистка выбранных sessions/traces |

Health никогда не возвращает key, auth headers, prompt, query или raw response.
Из-за отсутствия auth diagnostics routes считаются local-only; default bind
`127.0.0.1` является частью безопасной конфигурации MVP.

## 6. CLI boundary

Ожидаемые команды вызывают те же use cases:

```text
chatbot1c skills validate <file>
chatbot1c skills import <file> --mode create|replace --if-match <digest>
chatbot1c skills export <skill-id>|--all --output <file>
chatbot1c skills delete <skill-id> --if-match <digest>
chatbot1c docs build-index
chatbot1c diagnostics export <trace-id>
chatbot1c baseline capture-marker
```

Exit codes различают validation/conflict/dependency unavailable/internal error.
Web и CLI portability tests сравнивают active revision и skill digests, а не
текст сообщений интерфейса.

`capture-marker` всегда создает scope `acceptance_observable_state` для
`Q001-Q116` и включает digests контрольных проекций/агрегатов, configuration
profile, catalog snapshot и documentation index. Команда не запрашивает и не
эмулирует global MCP revision token или полный snapshot ИБ.
