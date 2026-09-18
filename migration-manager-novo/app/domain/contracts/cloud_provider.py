"""Contrato de alto nivel para um provedor de nuvem (AWS, Azure, GCP...)."""
from abc import ABC, abstractmethod

from app.domain.models.deployment import Deployment, HealthCheckResult
from app.domain.models.microservice import MicroserviceConfig


class CloudProvider(ABC):
    """Representa as operacoes de alto nivel necessarias para operar um
    microsservico em um provedor de nuvem especifico.

    Internamente, uma implementacao concreta (ex: AWSProvider) tipicamente
    delega para ComputeProvider, DataMigrationProvider e TrafficProvider,
    mas o MigrationManager so conhece este contrato.
    """

    @abstractmethod
    def deploy_service(self, config: MicroserviceConfig) -> Deployment:
        """Implanta o microsservico neste provedor."""

    @abstractmethod
    def remove_service(self, deployment: Deployment) -> None:
        """Remove o microsservico implantado neste provedor."""

    @abstractmethod
    def get_service_endpoint(self, deployment: Deployment) -> str:
        """Retorna o endpoint publico/interno do servico implantado."""

    @abstractmethod
    def health_check(self, deployment: Deployment) -> HealthCheckResult:
        """Verifica a saude do servico implantado."""
