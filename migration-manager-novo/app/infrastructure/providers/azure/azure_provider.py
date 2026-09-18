"""Implementacao de CloudProvider para Azure, apoiada em um ComputeProvider.

Esta classe traduz o contrato de alto nivel `CloudProvider` para o contrato
de execucao `ComputeProvider`, seguindo exatamente o mesmo padrao de
`AWSProvider`. Nenhuma logica de dominio depende desta classe diretamente -
ela e resolvida exclusivamente pela ProviderFactory.
"""
from __future__ import annotations

from app.domain.contracts.cloud_provider import CloudProvider
from app.domain.contracts.compute_provider import ComputeProvider
from app.domain.models.deployment import Deployment, HealthCheckResult
from app.domain.models.microservice import MicroserviceConfig
from app.domain.models.provider import ProviderConfig


class AzureProvider(CloudProvider):
    """Provedor de nuvem Azure (compativel com o emulador Floci-az).

    Delega a criacao/remocao/health-check do workload para um
    ComputeProvider concreto (ex: AzureContainerComputeProvider), mantendo
    este provider agnostico ao mecanismo exato de execucao.
    """

    def __init__(self, config: ProviderConfig, compute_provider: ComputeProvider) -> None:
        self._config = config
        self._compute_provider = compute_provider

    def deploy_service(self, config: MicroserviceConfig) -> Deployment:
        return self._compute_provider.deploy(config.container)

    def remove_service(self, deployment: Deployment) -> None:
        self._compute_provider.remove(deployment)

    def get_service_endpoint(self, deployment: Deployment) -> str:
        return self._compute_provider.get_endpoint(deployment)

    def health_check(self, deployment: Deployment) -> HealthCheckResult:
        return self._compute_provider.health_check(deployment)
