# Приемка среза 1

## Статус

Product E2E имеет статус `blocked_implementation`, пока runner не передал
публичные FastAPI/CLI endpoints через переменные ниже; наличие внутренних
модулей не считается доказательством готовности. Успешные проверки fixture
transport не являются приемкой приложения. Тесты не импортируют production
модули и работают только через HTTP, SSE, ZIP и subprocess CLI.

## Минимальный публичный DTO

Архитектура фиксирует routes, но не полные response schemas. До появления
OpenAPI среза 1 black-box tests закрепляют следующий минимальный контракт:

- `POST /api/v1/sessions` -> HTTP 201, `session_id`, `context_version`;
- `POST /api/v1/sessions/{id}/messages` -> HTTP 202, `turn_id`, `trace_id`,
  `status=accepted`; request JSON is `{text: string}`;
- `GET /api/v1/turns/{id}` -> `turn_id`, terminal `status`, typed `outcome`,
  `trace_id`, `assistant_message.text`, `assistant_message.citations`,
  `pinned.catalog_revision`, `pinned.catalog_snapshot_id`;
- a missing or ambiguous required entity completes with
  `outcome=clarification_required`, one question and no fabricated context
  export; a returned row whose order ref differs from the bound order completes
  with `outcome=contract_error` and no factual row rendering;
- `GET /api/v1/sessions/{id}` -> persisted messages with `role`, `text`,
  `created_at` and `turn_id`;
- SSE event data is JSON with `turn_id`, integer monotonic `sequence`, `stage`,
  `status`, `occurred_at`; stream contains progress before terminal event;
- accepted package import returns `status=accepted`, monotonic
  `catalog_revision` and exact `{skill_id, version, digest}` entries;
- rejected import returns HTTP 422/409/503 and
  `{status=rejected, catalog_revision, errors:[{code,json_pointer,message_ru}]}`;
- CLI writes the equivalent result as one JSON object to stdout and uses
  different non-zero exit codes for validation, conflict and unavailable
  dependency;
- diagnostic response is `application/zip`, includes deterministic files from
  the architecture, valid `checksums.sha256`, and contains no configured secret
  canary, auth header or absolute local path.

These fields are the minimum observable state needed to prove the accepted
architecture. Additional closed, versioned fields are allowed.

## Required environment

Chat E2E:

```text
SLICE1_BASE_URL=http://127.0.0.1:8000
SLICE1_FIXTURE_URL=http://127.0.0.1:<fixture-port>
SLICE1_SECRET_CANARY=<synthetic value injected as DEEPSEEK_API_KEY>
```

Catalog and portability E2E additionally use:

```text
SLICE1_CLI="chatbot1c"
SLICE1_CLEAN_BASE_URL=http://127.0.0.1:<clean-app-port>
SLICE1_PACKAGE_PATH=<accepted slice-1 package>
SLICE1_REPLACEMENT_PACKAGE_PATH=<new-version package>
SLICE1_REPLACE_IF_MATCH=<expected digest for the replacement operation>
SLICE1_HOT_RELOAD_QUESTION=<question enabled only by that package>
```

`SLICE1_CLEAN_BASE_URL` must be a separately launched app instance whose
isolated data directory has never received a package; the black-box precondition
is `GET /api/v1/skills -> {skills: []}`. CLI uses a newly created empty temporary
directory. Architecture does not define a public server launch command, so the
test cannot itself prove the physical web directory was empty; this remains an
explicit runner precondition rather than an inference about persistence files.

If built-in fixture planner responses do not match the concrete package IDs,
`SLICE1_PLANNER_RESPONSES_PATH` may point to a JSON map from fixture scenario
name to planner-output object. This is transport input, not an expected answer.

The web application must start with a clean `APP_DATA_DIR`, and both application
and CLI must point to the same synthetic MCP/DeepSeek fixture profile. Tests
never infer these paths from repository internals.

## Independent transports

`tests/fixtures/slice1/transport_server.py` is an external process. It exposes
OpenAI-compatible `/chat/completions`, MCP Streamable-HTTP JSON-RPC `/mcp`, and
local-only fixture control routes. It records request bodies but never auth
header values. Scenarios cover rows, exact `_objectRef`, empty, query error,
malformed MCP and malformed/schema-invalid DeepSeek output.

Real MCP normalization additionally accepts the observed minimal envelopes
`{success:true,data,schema}` and `{success:false,error}`. `count`, `truncated`
and `has_more` are not required at the adapter input boundary; normalized
evidence derives `row_count=len(data)` and must not reject either valid form.

Planner fixture content can be overridden through the control route after the
slice package publishes exact skill IDs. Until that package exists, fixture
transport contract tests run, while product Q001/Q011/Q036-Q037/Q102 remain
`blocked_implementation`.

## Открытые пробелы публичного контракта

- Architecture route tables не задают полные request/response DTO и OpenAPI;
  минимальные поля и outcome `clarification_required` закреплены этим набором.
- Не определена публичная команда запуска web process с новым `APP_DATA_DIR`,
  поэтому физически чистый web dir остается precondition runner; API проверяет
  пустой catalog до import.
- Один HTTP `If-Match` не описывает кодирование нескольких expected digests для
  multi-skill replace. Runner передает согласованное значение явно через
  `SLICE1_REPLACE_IF_MATCH`.
- Для identity mismatch архитектура требует reject, но не фиксирует публичный
  error code. Acceptance требует `contract_error` и запрет factual rendering,
  не навязывая внутреннее имя ошибки.

## Acceptance boundary

- Q001 requires a grounded `ut-help://` citation.
- Q011 requires an observed read-only `execute_query` call.
- Q036-Q037 require byte-for-byte JSON equality of the order `_objectRef`
  returned by the first MCP response and passed in parameters of the next turn.
- Q036 ambiguity must not select the first row or export a selected order. Q037
  without a retained order must clarify without MCP; normal Q037 resolves an
  opaque `ContextBinding` and performs no second order-number lookup. Every
  Q037 evidence row must repeat the bound order ref or be rejected.
- DeepSeek request bodies must contain no active query template text.
- Q102 is `success_empty`; a valid `success=false` envelope is `query_error` and
  must produce a different outcome and message.
- Import/replace tests compare revisions and digests, not localized prose.
- A turn started before replace keeps its old pinned snapshot; only the next
  turn sees the new revision.
- Every fixture-backed mandatory turn must finish in at most 30 seconds.
