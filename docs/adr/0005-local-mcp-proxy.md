# ADR-0005: Локальный MCP proxy для обработки 1С

- Статус: accepted
- Дата: 2026-07-21
- Область: integration, installation, live acceptance

## Контекст

Обработка `MCP_Toolkit.epf` в proxy-режиме не поднимает HTTP server внутри 1С.
Она опрашивает внешний bridge по `/1c/poll` и возвращает результат в
`/1c/result`. Поэтому один `MCP_URL=http://127.0.0.1:6003/mcp` в chatbot не
создает работающую интеграцию: на этом адресе должен быть запущен proxy.

Совместимый протокол подтвержден локальной копией `ROCTUP/1c-mcp-toolkit`
версии 1.7.0 и ранее работавшей обработкой. Исходная копия содержит много
неиспользуемых инструментов, REST/SSE/TOON/анонимизацию, не различает готовность
proxy и подключение 1С, а ее лицензия и git provenance локально не закреплены.
Копировать эту реализацию в продукт нельзя.

## Решение

ChatBot v7 получает собственный минимальный package `chatbot1c.mcp_proxy`,
реализованный по наблюдаемому wire protocol без копирования стороннего кода.

### 1. Маршруты

```text
POST|GET|DELETE /mcp?channel=<channel>
GET              /1c/poll?channel=<channel>
POST             /1c/result?channel=<channel>
GET              /health/live
GET              /health/ready?channel=<channel>
```

`/mcp` использует официальный MCP Streamable HTTP transport. Server публикует
ровно два tools: `execute_query` и `get_metadata`. REST API, legacy SSE,
`execute_code`, event log, object mutation, screenshot и session control не
входят в процесс и не регистрируются.

### 2. Wire между proxy и 1С

Команда:

```json
{
  "id": "opaque-command-id",
  "tool": "execute_query",
  "params": {}
}
```

Результат:

```json
{
  "id": "opaque-command-id",
  "success": true,
  "data": [],
  "schema": {"columns": []},
  "count": 0
}
```

Формат всегда JSON/UTF-8. TOON и автоматическое угадывание wrapper-форм
запрещены. Channel ID должен совпадать у chatbot и обработки 1С; default
`default` является явным значением, а не пустой строкой.

### 3. Очередь и состояния

Каждая команда проходит конечный автомат:

```text
queued -> leased_to_1c -> completed
                    \-> expired | cancelled
```

- command ID уникален в channel;
- поздний, повторный или неизвестный result отклоняется;
- timeout, disconnect и cancellation атомарно удаляют pending command;
- expired/cancelled command больше не выдается poll;
- один worker/process владеет in-memory queue в MVP;
- restart не обещает продолжить активные команды, но завершает их как transport
  unavailable, а не query error.

### 4. Ошибки и readiness

- `/health/live` подтверждает только работающий process/event loop;
- `/health/ready` возвращает `proxy_ready` и `one_c_connected` отдельно;
- `one_c_connected=true` только пока свежий успешный poll данного channel
  находится внутри configured heartbeat window;
- отсутствие poll, transport timeout и cancellation становятся MCP transport
  error/HTTP 503 или 504;
- валидный `success=false` от 1С остается query error и не смешивается с
  недоступностью bridge;
- chatbot retry policy остается единственным владельцем retries.

### 5. Лимиты

- bind default `127.0.0.1:6003`;
- один process worker;
- allowlist channel format и bounded channel count;
- request/result проходят общий bounded JSON loader;
- result не более 16 MiB, 1000 rows, depth 32 и 100000 nodes;
- pending commands/channel, poll wait и command lifetime ограничены;
- raw payload пишется только в локальную диагностику chatbot.

### 6. Совместимость адаптера

`ReadOnly1CPort.get_metadata` имеет локальное поле `mode`, используемое для
валидации ответа. Proxy/Toolkit его не принимает. MCP adapter формирует wire
payload по explicit allowlist полей и не отправляет `mode`.

Timeout proxy должен быть немного меньше соответствующего stage deadline
chatbot, чтобы bridge успел вернуть однозначную transport error и очистить
команду до клиентской cancellation.

## Acceptance

Fixture acceptance не требует запущенной 1С и проверяет:

1. MCP initialize/tools-list с ровно двумя tools.
2. Полный `call_tool -> poll -> result -> MCP response`.
3. Rows, confirmed empty, query error, malformed schema, null и `_objectRef`.
4. Summary/list/detail metadata; локальное `mode` отсутствует в wire params.
5. Изоляцию channels.
6. Timeout/cancellation/late/duplicate result и restart.
7. Отдельные liveness/readiness и устаревший poll heartbeat.
8. Кириллицу и одинаковый JSON wire на macOS/Windows.

Live gate остается отдельным и требует открытой обработки 1С:

- metadata summary/detail;
- простой parameterized SELECT;
- linked temporary-table batch;
- query error отдельно от transport timeout;
- тот же channel и release artifact на macOS/Windows.

## Последствия

- отсутствие proxy больше не является скрытой предпосылкой установки;
- chatbot можно полностью тестировать через simulated 1C poller;
- внешняя обработка `MCP_Toolkit.epf` остается отдельным бинарным dependency;
- live acceptance нельзя объявить пройденной, пока реальный сеанс 1С не
  подключился к `/1c/poll`.
