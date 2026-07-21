# Persistence и наблюдаемость

## 1. Выбор хранилища

MVP использует один SQLite database в WAL mode. Это соответствует одному
процессу и одной информационной базе, дает транзакции для каталога/сессий и FTS5
для справки. Все timestamps хранятся UTC ISO 8601, UI отображает
`Europe/Moscow`. Schema изменяется только Alembic migrations.

Сессии и traces хранятся до явной ручной очистки. Автоматический TTL в MVP нет.
Raw diagnostic payloads могут занимать много места, поэтому UI показывает объем,
а maintenance operation позволяет очистить выбранные traces без удаления
сессий.

## 2. Логическая модель

### 2.1. Диалог

| Таблица | Ключевые поля и инварианты |
| --- | --- |
| `sessions` | `id`, title, `context_version`, created/updated; optimistic version |
| `messages` | `id`, session, turn, role, public content, sent/received time; append-only |
| `turns` | request/trace IDs, status/outcome, pinned revisions, deadline, timestamps |
| `context_facts` | handle, semantic type, plain JSON value, entity identity, `origin_turn_id`, `origin_fact_instance_id`, active/replaced time |
| `pending_clarifications` | original turn, planner interpretation/requirements, one question, context version |
| `page_continuations` | random opaque handle, session/turn, normalized request, cursor, snapshot/marker, consumed/expiry policy |

`context_facts` хранит только confirmed evidence. Для entity ref уникальность в
сессии определяется `(semantic_type, ТипОбъекта, УникальныйИдентификатор)`.
Presentation не является identity. Замена товара в follow-up закрывает прежний
active slot и добавляет новый fact; warehouse/moment facts остаются активными.
При чтении handle origin pointers восстанавливают producer step, immutable skill
digest и exact column binding из сохраненного evidence/pinned snapshot. Значение
без этой цепочки не может стать MCP parameter.

Context и canonical normalized evidence хранятся обычным JSON в локальном
SQLite без application-level encryption. Continuation не содержит клиентского
payload: base64url handle из 24 байт CSPRNG адресует server-side запись. При
чтении repository проверяет session, consumed/expiry и pinned revisions. HMAC и
управление ключами
не входят в MVP. Файл SQLite и diagnostic bundles защищаются правами локального
пользователя/ОС; корпоративная security boundary находится вне scope прототипа.

Clarification создается в той же transaction, что assistant question. Ответ на
него использует сохраненную interpretation и не теряет исходный request.

### 2.2. Каталог

| Таблица | Ключевые поля и инварианты |
| --- | --- |
| `catalog_revisions` | monotonic revision, snapshot UUID, operation, package digest, created time |
| `skill_documents` | immutable `(skill_id, version, digest)`, canonical JSON, provenance |
| `active_skills` | revision, skill ID, version/digest; complete snapshot mapping |
| `skill_dependencies` | revision, from/to skill/version constraints |
| `package_imports` | package ID/version/digest, command mode, result revision, validation summary |
| `database_profiles` | MCP-observed config/platform metadata and required-metadata fingerprint |

Одинаковая `(skill_id, version)` с другим digest невозможна. Delete удаляет
skill только из новой active mapping; immutable document может оставаться для
historical trace reproducibility. Это не пользовательский draft lifecycle.

Каждый turn хранит snapshot ID и список фактически использованных
`skill_id/version/digest`; последующее удаление не ломает диагностику.

### 2.3. Документация

| Таблица | Назначение |
| --- | --- |
| `doc_corpora` | corpus/release/parser/tokenizer/index revision и manifest digest |
| `doc_sources` | relative path, metadata object, title, source hash |
| `doc_chunks` | source, heading/anchor/order, text, role hints, chunk hash |
| `doc_chunks_fts` | normalized title/heading/body FTS5 virtual table |

Index build происходит во временных tables/file. После полного parse и
integrity checks transaction меняет active corpus revision. Turn pin-ит index
revision аналогично catalog snapshot.

### 2.4. Evidence и traces

