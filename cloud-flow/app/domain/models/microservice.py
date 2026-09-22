"""Modelos que descrevem um microsservico e sua configuracao de execucao."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ContainerConfig(BaseModel):
    """Configuracao do container/imagem que executa o microsservico."""

    image: str = Field(..., min_length=1, description="Imagem do container, ex: ms-b:1.0.0")
    port: int = Field(..., gt=0, lt=65536, description="Porta exposta pelo container")
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("image")
    @classmethod
    def image_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("image nao pode ser vazia")
        return value


class HealthCheckConfig(BaseModel):
    """Configuracao do health check utilizado para validar o servico."""

    path: str = Field(default="/health", min_length=1)
    port: int = Field(..., gt=0, lt=65536)
    timeout_seconds: int = Field(default=5, gt=0)
    interval_seconds: int = Field(default=5, gt=0)


class MicroserviceConfig(BaseModel):
    """Configuracao completa de um microsservico a ser migrado."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    container: ContainerConfig
    health_check: HealthCheckConfig
    dependencies: list["Dependency"] = Field(default_factory=list)


from app.domain.models.dependency import Dependency  # noqa: E402

MicroserviceConfig.model_rebuild()
