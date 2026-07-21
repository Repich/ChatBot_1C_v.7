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

Все JSON ingress используют один bounded loader до DTO/schema validation.
Пределы считаются по UTF-8 document bytes: `skill` 1 MiB, `skill_package`
32 MiB, `planner_output` 256 KiB, `evidence_bundle` 64 MiB. Для каждого документа
дополнительно действуют maximum depth 32, maximum 500 000 JSON nodes и общий
array ceiling 100 000; более строгие contextual `maxItems` заданы schemas.
Multipart/CLI не обходят эти ограничения, compressed JSON не принимается.
Provider bodies также bounded до parse: DeepSeek HTTP response 1 MiB, JSON
content planner/answer 256 KiB; MCP/get_metadata JSON envelope 16 MiB, depth 32,
100 000 nodes, `data` не более 1000 rows. Oversize дает
`DEEPSEEK_OUTPUT_LIMIT` или `MCP_ENVELOPE_LIMIT`, не transport retry; raw
diagnostic storage не сохраняет неограниченный payload.

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
отсутствием BSL port, immutable reviewed templates и lexer/shallow parser по
ADR-0003. Query package обязан точно соответствовать закрытому
`operation.query_template.execution`:

- `single_select` содержит один результирующий `ВЫБРАТЬ` без `ПОМЕСТИТЬ`;
- `linked_temp_batch` содержит 2..16 `ВЫБРАТЬ` в одном request: каждый
  промежуточный statement создает ровно одну объявленную temporary table, а
  последний является единственным result statement;
- parser доказывает create-before-read, unique producer, declared consumers,
  отсутствие cycles/orphans и транзитивный путь каждой temporary table к final;
- несколько независимых SELECT, несколько result sets, empty internal statement,
  любой write/DDL/administrative/BSL token и несовпадение manifest отклоняются;
- один trailing `;` допустим; разделители в строках/comments не делят statements.

Весь linked batch выполняется одним `execute_query` вызовом и одним менеджером
временных таблиц. `params` связываются для package целиком, schema/column mapping
проверяются только по финальной проекции. Фактический query language package
дополнительно проходит live contract test на совместимой УТ.

Activation skills с `linked_temp_batch` требует успешного live compatibility
probe, сохраненного в database profile и доступного в diagnostics: smoke
выполняет producer и final consumer в одном request и подтверждает, что envelope
содержит только финальную проекцию. При неподдерживаемом профиле
import/activation дает compatibility error; fallback с разбиением на несколько
MCP-вызовов запрещен.

Static validator также разбирает все literals. Каждый business-instance value
(ref/UUID, товар, склад, код, имя, номер документа, дата, произвольный threshold
или доменный текст) обязан поступать через declared typed parameter. Литерал
может остаться в template только при точном совпадении с закрытой декларацией
`operation.query_template.invariant_constants`: zero boundary, boolean,
null/undefined, empty literal,
metadata constant, structural integer или unit scale. Statement, role, value и
число occurrences должны совпасть; произвольного domain/string escape hatch нет.

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
Envelope `count` означает число rows только в этом MCP response. Оно никогда не
является business total для filtered set и не может закрывать total-count
requirement.

Классификация:

| Условие | Internal outcome |
| --- | --- |
| connect/DNS/protocol/read timeout | `mcp_unavailable` |
| валидный envelope `success=false` | `query_error` |
| `success=true`, effective empty и `empty_semantics=confirmed_not_found|confirmed_no_rows` | `success_empty` |
| aggregate required fact равен 0 | `zero_aggregate` |
| rows есть и contract выполнен | `success_with_rows` |
| envelope/column/type/nullability/cardinality/identity contract нарушен | `contract_error`; response rows discarded |
| response contract валиден, но final coverage неполон по declared pagination/composite failure | `partial` только при наличии валидного final fact |

Строка `zero_aggregate` предполагает import-valid contract:
`cardinality=aggregate`, `pagination.strategy=none`, ровно одна factual row и
`collection_scope=complete_set`. Aggregate, унаследовавший keyset/prefix от list
skill, отклоняется до выполнения как `PAGINATION_CARDINALITY_MISMATCH` и не
может быть понижен до `partial`.

Ноль строк и одна допустимая null-sentinel строка проходят одну матрицу
`output_contract.sufficiency.empty_semantics`, определенную в
`skill_contract.md`. `not_applicable` и `error_if_empty` дают разные
`contract_error` codes; запрещенный `null` проверяется раньше этой матрицы.
Producer contract violation никогда не понижается до `partial`.

