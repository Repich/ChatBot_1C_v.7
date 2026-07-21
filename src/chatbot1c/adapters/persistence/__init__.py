"""SQLite persistence backed by versioned Alembic migrations."""

from chatbot1c.adapters.persistence.sqlite import SQLiteStore

__all__ = ["SQLiteStore"]
