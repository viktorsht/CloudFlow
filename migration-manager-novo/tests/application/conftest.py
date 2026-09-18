"""Fixtures e fakes (test doubles) para os contratos de dominio, usados nos
testes do MigrationManager e das estrategias de migracao.
"""
from __future__ import annotations

import pytest

from app.domain.contracts.cloud_provider import CloudProvider
from app.domain.contracts.data_migration_provider import DataMigrationProvider
from app.domain.contracts.traffic_provider import TrafficProvider
from app.domain.contracts.validation_provider import ValidationProvider
from app.domain.enums.provider_type import HealthStatus
from app.domain.models.data import (
    DataMigrationResult,
    DataSourceConfig,
    DataTargetConfig,
    ValidationResult,
)
from app.domain.models.dependency import DependencyGraph
from app.domain.models.deployment import Deployment, HealthCheckResult
from app.domain.models.microservice import (
    ContainerConfig,
    HealthCheckConfig,
    MicroserviceConfig,
)
from app.domain.models.migration import MigrationRequest
from app.domain.models.provider import ProviderConfig


class FakeCloudProvider(CloudProvider):
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []
        self.deployed: Deployment | None = None
        self.removed: list[Deployment] = []

    def deploy_service(self, config: MicroserviceConfig) -> Deployment:
        self.calls.append("deploy_service")
        self.deployed = Deployment(
            deployment_id=f"{self.name}-deploy",
            service_id=config.id,
            endpoint=f"http://{self.name}/{config.id}",
            provider=self.name,
        )
        return self.deployed

    def remove_service(self, deployment: Deployment) -> None:
        self.calls.append("remove_service")
        self.removed.append(deployment)

    def get_service_endpoint(self, deployment: Deployment) -> str:
        self.calls.append("get_service_endpoint")
        return deployment.endpoint or ""

    def health_check(self, deployment: Deployment) -> HealthCheckResult:
        self.calls.append("health_check")
        return HealthCheckResult(status=HealthStatus.HEALTHY)


class FakeDataMigrationProvider(DataMigrationProvider):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.rolled_back = False

    def prepare_source(self, source: DataSourceConfig) -> None:
        self.calls.append("prepare_source")

    def prepare_target(self, target: DataTargetConfig) -> None:
        self.calls.append("prepare_target")

    def migrate(self, source: DataSourceConfig, target: DataTargetConfig) -> DataMigrationResult:
        self.calls.append("migrate")
        return DataMigrationResult(success=True, records_migrated=10)

    def validate(self, source: DataSourceConfig, target: DataTargetConfig) -> ValidationResult:
        self.calls.append("validate")
        return ValidationResult(success=True)

    def cleanup(self, source: DataSourceConfig) -> None:
        self.calls.append("cleanup")

    def rollback(self, target: DataTargetConfig) -> None:
        self.calls.append("rollback")
        self.rolled_back = True


class FakeTrafficProvider(TrafficProvider):
    def __init__(self, fail_redirect: bool = False) -> None:
        self.calls: list[str] = []
        self.routes: dict[str, str] = {}
        self.fail_redirect = fail_redirect

    def get_current_route(self, service_id: str) -> str:
        self.calls.append("get_current_route")
        return self.routes.get(service_id, "http://source/ms-b")

    def redirect(self, service_id: str, target_endpoint: str) -> None:
        self.calls.append("redirect")
        if self.fail_redirect:
            raise RuntimeError("Falha simulada ao redirecionar trafego")
        self.routes[service_id] = target_endpoint

    def validate_route(self, service_id: str, expected_endpoint: str) -> bool:
        self.calls.append("validate_route")
        return self.routes.get(service_id) == expected_endpoint

    def restore(self, service_id: str, previous_endpoint: str) -> None:
        self.calls.append("restore")
        self.routes[service_id] = previous_endpoint


class FakeValidationProvider(ValidationProvider):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_service(self, deployment: Deployment) -> ValidationResult:
        self.calls.append("validate_service")
        return ValidationResult(success=True)

    def validate_data(self, source: DataSourceConfig, target: DataTargetConfig) -> ValidationResult:
        self.calls.append("validate_data")
        return ValidationResult(success=True)

    def validate_application(
        self, service_id: str, deployment: Deployment, dependency_graph: DependencyGraph
    ) -> ValidationResult:
        self.calls.append("validate_application")
        return ValidationResult(success=True)


@pytest.fixture
def migration_request() -> MigrationRequest:
    return MigrationRequest.model_validate(
        {
            "migration_id": "migration-ms-b-001",
            "microservice": {
                "id": "ms-b",
                "name": "ms-b",
                "container": {"image": "ms-b:1.0.0", "port": 8080, "environment": {}},
                "health_check": {"path": "/health", "port": 8080},
                "dependencies": [],
            },
            "source": {"provider": "aws", "region": "us-east-1", "environment": "source"},
            "target": {"provider": "aws", "region": "us-east-1", "environment": "target"},
            "data": {
                "type": "postgresql",
                "source": {"host": "source-db", "port": 5432, "database": "ms_b"},
                "target": {"host": "target-db", "port": 5432, "database": "ms_b"},
            },
        }
    )
