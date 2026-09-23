"""Modelos relacionados a migracao de dados."""
from __future__ import annotations

from pydantic import BaseModel, Field
from datetime import datetime

from app.domain.enums.provider_type import DataEngineType


class DataEndpointConfig(BaseModel):
    """Endpoint de conexao com uma base de dados (origem ou destino)."""

    host: str = Field(..., min_length=1)
    port: int = Field(..., gt=0, lt=65536)
    database: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1, repr=False)
    resource_id: str | None = Field(
        default=None,
        description="Identificador do recurso gerenciado (RDS ou Flexible Server).",
    )


# Aliases semanticos pedidos no prompt (DataSourceConfig / DataTargetConfig)
class DataSourceConfig(DataEndpointConfig):
    """Endpoint de origem dos dados a serem migrados."""


class DataTargetConfig(DataEndpointConfig):
    """Endpoint de destino dos dados migrados."""


class DataMigrationConfig(BaseModel):
    """Configuracao completa de migracao de dados de um microsservico."""

    type: DataEngineType
    source: DataSourceConfig
    target: DataTargetConfig


class DataMigrationResult(BaseModel):
    """Resultado da execucao da migracao de dados."""

    success: bool
    records_migrated: int = Field(default=0, ge=0)
    started_at: str | None = None
    finished_at: str | None = None
    message: str | None = None
    downtime_seconds: float | None = None
    downtime_started_at: datetime | None = None
    downtime_finished_at: datetime | None = None


class ValidationResult(BaseModel):
    """Resultado de uma etapa de validacao (servico, dados ou aplicacao)."""

    success: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    message: str | None = None
