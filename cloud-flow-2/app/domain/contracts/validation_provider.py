"""Contrato para validacao de servico, dados e aplicacao apos a migracao."""
from abc import ABC, abstractmethod

from app.domain.models.data import DataSourceConfig, DataTargetConfig, ValidationResult
from app.domain.models.deployment import Deployment
from app.domain.models.dependency import DependencyGraph


class ValidationProvider(ABC):
    """Abstrai as verificacoes de validacao necessarias durante a migracao.

    Cobre tres dimensoes: o servico implantado, os dados migrados e o
    comportamento da aplicacao como um todo apos o redirecionamento.
    """

    @abstractmethod
    def validate_service(self, deployment: Deployment) -> ValidationResult:
        """Valida que o servico esta executando, saudavel e acessivel.

        Deve cobrir, no minimo: processo em execucao, endpoint disponivel,
        health check e porta disponivel.
        """

    @abstractmethod
    def validate_data(
        self, source: DataSourceConfig, target: DataTargetConfig
    ) -> ValidationResult:
        """Valida os dados migrados: acessibilidade, contagem e integridade basica."""

    @abstractmethod
    def validate_application(
        self,
        service_id: str,
        deployment: Deployment,
        dependency_graph: DependencyGraph,
    ) -> ValidationResult:
        """Valida a aplicacao como um todo apos o redirecionamento de trafego:
        dependencias acessiveis, chamadas entre microsservicos, rota correta
        e ausencia de erros criticos.
        """