Для `count` coverage дополнительно проверяется collection scope. Count над
одним `page_is_complete`/keyset StepResult имеет scope `visible_page`, даже если
это последняя continuation page с `has_more=false`; public label только
`Показано N`. Total fact и label `Всего N` разрешены лишь для fully materialized
proved-prefix set, непагинируемого complete-set либо отдельного aggregate
producer. Scope mismatch отклоняется до MCP как
`PLAN_COUNT_SCOPE_MISMATCH`. В Evidence 1.1
`step_evidence.collection_scope` обязателен; default `complete_set` запрещен.
Только legacy 1.0 reader подставляет его при omission in-memory.

Normalized evidence сохраняет каждый planner requirement по exact
`requirement_id`; в Evidence 1.1 `coverage.requirements[*].required`
обязательно присутствует и равно immutable CoverageProof. Legacy 1.0 omission
нормализуется в `true` только при чтении. `coverage.sufficient` пересчитывается
как conjunction `status=covered` и satisfied collection obligation для каждого
`required=true`. Missing/ambiguous/incompatible/incomplete optional entries
остаются в payload, но не делают `sufficient=false`. Covered facts incomplete
page сохраняются; их required `complete_set` obligation делает bundle
insufficient без подмены status на `wrong_cardinality`.

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

После schema parse PlanValidator до любого MCP-вызова доказывает покрытие всех
requirements и отдельно final fact. Совпадение требует semantic type,
cardinality, unit, time semantics и identity/dimensions; одно имя fact type или
наличие intermediate fact недостаточно. Exact `(skill_id, version, digest)`
берется только из pinned catalog snapshot.

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
отклоняет forged/missing refs, повтор одной ссылки под разными позициями, группы
менее чем с двумя разными citations и позицию без собственного provenance.
Противоречащие positions сохраняются раздельно; они не агрегируются в один факт.
Валидная группа записывается в `documentation_disagreements`; deterministic
renderer выводит нейтральное сообщение о расхождении и все позиции с отдельными
ссылками. BM25 rank, порядок MCP/docs response и LLM confidence не используются
для молчаливого разрешения расхождения.

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
| `POST /sessions/{id}/continuations` | Одноразово получить следующую страницу без replanning, `202` |
| `GET /turns/{id}` | Текущее/финальное состояние |
| `GET /turns/{id}/events` | SSE progress |
| `GET /turns/{id}/details` | Plan/evidence summary без raw payload |

Enter отправляет, Shift+Enter вставляет перевод строки. UI показывает время
вопроса/ответа, progress, version и dependency status. Skill/capability IDs не
видны в обычном message block.

Terminal turn с пагинацией публикует минимальный DTO:

```json
{
  "pagination": {
    "shown": 20,
    "page_size": 20,
    "has_more": true,
    "continuation": {
      "handle": "page_<base64url>",
      "expires_at": "2026-07-21T12:30:00Z"
    }
  }
}
```

`pagination.shown` всегда равно числу строк именно в public page и отображается
как `Показано N`; DTO намеренно не содержит `total`. Поле total может появиться
только как отдельный verified aggregate fact. Для keyset continuation endpoint
вызывает pinned producer с exact cursor и `page_size+1`; для proved-prefix он
режет уже materialized immutable source evidence и не вызывает MCP.

При `has_more=false` поле `continuation` равно `null`. Продолжение принимает
closed JSON `{ "continuation_handle": "page_<base64url>" }` и при успехе
возвращает тот же acceptance DTO, что message submission: HTTP 202,
`status=accepted`, `turn_id`, `trace_id`. Handle не передает cursor, params или
binding fields на клиент.

Любой reject до создания turn возвращает
`{status:"rejected",trace_id,error:{code,message_ru,retryable:false}}`.
Нормативные continuation errors:

| Состояние | HTTP | `error.code` |
| --- | --- | --- |
| syntax не соответствует `page_` contract | 422 | `CONTINUATION_HANDLE_INVALID` |
| well-formed forged/unknown handle | 404 | `CONTINUATION_NOT_FOUND` |
| handle принадлежит другой session | 409 | `CONTINUATION_SESSION_MISMATCH` |
| handle уже принят ранее | 409 | `CONTINUATION_CONSUMED` |
| прошло 30 минут от `issued_at` | 410 | `CONTINUATION_EXPIRED` |
| active catalog snapshot отличается от bound snapshot | 409 | `CONTINUATION_CATALOG_CHANGED` |
| catalog совпадает, но database marker отличается | 409 | `CONTINUATION_MARKER_CHANGED` |

Reject не создает turn и не вызывает DeepSeek/MCP. Если одновременно изменены
catalog и marker, возвращается `CONTINUATION_CATALOG_CHANGED`.

### 5.2. Skill administration

