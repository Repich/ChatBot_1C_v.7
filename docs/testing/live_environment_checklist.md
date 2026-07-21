# Checklist живой среды

Статус на 2026-07-21: **`blocked_external`**. MCP сейчас не запущен. Ни один
пункт live UT/data acceptance ниже не отмечен выполненным.

## 1. Идентификация артефактов

- [ ] Зафиксированы commit, app version, package version/digest и schema version.
- [ ] Зафиксирован corpus `ut_11_5_27_56_mvp`, `Q001-Q116`.
- [ ] Подтверждены configuration ID, release `11.5.27.56` и compatibility `8.3.27`.
- [ ] Подтвержден active built-in help index и его manifest digest.
- [ ] Секреты и абсолютные пути не записаны в отчет или fixtures.

## 2. MCP и контрольная база

- [ ] MCP endpoint доступен через официальный Streamable HTTP client.
- [ ] `initialize` и `tools/list` успешны.
- [ ] Присутствуют `execute_query` и `get_metadata`.
- [ ] Дополнительные server tools не опубликованы через application port.
- [ ] Локальный forbidden-tool test останавливает write/BSL call до сети.
- [ ] `get_metadata` подтверждает configuration/profile целевой базы.
- [ ] Adapter profile явно подтверждает `supports_linked_temp_batch=true`.
- [ ] `execute_query` smoke использует `include_schema=true` и limit `1..1000`.
- [ ] Linked smoke выполняет producer и final consumer одним `execute_query` call.
- [ ] Linked smoke envelope содержит только aliases финальной проекции.
- [ ] Профиль без linked capability отклоняет import/activation compatibility
  error и не разбивает batch на несколько calls.
- [ ] Live negative query возвращает `success=false`, а не `success_empty`.
- [ ] Read-only audit не обнаруживает изменений данных до/после suite.

## 3. DeepSeek

- [x] Endpoint/model/auth smoke успешен: HTTP 200; key не раскрывался. Evidence: [source inventory](../source_inventory.md#deepseek).
- [x] JSON-mode smoke с `response_format={"type":"json_object"}` вернул валидный JSON и echo `request_id`.
- [ ] Planner output проходит Draft 2020-12 и domain validation.
- [ ] Prompt/response не содержат shipped query templates.
- [ ] Один repair call проверен на malformed/schema-invalid response.
- [ ] Timeout дает `llm_unavailable`, trace ID и сохраняет session.

## 4. Marker и независимые baselines

- [ ] Независимый reviewer подтвердил control query design.
- [ ] Ни один control query не скопирован и не производен от shipped skill query.
- [ ] Выполнены все control query families из oracle manifest.
- [ ] Для каждой projection сохранены query digest и canonical result digest.
- [ ] Сформирован marker scope `acceptance_observable_state`.
- [ ] Marker включает configuration, catalog, docs и projection digests.
- [ ] Expected scalar/set/order values записаны только после успешного capture.
- [ ] Повтор capture без изменений дает тот же marker/result digests.

## 5. Live skill и corpus checks

- [ ] Каждый data skill прошел positive, empty/error и metadata/schema checks.
- [ ] `single_select` и разрешенные typed invariant constants проверены live.
- [ ] Sequential и branching `linked_temp_batch` прошли одним MCP request.
- [ ] Даты, `ПОДОБНО`, virtual tables, refs и tabular sections проверены live.
- [ ] Required output facts имеют exact column bindings и MCP types.
- [ ] `success_empty`, `zero_aggregate`, `query_error` и `partial` различимы.
- [ ] Выполнены все 116 corpus scenarios с result record.
- [ ] Q001-Q090 first-attempt threshold рассчитан.
- [ ] Все 11 follow-up и 9 negative scenarios оценены отдельно.
- [ ] Q107 и end-to-end results всех 87 capabilities присутствуют.
- [ ] Повторный same-marker run сравнен по normalized facts.

## 6. Browser, diagnostics и failure injection

- [ ] Composer доступен при длинной истории; Enter/Shift+Enter работают.
- [ ] Reload/restart сохраняет session, messages, timestamps и context.
- [ ] SSE показывает progress длительной операции.
- [ ] Catalog import/export/replace/delete дают видимый accepted/rejected result.
- [ ] DeepSeek, MCP, query, contract и skill failures различаются в UI.
- [ ] После каждого injected failure следующий turn выполняется.
- [ ] Diagnostic ZIP проходит manifest/checksum и offline replay.
- [ ] Secret canary отсутствует в UI, logs и ZIP.
- [ ] Crash между catalog commit/swap восстанавливается на startup.

## 7. Performance и переносимость

- [x] Exact basic set зафиксирован: `Q001`, `Q011`, `Q031`, `Q041`, `Q051`, `Q062`, `Q071`, `Q081`, `Q102`, `Q107`.
- [ ] Basic set p100 <=30 s.
- [ ] Полный supported corpus `Q001-Q116` p95 <=90 s.
- [ ] Web и CLI import/export совпадают по revision/digests.
- [ ] Package перенесен между двумя clean application data dirs.
- [ ] Turn до replace завершился на старом snapshot, следующий на новом.
- [ ] macOS clean install/startup acceptance завершена.
- [ ] Тем же release/package выполнен Windows startup/FTS5/DeepSeek/MCP/chat/log/portability smoke.

## 8. Решение

- [ ] Все обязательные artifacts приложены к acceptance report.
- [ ] Нет открытых hard blockers из `release_gates.md`.
- [ ] Руководитель зафиксировал итоговое `pass` или мотивированный `blocked`.

До запуска MCP итог этого checklist остается `blocked_external`; synthetic
fixtures подтверждают форму контрактов, но не факты контрольной базы.