| Таблица | Назначение |
| --- | --- |
| `trace_events` | ordered sequence: stage, timestamp, attempt, duration, status, public metadata |
| `external_calls` | provider, request/response status, timing, retry, payload references |
| `diagnostic_payloads` | compressed raw prompt/query/params/response с media type и SHA-256 |
| `evidence_bundles` | schema version, canonical plain JSON evidence, digest, created time |
| `step_evidence` | normalized outcome, row counts, truncation, operation refs |
| `facts` | typed fact instances, row IDs, units/time, source locator |
| `citations` | help corpus, title, relative path, anchor, chunk hash |
| `documentation_disagreements` | kind и минимум две позиции с fact/citation refs одного встроенного корпуса |
| `coverage_results` | required fact -> covered/missing/type/unit/time result |
| `database_state_markers` | marker profile/version/digest/components/timestamp |

`evidence.schema.json` описывает экспортируемую нормализованную форму. Raw MCP
не включается в public evidence object; diagnostic bundle добавляет его отдельным
файлом.

## 3. Транзакционные границы

1. Прием message: session/version check + user message + turn/trace.
2. Clarification: assistant message + pending request + context version.
3. Завершение turn: assistant message + outcome + evidence refs + context facts +
   page handle + context version.
4. Import/replace/delete: immutable documents + full active mapping + dependency
   graph + new catalog revision.
5. Help index activation: corpus manifest + all sources/chunks + active revision.

Внешние HTTP/MCP calls никогда не выполняются внутри SQLite write transaction.
Их intent/result сохраняются короткими transactions. Crash recovery переводит
turn в `interrupted` и сохраняет trace; side-effect в 1С невозможен из-за
read-only.

## 4. Catalog hot reload

`CatalogManager` держит immutable snapshot object. Import flow:

```text
validate outside transaction
begin immediate
  verify expected current revision/digests
  insert immutable documents if new
  copy active mapping and apply command
  validate dependency closure
  insert revision and mapping
commit
build snapshot from committed revision
atomic reference swap
```

Turn получает strong reference до planner call. Revision watcher сравнивает
in-memory и DB revision после каждого import и периодически; это восстанавливает
swap после crash. Нет mutable global list, файлового watcher-а и hidden skill
creation во время пользовательского запроса.

## 5. Database profile и marker

### 5.1. Compatibility profile

На startup и перед import при устаревшем cache `get_metadata` summary/detail
фиксирует configuration name/version, compatibility/platform values и exact
required metadata assertions. Profile получает digest canonical JSON.

Если MCP недоступен, можно использовать только profile, успешно полученный в
текущем process lifetime для этой one-database binding. Если такого profile нет,
activation package отклоняется `MCP_UNAVAILABLE_FOR_COMPATIBILITY_CHECK`; JSON не
сохраняется как кандидат. Пользователь повторяет import после восстановления.

### 5.2. Marker состояния данных

Доступный MCP не предоставляет транзакционный revision всей информационной базы.
Поэтому MVP явно определяет marker как **acceptance-observable state**, а не как
криптографический snapshot всех таблиц 1С.

Marker profile - отдельный versioned тестовый manifest фиксированных read-only
control queries, независимо написанных тестировщиком, для всех источников,
влияющих на `Q001-Q116`. Проекции включают канонические row sets и агрегаты;
marker дополнительно pin-ит revisions/digests configuration profile, active
catalog и встроенного documentation index. Результат:

```json
{
  "scope": "acceptance_observable_state",
  "profile_version": "1.0.0",
  "acceptance_suite_version": "q001-q116-v1",
  "configuration_revision": "11.5.27.56",
  "configuration_profile_digest": "<sha256>",
  "catalog_revision": 12,
  "catalog_snapshot_digest": "<sha256>",
  "documentation_revision": "ut-help-11.5.27.56-r1",
  "documentation_manifest_digest": "<sha256>",
  "projection_manifest_digest": "<sha256>",
  "projections": [
    {
      "projection_id": "sales-documents",
      "kind": "row_set",
      "query_digest": "<sha256>",
      "result_digest": "<sha256>"
    }
  ],
  "digest": "<sha256>"
}
```

