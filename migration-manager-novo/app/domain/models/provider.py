"""Modelos para configuracao de provedores de nuvem e ambiente."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums.provider_type import CloudProviderType


class ProviderConfig(BaseModel):
    """Configuracao de um provedor de nuvem para origem ou destino."""

    provider: CloudProviderType
    region: str = Field(..., min_length=1)
    environment: str = Field(..., min_length=1, description="Rotulo logico, ex: 'source' ou 'target'")
    endpoint_override: str | None = Field(
        default=None,
        description="Endpoint customizado (ex: emulador Floci). Resolvido via config externa/env.",
    )
    credentials_ref: str | None = Field(
        default=None,
        description="Referencia a um segredo externo (nunca a credencial em si).",
    )
