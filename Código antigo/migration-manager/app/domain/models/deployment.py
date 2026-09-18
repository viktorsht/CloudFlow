"""Modelos que representam a implantacao (deployment) de um microsservico."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums.provider_type import HealthStatus


class Deployment(BaseModel):
    """Representa uma implantacao concreta de um microsservico em um provedor.

    E o "handle" que o dominio usa para se referir a um workload em execucao,
    sem conhecer os detalhes de como ele foi criado (Docker, ECS, VM, etc).
    """

    deployment_id: str = Field(..., min_length=1)
    service_id: str = Field(..., min_length=1)
    endpoint: str | None = None
    provider: str | None = None
    region: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class RollbackResult(BaseModel):
    """Resultado da execucao de um rollback."""

    success: bool
    restored_route: bool = False
    target_removed: bool = False
    message: str | None = None


class HealthCheckResult(BaseModel):
    """Resultado de uma verificacao de saude de um deployment."""

    status: HealthStatus
    detail: str | None = None
