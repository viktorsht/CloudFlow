"""Implementacao inicial (stub) de ValidationProvider para AWS/Floci."""
from __future__ import annotations

import httpx

from app.domain.contracts.validation_provider import ValidationProvider
from app.domain.models.data import DataSourceConfig, DataTargetConfig, ValidationResult
from app.domain.models.dependency import DependencyGraph
from app.domain.models.deployment import Deployment
from app.domain.models.provider import ProviderConfig


class AWSValidationProvider(ValidationProvider):
    """Validacoes experimentais/stub, uteis para exercitar o fluxo completo
    do MigrationManager antes de integrar checagens reais (HTTP, SQL, etc).
    """

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    def validate_service(self, deployment: Deployment) -> ValidationResult:
        try:
            response = httpx.get(f"{(deployment.endpoint or '').rstrip('/')}{deployment.metadata.get('health_path', '/health')}", timeout=5)
            is_deployed = response.is_success
        except httpx.HTTPError:
            is_deployed = False
        return ValidationResult(
            success=is_deployed,
            checks={
                "process_running": is_deployed,
                "endpoint_available": is_deployed,
                "health_check": is_deployed,
            },
            message="Validacao HTTP do servico AWS",
        )

    def validate_data(
        self, source: DataSourceConfig, target: DataTargetConfig
    ) -> ValidationResult:
        return ValidationResult(
            success=True,
            checks={"database_accessible": True, "record_count": True, "integrity": True},
            message="Validacao de dados (stub)",
        )

    def validate_application(
        self,
        service_id: str,
        deployment: Deployment,
        dependency_graph: DependencyGraph,
    ) -> ValidationResult:
        dependencies_ok = dependency_graph.validate_dependencies(service_id)
        return ValidationResult(
            success=dependencies_ok,
            checks={
                "dependencies_reachable": dependencies_ok,
                "route_correct": True,
                "no_critical_errors": True,
            },
            message="Validacao de aplicacao (stub)",
        )
