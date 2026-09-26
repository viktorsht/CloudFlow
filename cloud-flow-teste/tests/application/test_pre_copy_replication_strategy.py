"""PreCopyReplicationStrategy via MigrationManager, com providers falsos."""
from __future__ import annotations

import time

import pytest

from app.application.migration_manager import MigrationManager
from app.domain.enums.migration_state import MigrationState
from app.domain.enums.provider_type import HealthStatus
from app.domain.models.data import DataMigrationResult, ReplicationLagResult, ValidationResult
from app.domain.models.database import DatabaseConnection
from app.domain.models.deployment import Deployment, HealthCheckResult
from app.domain.models.migration import MigrationMode, MigrationRequest
from app.infrastructure.providers.factory import ProviderFactory

# Tempo simulado de dump/restore do banco inteiro.
FULL_COPY_SECONDS = 0.2


class Env:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.fail: set[str] = set()
        self.data_mode: MigrationMode | None = None

    def step(self, name: str) -> None:
        self.log.append(name)
        if name in self.fail:
            raise RuntimeError(f"falha simulada em {name}")


class FakeCloud:
    def __init__(self, role: str, env: Env) -> None:
        self.role, self.env = role, env

    def discover_service(self, reference):
        return Deployment(deployment_id=reference.resource_id, service_id="ms2", endpoint="http://source")

    def deploy_service(self, config):
        self.env.step("target:deploy")
        return Deployment(deployment_id="target-1", service_id=config.id, endpoint="http://target")

    def health_check(self, deployment):
        return HealthCheckResult(status=HealthStatus.HEALTHY)

    def stop_service(self, deployment):
        self.env.step("source:stop")

    def start_service(self, deployment):
        self.env.step("source:start")

    def remove_service(self, deployment):
        self.env.step("target:remove")


class FakeDatabase:
    def __init__(self, role: str, env: Env) -> None:
        self.role, self.env = role, env

    def _connection(self, config) -> DatabaseConnection:
        return DatabaseConnection(config.host, 5432, config.database, config.username, config.password, f"{self.role}-db", "fake")

    def discover(self, config):
        return self._connection(config)

    def provision(self, config):
        self.env.step("target-db:provision")
        return self._connection(config)

    def remove(self, connection):
        self.env.step("target-db:remove")


class FakeData:
    """Implementa tanto o dump/restore classico quanto a interface de replicacao."""

    def __init__(self, env: Env) -> None:
        self.env = env

    def migrate_connections(self, source, target):
        self.env.step("data:dump_restore")
        time.sleep(FULL_COPY_SECONDS)
        return DataMigrationResult(success=True)

    def prepare_source_connection(self, source):
        self.env.step("data:check_prerequisites")

    def base_copy(self, source, target, replication_name, options):
        self.env.step("data:base_copy")
        time.sleep(FULL_COPY_SECONDS)
        return DataMigrationResult(success=True)

    def start_replication(self, source, target, replication_name, options):
        self.env.step("replication:start")

    def wait_until_synced(self, source, replication_name, options):
        self.env.step("replication:wait")
        if "replication:timeout" in self.env.fail:
            raise TimeoutError("Replicacao nao sincronizou em 600s")
        return ReplicationLagResult(lag_bytes=0, slot_active=True, synced=True)

    def drain_final_delta(self, source, target, replication_name, options):
        self.env.step("data:drain")
        return DataMigrationResult(success=True)

    def stop_replication(self, source, target, replication_name):
        self.env.step("replication:stop")

    def validate_connections(self, source, target):
        self.env.step("data:validate")
        return ValidationResult(success=True)

    def rollback(self, target):
        pass


class FakeTraffic:
    def __init__(self, env: Env) -> None:
        self.env = env

    def get_current_route(self, service_id):
        return "http://source"

    def enable_maintenance(self, service_id):
        self.env.step("maintenance:on")

    def disable_maintenance(self, service_id):
        self.env.step("maintenance:off")

    def redirect_deployment(self, service_id, deployment):
        self.env.step("route:redirect")

    def restore(self, service_id, previous_endpoint):
        self.env.step("route:restore")

    def validate_route(self, service_id, expected_endpoint):
        return True

    def validate_public_application(self, service_id):
        return True


class FakeValidation:
    def validate_application(self, service_id, deployment, dependency_graph):
        return ValidationResult(success=True)


class FakeFactory(ProviderFactory):
    def __init__(self, env: Env) -> None:
        self.env = env

    def create_cloud_provider(self, config):
        return FakeCloud(config.environment, self.env)

    def create_database_provider(self, config):
        return FakeDatabase(config.environment, self.env)

    def create_validation_provider(self, config):
        return FakeValidation()

    def create_traffic_provider(self, ingress):
        return FakeTraffic(self.env)

    def create_data_provider(self, engine_type, mode=None):
        self.env.data_mode = mode
        return FakeData(self.env)


