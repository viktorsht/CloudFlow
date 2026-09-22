"""Modelos que representam a solicitacao, o plano e o resultado de uma migracao."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.domain.enums.migration_state import MigrationState
from app.domain.enums.provider_type import OperationStatus
from enum import Enum
from app.domain.models.data import DataMigrationConfig
from app.domain.models.microservice import MicroserviceConfig
from app.domain.models.provider import ProviderConfig


class WorkloadReference(BaseModel):
    """Referencia suficiente para encontrar e controlar o workload de origem."""

    resource_id: str = Field(..., min_length=1)
    container_name: str | None = None
    port: int | None = Field(default=None, gt=0, lt=65536)
    container_only: bool = Field(
        default=False,
        description="Controla apenas o container quando a task ECS legada agrupa servicos.",
    )


class IngressConfig(BaseModel):
    """Configuracao da rota publica administrada pelo Spring Gateway neutro."""

    gateway_admin_url: str = Field(..., min_length=1)
    route_id: str = Field(..., min_length=1)
    public_path: str = Field(..., min_length=1)


class MigrationMode(str, Enum):
    CONTINUOUS = "continuous"
    STOP_AND_MIGRATE = "stop_and_migrate"


class MigrationRequest(BaseModel):
    """Especificacao completa de uma migracao, recebida pela API.

    Este e o unico ponto de entrada externo do MigrationManager: tudo que o
    orquestrador precisa saber sobre a migracao deve estar contido aqui.
    """

    migration_id: str = Field(..., min_length=1)
    mode: MigrationMode = MigrationMode.CONTINUOUS
    microservice: MicroserviceConfig
    source: ProviderConfig
    target: ProviderConfig
    data: DataMigrationConfig
    source_workload: WorkloadReference | None = None
    ingress: IngressConfig | None = None


class MigrationEvent(BaseModel):
    """Um evento de observabilidade emitido durante a execucao da migracao."""

    migration_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    state: MigrationState
    operation: str
    status: OperationStatus
    message: str | None = None
    duration_ms: float | None = None


class MigrationPlan(BaseModel):
    """Plano de execucao gerado a partir de um MigrationRequest validado.

    Representa o resultado da etapa de "prepare": a solicitacao original
    acompanhada de metadados de planejamento (ex: ordem de dependencias).
    """

    migration_id: str
    request: MigrationRequest
    dependency_order_notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MigrationResult(BaseModel):
    """Resultado final (sucesso ou falha) da execucao de uma migracao."""

    migration_id: str
    success: bool
    final_state: MigrationState
    events: list[MigrationEvent] = Field(default_factory=list)
    message: str | None = None
