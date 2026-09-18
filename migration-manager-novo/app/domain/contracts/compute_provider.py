"""Contrato para provedores de execucao/compute (Docker, ECS, VM, Kubernetes...)."""
from abc import ABC, abstractmethod

from app.domain.models.deployment import Deployment, HealthCheckResult
from app.domain.models.microservice import ContainerConfig
from app.domain.models.migration import WorkloadReference


class ComputeProvider(ABC):
    """Abstrai o mecanismo utilizado para executar um microsservico.

    O dominio nunca deve saber se, por baixo, isso e Docker, ECS, uma VM ou
    Kubernetes: apenas interage com este contrato.
    """

    @abstractmethod
    def deploy(self, config: ContainerConfig) -> Deployment:
        """Cria e inicia a execucao de um container a partir da configuracao."""

    @abstractmethod
    def start(self, deployment: Deployment) -> None:
        """Inicia um deployment existente (caso esteja parado)."""

    @abstractmethod
    def stop(self, deployment: Deployment) -> None:
        """Para a execucao de um deployment sem remove-lo."""

    @abstractmethod
    def remove(self, deployment: Deployment) -> None:
        """Remove definitivamente um deployment."""

    @abstractmethod
    def get_endpoint(self, deployment: Deployment) -> str:
        """Retorna o endpoint de rede utilizado para acessar o deployment."""

    @abstractmethod
    def health_check(self, deployment: Deployment) -> HealthCheckResult:
        """Executa uma verificacao de saude sobre o deployment."""

    @abstractmethod
    def discover(self, reference: WorkloadReference) -> Deployment:
        """Resolve um workload que foi criado antes desta execucao."""