Каждый result canonicalized с явным sort и типами. Итоговый SHA-256 считается
по canonical object всех перечисленных components, кроме собственного `digest`.
Baseline values сохраняются с marker, package/corpus/test versions и captured
time. Если изменился любой component или probe не выполнен, numeric/list/doc
baselines устарели и пересоздаются до оценки.

Ограничение: изменение данных вне observable projections может не изменить
marker, поэтому он не доказывает неизменность всей ИБ. Это принятое ограничение
MVP: global revision token MCP и полный snapshot не требуются и не эмулируются.

## 6. Trace model

Минимальные event names:

```text
request.accepted
snapshot.pinned
context.loaded
planner.requested / planner.completed / planner.failed
plan.schema_validated
plan.coverage_validated
step.started / step.attempt / step.completed / step.failed
evidence.normalized / evidence.validated
answer.requested / answer.validated / answer.fallback
context.committed
request.completed
```

Каждый event содержит `trace_id`, request/session/turn IDs, monotonic sequence,
timestamp, stage, duration, status и hashes relevant artifacts. Capability IDs,
skill IDs, parameters, MCP calls, raw responses, outcome и errors доступны для
воспроизведения. В обычный log идут только IDs, counts, durations, outcomes и
error codes.

## 7. Redaction

Перед persistence/log/export запрещаются:

- `DEEPSEEK_API_KEY`, Authorization/Cookie headers;
- environment dumps и connection strings с credentials;
- local absolute paths (заменяются logical labels/relative help paths);
- framework exception locals, которые могут содержать secret;
- случайные continuation handles вне необходимого request/response context.

Raw business data MCP по требованию сохраняется в diagnostics. Это локальный
прототип без auth, поэтому diagnostic package считается чувствительным и UI явно
помечает его как техническую выгрузку. Redaction test использует secret canaries
и блокирует экспорт при совпадении.

## 8. Выгружаемый диагностический пакет

Формат: ZIP с детерминированными именами и `manifest.json`:

```text
manifest.json
request.json
context.json
catalog-snapshot.json
planner/request.json
planner/response.json
plan.json
coverage-pre.json
steps/s01/request.json
steps/s01/response.json
steps/s01/evidence.json
evidence.json
answer/request.json
answer/response.json
events.jsonl
errors.json
environment-summary.json
checksums.sha256
```

`manifest` содержит app/schema/package/index/marker versions, file media types,
sizes и SHA-256. Secrets отсутствуют. Query text и raw MCP доступны только в
`steps/*` этого bundle. По bundle можно запустить offline replay normalizer,
coverage validator и renderer без DeepSeek/MCP; live replay является отдельной
операцией и не выполняется автоматически.

## 9. Метрики и health

Основные counters/histograms:

- turns/outcomes по типу, без текста вопроса;
- planner/MCP/answer latency и attempts;
- schema/coverage/contract failures;
- selected plan step count и shortlist size;
- evidence row/fact counts, truncation, continuation;
- catalog revision/import result;
- help index revision/query latency;
- SQLite size, trace/raw payload bytes;
- last successful DeepSeek/MCP smoke.

`live` не вызывает внешние сервисы. `ready` проверяет migration, writable DB,
active catalog, active help index и database profile. Dependency diagnostics
проверяет DeepSeek и MCP раздельно и возвращает `healthy/degraded/unavailable`,
latency, last success и public error code.

## 10. Backup, clear и portability

- SQLite backup выполняется online backup API в отдельный файл с manifest/hash.
- Skill export строится из immutable canonical document, а не UI model.
- Manual clear принимает scopes `sessions`, `traces`, `raw_payloads`, preview
  counts и confirmation token; catalog/help index не удаляются этой командой.
- Windows/macOS используют одинаковую schema и logical paths; `APP_DATA_DIR`
  выбирается platform adapter.
