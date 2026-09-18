"""Implementacao inicial de ComputeProvider para o ambiente experimental Azure.

Esta implementacao (`AzureContainerComputeProvider`) simula a criacao de
containers (analogo a Azure Container Instances) localmente, servindo como
stub compativel com o ambiente Floci-az (emulador Azure) enquanto uma
integracao real com Azure Container Instances/AKS nao e adicionada. Segue
exatamente o mesmo padrao de `DockerComputeProvider` (AWS), cumprindo o
contrato `ComputeProvider` por completo, permitindo que o restante do
sistema seja desenvolvido e testado sem depender de infraestrutura real.
"""
from __future__ import annotations

import itertools
import os
import uuid

from app.domain.contracts.compute_provider import ComputeProvider
from app.domain.enums.provider_type import HealthStatus
from app.domain.models.deployment import Deployment, HealthCheckResult
from app.domain.models.microservice import ContainerConfig
from app.domain.models.provider import ProviderConfig

# Base de portas distinta da usada pelo DockerComputeProvider (AWS) para que
# deployments simulados de ambos os provedores possam coexistir no mesmo
# processo (ex: testes que exercitam migracao AWS -> Azure) sem colidir.
_port_counter = itertools.count(31000)


class AzureContainerComputeProvider(ComputeProvider):
    """Compute provider experimental compativel com o Floci-az (emulador Azure).

    Nao acopla o dominio ao Azure SDK diretamente: toda a configuracao de
    endpoint (ex: `AZURE_ENDPOINT_URL`) e resolvida a partir de variaveis de
    ambiente/config externa, nunca hardcoded nas classes de dominio.
    """

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._endpoint_url = config.endpoint_override or os.getenv(
            "AZURE_ENDPOINT_URL", "http://localhost:4577"
        )
        self._deployments: dict[str, Deployment] = {}

    def deploy(self, config: ContainerConfig) -> Deployment:
        deployment_id = f"deploy-{uuid.uuid4().hex[:8]}"
        host_port = next(_port_counter)
        deployment = Deployment(
            deployment_id=deployment_id,
            service_id=config.image.split(":")[0],
            endpoint=f"http://localhost:{host_port}",
            provider="azure",
            region=self._config.region,
            metadata={
                "image": config.image,
                "container_port": str(config.port),
                "floci_endpoint": self._endpoint_url,
            },
        )
        self._deployments[deployment_id] = deployment
        return deployment

    def start(self, deployment: Deployment) -> None:
        self._deployments[deployment.deployment_id] = deployment

    def stop(self, deployment: Deployment) -> None:
        # Stub: em uma implementacao real, pararia o container/task.
        return None

    def remove(self, deployment: Deployment) -> None:
        self._deployments.pop(deployment.deployment_id, None)

    def get_endpoint(self, deployment: Deployment) -> str:
        return deployment.endpoint or ""

    def health_check(self, deployment: Deployment) -> HealthCheckResult:
        is_known = deployment.deployment_id in self._deployments
        status = HealthStatus.HEALTHY if is_known else HealthStatus.UNKNOWN
        return HealthCheckResult(status=status, detail="stub health check (Floci-az)")
