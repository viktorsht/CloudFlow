"""Handles internos para bancos provisionados durante uma migracao."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseConnection:
    """Conexao resolvida. Nunca deve ser serializada em eventos ou respostas."""

    host: str
    port: int
    database: str
    username: str
    password: str
    resource_id: str
    provider: str
