"""Testes de validacao de MigrationRequest (Teste 1 e Teste 2 do prompt)."""
import pytest
from pydantic import ValidationError

from app.domain.models.data import DataMigrationConfig, DataSourceConfig, DataTargetConfig
from app.domain.models.microservice import ContainerConfig, HealthCheckConfig, MicroserviceConfig
from app.domain.models.migration import MigrationRequest
from app.domain.models.provider import ProviderConfig


def _valid_payload() -> dict:
    return {
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


def test_valid_migration_request_is_accepted():
    request = MigrationRequest.model_validate(_valid_payload())
    assert request.migration_id == "migration-ms-b-001"
    assert request.microservice.container.image == "ms-b:1.0.0"


def test_missing_provider_is_rejected():
    payload = _valid_payload()
    del payload["source"]["provider"]
    with pytest.raises(ValidationError):
        MigrationRequest.model_validate(payload)


def test_microservice_without_image_is_rejected():
    payload = _valid_payload()
    del payload["microservice"]["container"]["image"]
    with pytest.raises(ValidationError):
        MigrationRequest.model_validate(payload)


def test_invalid_port_is_rejected():
    payload = _valid_payload()
    payload["microservice"]["container"]["port"] = 70000
    with pytest.raises(ValidationError):
        MigrationRequest.model_validate(payload)


def test_missing_target_is_rejected():
    payload = _valid_payload()
    del payload["target"]
    with pytest.raises(ValidationError):
        MigrationRequest.model_validate(payload)


def test_invalid_data_config_is_rejected():
    payload = _valid_payload()
    del payload["data"]["source"]["port"]
    with pytest.raises(ValidationError):
        MigrationRequest.model_validate(payload)
