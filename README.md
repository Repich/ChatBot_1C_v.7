# ChatBot 1C v7

Contract-first ассистент для `1С:Управление торговлей 11.5.27.56`. Текущая
версия реализует первые три вертикальных среза: локальный русскоязычный web-чат,
DeepSeek planner, read-only MCP execution, встроенную справку УТ, SQLite
persistence и переносимый JSON-каталог навыков. Ядро различает успешные,
пустые, частичные и ошибочные результаты, проверяет полноту evidence и
поддерживает безопасное продолжение keyset-списков без перезапуска приложения.
Типизированные resolver-навыки сохраняют только явно выбранные сущности и
подтвержденные фильтры, умеют запрашивать уточнение при неоднозначности и
переиспользуют opaque session context без передачи внутренних UUID в DeepSeek.
Встроенный каталог третьего среза содержит 39 навыков: типизированные
справочные resolvers, точные потребители реквизитов, производители документов
и базовые навыки остатков/заказов. Детерминированный оператор ранжирования
выбирает один объект только по полному, сопоставимому набору фактов и не
подменяет неоднозначность случайным результатом.

## Запуск

Требуются Python 3.12 и [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --all-groups
cp .env.example .env.local
uv run --locked chatbot1c start
```

По умолчанию сервер доступен на `http://127.0.0.1:8000`. В `.env.local` нужно
указать `DEEPSEEK_API_KEY`, URL read-only MCP и путь к файловой выгрузке УТ.
Сервис не имеет аутентификации и предназначен для локального запуска.

SQLite создается в `APP_DATA_DIR`, включается в WAL mode и обновляется только
versioned Alembic migrations. Встроенный starter catalog можно загрузить при
старте через `AUTO_IMPORT_BUILTIN_SKILLS=true`.

## CLI

```bash
# Построить индекс Ext/Help/ru.html из файловой выгрузки конфигурации
uv run --locked chatbot1c docs build-index --config-dir /path/to/config

# Проверить и атомарно импортировать skill или package
uv run --locked chatbot1c skills validate skill-or-package.json
uv run --locked chatbot1c skills import skill-or-package.json --mode create

# Экспортировать bare skill или self-contained dependency closure
uv run --locked chatbot1c skills export SKILL_ID --output skill.json
uv run --locked chatbot1c skills export SKILL_ID --with-dependencies --output package.json

# Экспортировать весь активный catalog
uv run --locked chatbot1c skills export --all --output catalog.package.json
```

## Проверки

```bash
uv lock --check
uv run --locked pytest
uv run --locked ruff check .
uv run --locked mypy --strict src
uv build
```

Детерминированный black-box E2E запускает два экземпляра приложения и fixture
DeepSeek/MCP transport без внешних секретов:

```bash
uv run --locked python scripts/run_slice1_e2e.py
```

Документы проекта:

- [план проекта](docs/project_plan.md);
- [продуктовые требования](docs/requirements/product_requirements.md);
- [каталог возможностей](docs/requirements/skill_catalog.md);
- [критерии приемки](docs/requirements/acceptance_criteria.md);
- [архитектура](docs/architecture/architecture.md);
- [контракт переносимого навыка](docs/architecture/skill_contract.md);
- [порядок реализации](docs/architecture/implementation_slices.md);
- [стратегия тестирования](docs/testing/test_strategy.md);
- [release gates](docs/testing/release_gates.md);
- [исходные допущения](docs/assumptions.md);
- [реестр исходных источников](docs/source_inventory.md).
