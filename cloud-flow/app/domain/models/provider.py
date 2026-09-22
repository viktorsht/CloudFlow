"""Modelos para configuracao de provedores de nuvem e ambiente."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.domain.enums.provider_type import CloudProviderType


class AwsCredentials(BaseModel):
    access_key_id: str = Field(..., min_length=1)
    secret_access_key: str = Field(..., min_length=1, repr=False)
    session_token: str | None = Field(default=None, repr=False)


class AzureCredentials(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1, repr=False)
    subscription_id: str = Field(..., min_length=1)


class ProviderConfig(BaseModel):
    """Configuracao de um provedor de nuvem para origem ou destino."""

    provider: CloudProviderType
    region: str = Field(..., min_length=1)
    environment: str = Field(..., min_length=1, description="Rotulo logico, ex: 'source' ou 'target'")
    endpoint: str = Field(
        ...,
        min_length=1,
        description="Endpoint da API do provedor, inclusive de emuladores quando usados.",
    )
    credentials: AwsCredentials | AzureCredentials
    runtime_options: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Parametros neutros de runtime. Exemplos: cluster/task_arn para ECS; "
            "subscription_id/resource_group/managed_environment para Azure."
        ),
    )

    @model_validator(mode="after")
    def credentials_must_match_provider(self) -> "ProviderConfig":
        expected = AwsCredentials if self.provider is CloudProviderType.AWS else AzureCredentials
        if not isinstance(self.credentials, expected):
            raise ValueError(f"credentials incompativel com provider={self.provider.value}")
        return self
