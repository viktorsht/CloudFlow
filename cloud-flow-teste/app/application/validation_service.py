"""Servico de aplicacao que coordena as chamadas de validacao durante a migracao."""
from __future__ import annotations

from app.domain.contracts.validation_provider import ValidationProvider
from app.domain.models.data import DataSourceConfig, DataTargetConfig, ValidationResult
from app.domain.models.dependency import DependencyGraph
from app.domain.models.deployment import Deployment


class ValidationService:
    """Encapsula a orquestracao das validacoes de servico, dados e aplicacao,
    delegando a execucao real para um ValidationProvider concreto.
    """

    def __init__(self, provider: ValidationProvider) -> None:
        self._provider = provider

    def validate_target_service(self, deployment: Deployment) -> ValidationResult:
        return self._provider.validate_service(deployment)

    def validate_data(
        self, source: DataSourceConfig, target: DataTargetConfig
    ) -> ValidationResult:
        return self._provider.validate_data(source, target)

    def validate_application(
        self,
        service_id: str,
        deployment: Deployment,
        dependency_graph: DependencyGraph,
    ) -> ValidationResult:
        return self._provider.validate_application(service_id, deployment, dependency_graph)
