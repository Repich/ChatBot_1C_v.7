# ADR-0002: Атомарные hot-reload пакеты навыков

- Статус: accepted
- Дата: 2026-07-21

## Контекст

Навыки должны импортироваться, заменяться, удаляться и переноситься через web и
CLI без restart приложения. Поврежденный/несовместимый package не должен менять
рабочий каталог. Параллельный пользовательский запрос не должен увидеть половину
обновления или внезапно сменить skill version.

Опыт v5 показал скрытые побочные изменения, skill, который появлялся только при
повторе, и сложный lifecycle drafts/candidates.

## Решение

1. Skill/package являются canonical JSON documents с schema version, SemVer,
   compatibility, tests, provenance и RFC 8785/SHA-256 integrity.
2. Import имеет только два результата: accepted целиком или rejected без
   изменения. Draft/candidate/pending states отсутствуют.
3. `(skill_id, version, digest)` immutable. Та же ID/version с другим digest
   запрещена.
4. Create/replace/delete intent задается API/CLI command. Replace/delete требуют
   optimistic `If-Match`/expected current digest.
5. Весь validation pipeline выполняется до write transaction. Одна SQLite
   transaction записывает documents, полный active mapping, dependencies и
   следующую monotonic catalog revision.
6. После commit строится immutable in-memory snapshot и меняется одной atomic
   reference. Каждый turn pin-ит snapshot до планирования и держит его до конца.
7. Startup/revision watcher восстанавливает in-memory revision, если process
   завершился после commit, но до swap.
8. Web и CLI вызывают один application use case и возвращают одинаковые
   revision/digests.

## Validation до commit

- JSON limits/parse и Draft 2020-12 schemas;
- hard-reject внешнего documentation `source_kind` в schema v1;
- embedded и package checksums;
- semantic mappings, uniqueness и portable-value lint;
- query read-only/static checks;
- current database compatibility/metadata assertions;
- dependency DAG и version conflicts;
- positive/negative portable fixtures;
- replacement preconditions.

Если текущую совместимость нельзя проверить и нет profile этого process/database,
import отклоняется. Невалидный JSON не сохраняется как candidate.

## Последствия

Положительные:

- следующий turn видит новый каталог без restart;
- текущий turn воспроизводим на старом snapshot;
- нет partially active package;
- конфликт/rollback понятны по revision/digest;
- historical trace сохраняет exact skill documents после delete.

Отрицательные:

- active mapping дублируется по revision, что приемлемо для небольшого MVP;
- large package validation может занять время, поэтому выполняется вне write
  transaction и имеет size/time limits;
- исправление skill требует новой version, даже если меняется одна строка query;
- checksum не удостоверяет автора; signing отложен.

## Отклоненные альтернативы

### File watcher над каталогом JSON

Отклонено: наблюдает промежуточную запись, различается на macOS/Windows, не дает
общую transaction package/dependencies и сложнее восстанавливается после crash.

### Mutable global registry

Отклонено: turn может увидеть разные versions между planner и executor; rollback
и диагностика не воспроизводимы.

### Restart после импорта

Отклонено прямым FR-040/AC-009 и ухудшает локальную эксплуатацию.

### Multi-state draft/candidate lifecycle

Отклонено как лишнее для локального MVP. Авторинг и review происходят вне
runtime; runtime принимает готовый tested artifact.

### Неявная замена по большему SemVer

Отклонено: может случайно сломать рабочий каталог. Replace всегда explicit и с
expected digest.

## Проверка решения

- Поврежденный package оставляет revision/digests неизменными.
- Concurrent turns до/после replace используют разные целые snapshots.
- Crash injection между commit/swap восстанавливается на startup.
- Dependency cycle/missing dependency/delete-in-use отклоняются.
- Один package проходит web export/import и CLI import/export между двумя
  чистыми `APP_DATA_DIR` с одинаковыми digests.
