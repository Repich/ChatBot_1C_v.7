# Persistence и наблюдаемость

## 1. Выбор хранилища

MVP использует один SQLite database в WAL mode. Это соответствует одному
процессу и одной информационной базе, дает транзакции для каталога/сессий и FTS5
для справки. Все timestamps хранятся UTC ISO 8601, UI отображает
`Europe/Moscow`. Schema изменяется только Alembic migrations.

Сессии и traces хранятся до явной ручной очистки; автоматического retention TTL
для них в MVP нет. TTL применяется только к ephemeral continuation,
clarification и confirmation handles и не удаляет принадлежащие им turns/traces.
Raw diagnostic payloads могут занимать много места, поэтому UI показывает объем,
а maintenance
operation позволяет очистить выбранные traces без удаления сессий.

## 2. Логическая модель

### 2.1. Диалог

| Таблица | Ключевые поля и инварианты |
| --- | --- |
| `sessions` | `id`, title, `context_version`, created/updated; optimistic version |
| `messages` | `id`, session, turn, role, public content, sent/received time; append-only |
| `turns` | request/trace IDs, status/outcome, pinned revisions, deadline, timestamps |
| `context_facts` | immutable semantic/value type, canonical value bytes/digest, `origin_turn_id`, `origin_fact_instance_id`; full entity ref и raw scalar остаются server-side |
| `context_slots` | session, portable slot key, generation, random handle, semantic/value type, policy mode/cardinality, membership/value digest, lifetime, active/replaced/expired/invalidated state |
| `context_slot_members` | exact ordered entity origin facts или один scalar origin одной slot generation |
| `pending_clarifications` | random one-use handle, kind, original turn/plan/requirements/candidates, context/catalog/marker binding, issued/expires/consumed/superseded state |
| `page_continuations` | random opaque handle, session/source turn, mode `keyset|prefix`, normalized request, skill/version/digest, keyset cursor/sort tuple or prefix evidence offset, exact snapshot/marker, `issued_at`/`expires_at`/`consumed_at` |
| `maintenance_previews` | random opaque confirmation token, canonical scopes, target fingerprint/counts, `issued_at`/`expires_at`/`consumed_at` |

`context_facts` хранит только confirmed evidence. Для entity ref уникальность в
сессии определяется `(semantic_type, ТипОбъекта, УникальныйИдентификатор)`.
Presentation не является identity. Активность задает не append order, а ровно
одна active generation на `(session_id, slot_key)`. Замена товара закрывает
прежний exact slot и добавляет generation; warehouse/moment slots остаются
активными. Candidates, display lists и line rows без SelectionProof в slots не
попадают. Non-entity fact попадает только по отдельному `confirmed_filter` proof;
moment/period/enum/detail хранится canonical и не вычисляется заново. При чтении
handle origin pointers восстанавливают producer step, immutable skill digest,
typed parameter source/Evidence locator и exact contract из сохраненного
evidence/pinned snapshot. Entity дополнительно требует physical column proof.
Значение без этой цепочки не может стать MCP parameter.

Context и canonical normalized evidence хранятся обычным JSON в локальном
SQLite без application-level encryption. Continuation не содержит клиентского
payload: base64url handle из 24 байт CSPRNG адресует server-side запись. TTL
handle фиксирован: `expires_at = issued_at + 30 minutes`; это не TTL session,
turn или trace. При чтении repository проверяет session, consumed, expiry,
точный active catalog snapshot и затем database marker. Любое отличие catalog
или marker отклоняет continuation до MCP; immutable historical snapshot не
используется для продолжения после catalog change. Claim handle и создание
нового turn выполняются одной transaction, поэтому два concurrent claim не
могут оба получить HTTP 202. Consumed и expired rows не удаляются самим TTL:
они остаются до clear source session, чтобы public `CONSUMED`/`EXPIRED` не
деградировали в `NOT_FOUND`.

Для keyset handle хранит exact last displayed sort tuple. Для proved-prefix
полный bounded set сохраняется в immutable source-turn evidence; handle хранит
только следующий display offset и ссылку на это evidence. Поэтому prefix
continuation не перечитывает mutable database и не может повторить первый
prefix. Если actual producer boundary превышает proved `maximum_total`, source
evidence не создается и response завершается
`RESULT_PREFIX_BOUND_EXCEEDED`.

