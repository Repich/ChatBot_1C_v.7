"""Public ASGI factory for the local 1C MCP proxy."""

from .app import create_app

__all__ = ["create_app"]