| Method/path | Назначение |
| --- | --- |
| `GET /skills` | Карточки purpose/params/output/compatibility/examples |
| `GET /skills/{id}` | Карточка активной версии без raw template |
| `GET /skills/{id}/export` | Canonical bare `skill` JSON; существующее поведение |
| `GET /skills/{id}/export?closure=embedded` | Один self-contained `skill_package` JSON с выбранным skill и transitive closure |
| `POST /skills/import?mode=create|replace` | Canonical multipart import `skill` или `skill_package`; replace требует `If-Match` |
| `POST /skill-packages/import?mode=create` | Совместимый alias только для package import |
| `PUT /skill-packages/import?mode=replace` | Совместимый package replace alias + `If-Match` |
| `GET /skill-packages/export` | Export selected/all active skills |
| `DELETE /skills/{id}` | Delete from next revision + `If-Match` |

Import response: `accepted`, new revision, IDs/versions/digests. Reject response:
HTTP 422/409/503 с массивом `{code, json_pointer, message_ru}` и неизменной
revision. UI не содержит JSON editor.

Canonical endpoint и CLI читают top-level `document_type` и dispatch-ят в
`skill.schema.json` либо `skill-package.schema.json`; иной/неоднозначный
discriminator отклоняется. Общий pre-parse hard cap равен 32 MiB, streaming
discriminator reader сохраняет depth/node limits, после определения `skill`
дополнительно применяется его 1 MiB limit до создания полного DTO.

Bare skill не преобразуется на wire и не получает новый digest. Import use case
атомарно строит внутренний closure из root и exact dependencies pinned active
snapshot. Missing dependency возвращает `SKILL_DEPENDENCY_MISSING`; ничего не
устанавливается и revision не меняется. Для package проверяется переданный
замкнутый `dependency_lock`: embedded skills и транзитивные dependencies
присутствуют ровно один раз, лишних entries нет. Digest embedded entry совпадает
с document integrity; external entry - с pinned active catalog. Та же
`(skill_id, version)` с иным digest возвращает conflict до transaction.

UI выбирает bare export для skill без skill dependencies и
`closure=embedded` для dependency-bearing skill. В обоих случаях ровно тот же
скачанный JSON-файл принимают web и CLI второго instance. AC-007/AC-008 имеют
два теста: dependency-free bare skill импортируется в чистый compatible
`APP_DATA_DIR`; selected dependency-bearing skill переносится в чистый instance
одним self-contained package-файлом. Дополнительно bare dependency-bearing skill
без установленного closure обязан атомарно отклоняться.

### 5.3. Health и diagnostics

| Method/path | Назначение |
| --- | --- |
| `GET /health/live` | Процесс/event loop, без внешних calls |
| `GET /health/ready` | DB migrations, catalog, help index, database profile |
| `GET /diagnostics/dependencies` | Separate DeepSeek/MCP status, latency, last success |
| `GET /traces/{trace_id}/export` | Diagnostic ZIP |
| `POST /maintenance/clear` | Двухфазная preview/confirm очистка выбранных scopes |

`POST /maintenance/clear` принимает ровно одну из двух closed DTO forms:

```json
{"mode":"preview","scopes":["sessions","traces"]}
```

```json
{
  "mode":"confirm",
  "scopes":["sessions","traces"],
  "confirmation_token":"clear_<base64url>"
}
```

`scopes` непуст, не содержит повторов и допускает только `sessions`, `traces`,
`raw_payloads`. Preview canonicalizes порядок как `sessions`, `traces`,
`raw_payloads` с пропуском невыбранных scopes и возвращает HTTP 200:

```json
{
  "status": "preview",
  "scopes": ["sessions", "traces"],
  "counts": {"sessions": 2, "traces": 7, "raw_payloads": 11},
  "confirmation_token": "clear_<base64url>",
  "expires_at": "2026-07-21T12:05:00Z"
}
```

Confirm с теми же scopes и неизменным target set возвращает
`{status:"cleared",scopes,deleted:{sessions,traces,raw_payloads}}`; `deleted`
точно равен preview `counts`. Token является server-side opaque CSPRNG handle,
действует 5 минут и становится consumed только после успешного commit. HMAC,
подпись и auth к нему не добавляются.

| Ошибка clear | HTTP | `error.code` |
| --- | --- | --- |
| unknown/duplicate/empty scopes либо неверная DTO form | 422 | `CLEAR_SCOPES_INVALID` |
| forged/unknown token | 404 | `CLEAR_CONFIRMATION_NOT_FOUND` |
| token expired | 410 | `CLEAR_CONFIRMATION_EXPIRED` |
| token already committed | 409 | `CLEAR_CONFIRMATION_CONSUMED` |
| confirm scopes не равны preview scopes | 409 | `CLEAR_SCOPE_MISMATCH` |
| target fingerprint/counts изменились после preview | 409 | `CLEAR_PREVIEW_STALE` |
| target содержит non-terminal turn | 409 | `CLEAR_TARGET_ACTIVE` |

Ошибки используют тот же rejected DTO и ничего не удаляют. Catalog, help index,
database profile и marker не являются допустимыми scopes.

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
