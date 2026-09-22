"""Testes da API assincrona POST /migrate/stop-and-migrate e GET /migrate/{id}/status.

Usam providers falsos (duck-typed) no lugar de AWS/Azure/Gateway para exercitar o
fluxo real: API -> MigrationManager -> StopAndMigrateStrategy -> maquina de estados.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.api.controllers import migration_controller as controller
from app.application.migration_manager import MigrationManager
from app.domain.enums.provider_type import HealthStatus
from app.domain.models.data import DataMigrationResult, ValidationResult
from app.domain.models.database import DatabaseConnection
from app.domain.models.deployment import Deployment, HealthCheckResult
from app.infrastructure.providers.factory import ProviderFactory
from app.main import app


class Env:
    """Estado compartilhado dos fakes: log de chamadas, falha injetada e um portao
    que segura a parada da origem para simular uma migracao demorada."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.fail_data = False
        self.gate = threading.Event()
        self.gate.set()


class FakeCloud:
    def __init__(self, role: str, env: Env) -> None:
        self.role, self.env = role, env

    def discover_service(self, reference):
        return Deployment(deployment_id=reference.resource_id, service_id="svc", endpoint="http://source")

    def stop_service(self, deployment):
        self.env.log.append("source:stop")
        assert self.env.gate.wait(10), "portao nunca foi liberado"

    def start_service(self, deployment):
        self.env.log.append("source:start")

    def deploy_service(self, config):
        self.env.log.append("target:deploy")
        return Deployment(deployment_id="target-1", service_id=config.id, endpoint="http://target")

    def remove_service(self, deployment):
        self.env.log.append("target:remove")

    def health_check(self, deployment):
        return HealthCheckResult(status=HealthStatus.HEALTHY)


class FakeDatabase:
    def __init__(self, role: str, env: Env) -> None:
        self.role, self.env = role, env

    def _connection(self, config) -> DatabaseConnection:
        return DatabaseConnection("db", 5432, config.database, config.username, config.password, f"{self.role}-db", "fake")

    def discover(self, config):
        return self._connection(config)

    def provision(self, config):
        self.env.log.append("target-db:provision")
        return self._connection(config)

    def remove(self, connection):
        self.env.log.append("target-db:remove")


class FakeData:
    def __init__(self, env: Env) -> None:
        self.env = env

    def migrate_connections(self, source, target):
        self.env.log.append("data:migrate")
        return DataMigrationResult(success=not self.env.fail_data)

    def validate_connections(self, source, target):
        return ValidationResult(success=True)

    def rollback(self, target):
        pass


class FakeTraffic:
    def __init__(self, env: Env) -> None:
        self.env = env

    def get_current_route(self, service_id):
        return "http://source"

    def enable_maintenance(self, service_id):
        self.env.log.append("maintenance:on")

    def disable_maintenance(self, service_id):
        self.env.log.append("maintenance:off")

    def redirect_deployment(self, service_id, deployment):
        self.env.log.append("route:redirect")

    def restore(self, service_id, previous_endpoint):
        self.env.log.append("route:restore")

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

    def create_data_provider(self, engine_type):
        return FakeData(self.env)


def payload(service: str = "ms2", **overrides) -> dict:
    body = {
        "microservice": {
            "id": service,
            "name": f"{service}-composite",
            "container": {"image": f"microservices-demo/{service}:1.0", "port": 8082, "environment": {}},
            "health_check": {"path": "/health", "port": 8082},
            "dependencies": [{"service_id": "ms3", "protocol": "http", "port": 8083, "required": True}],
        },
        "source": {
            "provider": "aws", "region": "us-east-1", "environment": "source",
            "endpoint": "http://floci:4566", "credentials": {"access_key_id": "test", "secret_access_key": "test"},
        },
        "destination": {
            "provider": "azure", "region": "eastus", "environment": "target",
            "endpoint": "http://floci-az:4577",
            "credentials": {"tenant_id": "t", "client_id": "c", "client_secret": "s", "subscription_id": "sub"},
        },
        "data": {
            "type": "postgresql",
            "source": {"host": "src", "port": 5432, "database": f"{service}_db", "username": "u", "password": "p"},
            "target": {"host": "dst", "port": 5432, "database": f"{service}_db", "username": "u", "password": "p"},
        },
        "source_workload": {"resource_id": "arn:aws:ecs:task/1", "container_name": service, "port": 8082},
        "ingress": {
            "gateway_admin_url": "http://gateway:8080/actuator/gateway",
            "route_id": f"{service}-route", "public_path": f"/{service}/**",
        },
    }
    body.update(overrides)
    return body


@pytest.fixture
def env() -> Env:
    return Env()


@pytest.fixture
def client(env: Env):
    for registry in (controller._plans, controller._active, controller._results):
        registry.clear()
    manager = MigrationManager(provider_factory=FakeFactory(env))
    app.dependency_overrides[controller.get_manager] = lambda: manager
    yield TestClient(app)
    env.gate.set()  # nao deixa thread presa se um teste falhar no meio
    app.dependency_overrides.clear()