Confirmation token clear использует тот же минимальный подход: `clear_*` handle
из 24 байт CSPRNG адресует server-side preview record и действует 5 минут. Он
связан с canonical scopes и fingerprint точного target set, без клиентского
payload, HMAC или signing keys. HMAC и управление ключами не входят в MVP. Файл
SQLite и diagnostic bundles защищаются правами локального пользователя/ОС;
корпоративная security boundary находится вне scope прототипа.

Clarification создается в той же transaction, что assistant question, и имеет
30-minute one-use handle. Ответ atomically claim-ит exact pending state,
использует сохраненные plan/interpretation/requirements и не теряет исходный
request. Resolver candidate выбирается без LLM; interpretation choice допускает
только composition call с frozen typed binding. Полная state/error matrix задана
в `docs/testing/slice3_acceptance_contract.md`.

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
| `plan_coverage_proofs` | immutable final-to-requirement mapping, canonical predecessors, `required_closure`, per-step criticality/required-by IDs and digest |
| `step_evidence` | normalized outcome, row counts, truncation, `visible_page|complete_set` collection scope, operation refs |
| `facts` | typed fact instances, row IDs, units/time, source locator |
| `citations` | help corpus, title, relative path, anchor, chunk hash |
| `documentation_disagreements` | kind и минимум две позиции с fact/citation refs одного встроенного корпуса |
| `coverage_results` | every planner requirement exactly once: `requirement_id`, copied `required` boolean, covered/missing/type/unit/time/collection-scope result and fact refs |
| `database_state_markers` | marker profile/version/digest/components/timestamp |

`evidence.schema.json` описывает экспортируемую нормализованную форму. Raw MCP
не включается в public evidence object; diagnostic bundle добавляет его отдельным
файлом.

Evidence reader dispatches strictly by explicit `schema_version` before common
semantic validation. Frozen/stored `1.0.0` is legacy read-only: only that branch
may materialize missing `steps[*].collection_scope=complete_set` and
`coverage.requirements[*].required=true` in memory. It does not mutate the
stored JSON, fixture, digest input or diagnostic export. Presence of the fields
does not upgrade a 1.0 payload, and unknown versions are rejected.

Every newly completed turn writes Evidence `1.1.0` and explicitly supplies both
fields. A 1.1 omission is a contract error; neither repository nor Pydantic/JSON
Schema layer applies a default. Automatic rewrite of historical bundles is not
part of slice 2.

The persisted PlanCoverageProof includes an exact requirement-to-final mapping,
copied criticality and collection obligation
`fact|visible_page|complete_set`. Before evidence persistence/rendering, the
cross-artifact validator compares it with the pinned plan/catalog and evidence,
then derives `coverage.sufficient` from required fact coverage and required
collection completeness. Evidence-only replay cannot claim that ID or
criticality drift was checked unless the pinned plan and proof are available.

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
- Manual clear принимает scopes `sessions`, `traces`, `raw_payloads`. Scope
  `sessions` удаляет session roots и весь принадлежащий им chat/context/
  continuation/trace/evidence/raw graph. Scope `traces` удаляет diagnostic
  trace/evidence/raw graph, но сохраняет sessions, messages и terminal turn
  summary; сохраненный `trace_id` после этого больше не экспортируется. Scope
  `raw_payloads` удаляет только raw blobs. Пересечения считаются как union без
  повторного учета.
- Preview возвращает counts удаляемых root sessions, traces и raw payload rows,
  а server-side token связывает scopes, counts и fingerprint всех затронутых
  rows. Non-terminal target turn блокирует preview/confirm.
- Confirm в одной write transaction повторно вычисляет fingerprint. При любом
  изменении target set transaction ничего не удаляет и возвращает
  `CLEAR_PREVIEW_STALE`; при успехе фактические deleted counts обязаны точно
  совпасть с preview, после чего token помечается consumed.
- Catalog revisions/documents, active catalog, help index, database profiles и
  markers не входят в clear closure и не удаляются этой командой.
- Windows/macOS используют одинаковую schema и logical paths; `APP_DATA_DIR`
  выбирается platform adapter.