def make_request(mode: MigrationMode) -> MigrationRequest:
    return MigrationRequest.model_validate({
        "migration_id": f"mig-{mode.value}",
        "mode": mode.value,
        "microservice": {
            "id": "ms2", "name": "ms2-composite",
            "container": {"image": "microservices-demo/ms2:1.0", "port": 8082, "environment": {}},
            "health_check": {"path": "/health", "port": 8082},
            "dependencies": [],
        },
        "source": {"provider": "aws", "region": "us-east-1", "environment": "source",
                   "endpoint": "http://floci:4566", "credentials": {"access_key_id": "t", "secret_access_key": "t"}},
        "target": {"provider": "azure", "region": "eastus", "environment": "target",
                   "endpoint": "http://floci-az:4577",
                   "credentials": {"tenant_id": "t", "client_id": "c", "client_secret": "s", "subscription_id": "sub"}},
        "data": {
            "type": "postgresql",
            "source": {"host": "src", "port": 5432, "database": "ms2_db", "username": "u", "password": "p"},
            "target": {"host": "dst", "port": 5432, "database": "ms2_db", "username": "u", "password": "p"},
        },
        "source_workload": {"resource_id": "arn:aws:ecs:task/1", "container_name": "ms2", "port": 8082},
        "ingress": {"gateway_admin_url": "http://gateway:8080/actuator/gateway",
                    "route_id": "ms2-route", "public_path": "/ms2/**"},
    })


@pytest.fixture
def env() -> Env:
    return Env()


def migrate(env: Env, mode: MigrationMode = MigrationMode.PRE_COPY_REPLICATION):
    manager = MigrationManager(provider_factory=FakeFactory(env))
    plan = manager.prepare(make_request(mode))
    return manager, plan, manager.migrate(plan)


def test_happy_path_copies_and_syncs_before_maintenance(env):
    manager, plan, result = migrate(env)

    assert result.success, result.message
    assert result.final_state is MigrationState.COMPLETED
    assert env.data_mode is MigrationMode.PRE_COPY_REPLICATION
    assert env.log == [
        "data:check_prerequisites",
        "target-db:provision", "data:base_copy",
        "target:deploy",
        "replication:start", "replication:wait",
        "maintenance:on", "data:drain", "replication:stop", "data:validate",
        "route:redirect", "maintenance:off",
        "source:stop",
    ]
    context = manager._get_context(plan.migration_id)
    assert context.replication_stopped and context.replication_lag.synced
    assert result.downtime_seconds is not None
    state_of = {event.operation: event.state for event in result.events}
    assert state_of["base_copy"] is MigrationState.BASE_COPY_IN_PROGRESS
    assert state_of["wait_replication_sync"] is MigrationState.REPLICATING
    assert state_of["drain_final_delta"] is MigrationState.MIGRATING_DATA


def test_sync_timeout_fails_without_touching_traffic(env):
    env.fail.add("replication:timeout")

    manager, plan, result = migrate(env)

    assert not result.success
    assert result.final_state is MigrationState.FAILED
    assert "nao sincronizou" in result.message
    assert not any(step.startswith(("maintenance:", "route:", "source:")) for step in env.log)
    # Replicacao desmontada antes do banco de destino, para nao reter WAL na origem.
    assert env.log[-3:] == ["replication:stop", "target:remove", "target-db:remove"]
    assert result.downtime_seconds is None


@pytest.mark.parametrize("failing_step", ["data:drain", "route:redirect"])
def test_cutover_failure_restores_source_and_tears_down_replication(env, failing_step):
    env.fail.add(failing_step)

    _, _, result = migrate(env)

    assert not result.success
    assert failing_step in result.message
    compensation = env.log[env.log.index(failing_step) + 1:]
    assert compensation[:2] == ["route:restore", "maintenance:off"]
    # Encerrada uma unica vez: no corte (se chegou la) ou na compensacao.
    assert env.log.count("replication:stop") == 1
    assert compensation[-2:] == ["target:remove", "target-db:remove"]
    assert "source:start" not in compensation  # a origem nunca foi parada
    assert result.downtime_seconds is not None


def test_failure_in_base_copy_cleans_partial_replication_artifacts(env):
    env.fail.add("data:base_copy")

    _, _, result = migrate(env)

    assert not result.success
    assert env.log[-3:] == ["data:base_copy", "replication:stop", "target-db:remove"]


def test_downtime_does_not_include_the_full_copy():
    downtimes = {}
    for mode in MigrationMode:
        _, _, result = migrate(Env(), mode)
        assert result.success, (mode, result.message)
        downtimes[mode] = result.downtime_seconds

    assert downtimes[MigrationMode.PRE_COPY_REPLICATION] < FULL_COPY_SECONDS / 2
    assert downtimes[MigrationMode.CONTINUOUS] >= FULL_COPY_SECONDS
    assert downtimes[MigrationMode.STOP_AND_MIGRATE] >= FULL_COPY_SECONDS