def wait_for(client: TestClient, migration_id: str, predicate, timeout: float = 5.0) -> dict:
    deadline, body = time.monotonic() + timeout, {}
    while time.monotonic() < deadline:
        body = client.get(f"/migrate/{migration_id}/status").json()
        if predicate(body):
            return body
        time.sleep(0.02)
    raise AssertionError(f"tempo esgotado; ultimo status: {body}")


def finished(body: dict) -> bool:
    return body["status"] in ("COMPLETED", "FAILED")


def test_accepts_immediately_and_migrates_in_background(client, env):
    env.gate.clear()  # a parada da origem fica bloqueada: a migracao esta "demorando"

    response = client.post("/migrate/stop-and-migrate", json=payload())

    assert response.status_code == 202
    body = response.json()
    assert body["migrationId"]
    assert body == {
        "migrationId": body["migrationId"], "microservice": "ms2",
        "source": "aws", "destination": "azure", "status": "PENDING",
    }
    migration_id = body["migrationId"]

    running = wait_for(client, migration_id, lambda b: b["state"] == "quiescing_source")
    assert running["status"] == "IN_PROGRESS"
    assert running["error"] is None

    env.gate.set()
    done = wait_for(client, migration_id, finished)

    assert done["status"] == "COMPLETED"
    assert done["state"] == "completed"
    assert done["error"] is None
    assert (done["microservice"], done["source"], done["destination"]) == ("ms2", "aws", "azure")
    assert env.log == [
        "target-db:provision", "maintenance:on", "source:stop", "data:migrate",
        "target:deploy", "route:redirect", "maintenance:off",
    ]


@pytest.mark.parametrize("destination_key", ["destination", "target"])
def test_destination_can_be_sent_as_destination_or_target(client, destination_key):
    body = payload()
    body[destination_key] = body.pop("destination")

    response = client.post("/migrate/stop-and-migrate", json=body)

    assert response.status_code == 202
    assert response.json()["destination"] == "azure"


def test_failure_is_reported_and_source_is_restored(client, env):
    env.fail_data = True

    migration_id = client.post("/migrate/stop-and-migrate", json=payload()).json()["migrationId"]
    done = wait_for(client, migration_id, finished)

    assert done["status"] == "FAILED"
    assert done["state"] == "failed"
    assert "dump/restore" in done["error"]
    # a estrategia desfaz: rota, manutencao, origem religada e destino removido
    assert {"route:restore", "maintenance:off", "source:start", "target-db:remove"} <= set(env.log)
    assert "target:deploy" not in env.log


def test_unknown_migration_returns_404(client):
    assert client.get("/migrate/does-not-exist/status").status_code == 404


def test_same_microservice_is_serialized_but_different_ones_run_in_parallel(client, env):
    env.gate.clear()

    first = client.post("/migrate/stop-and-migrate", json=payload("ms2"))
    same_service = client.post("/migrate/stop-and-migrate", json=payload("ms2"))
    other_service = client.post("/migrate/stop-and-migrate", json=payload("ms3"))

    assert first.status_code == 202
    assert same_service.status_code == 409
    assert other_service.status_code == 202

    env.gate.set()
    for accepted in (first, other_service):
        assert wait_for(client, accepted.json()["migrationId"], finished)["status"] == "COMPLETED"

    # terminada a migracao, o mesmo microsservico pode ser migrado de novo
    again = client.post("/migrate/stop-and-migrate", json=payload("ms2"))
    assert again.status_code == 202
    assert wait_for(client, again.json()["migrationId"], finished)["status"] == "COMPLETED"


def test_client_supplied_migration_id_is_used_and_cannot_be_reused(client):
    first = client.post("/migrate/stop-and-migrate", json=payload(migration_id="mig-42"))
    assert first.status_code == 202
    assert first.json()["migrationId"] == "mig-42"
    wait_for(client, "mig-42", finished)

    assert client.post("/migrate/stop-and-migrate", json=payload(migration_id="mig-42")).status_code == 409


@pytest.mark.parametrize("missing", ["source_workload", "ingress", "source", "destination", "data", "microservice"])
def test_incomplete_body_is_rejected_before_starting(client, missing):
    body = payload()
    del body[missing]

    response = client.post("/migrate/stop-and-migrate", json=body)

    assert response.status_code == 422
    assert not controller._plans


def test_unsupported_provider_is_rejected_with_422():
    for registry in (controller._plans, controller._active, controller._results):
        registry.clear()
    app.dependency_overrides[controller.get_manager] = lambda: MigrationManager()  # factory real
    try:
        body = payload()
        body["source"]["provider"] = "gcp"
        body["source"]["credentials"] = body["destination"]["credentials"]  # gcp usa o formato nao-AWS

        response = TestClient(app).post("/migrate/stop-and-migrate", json=body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "nao suportado" in response.json()["detail"]
    assert not controller._active
