"""Teste 5 - Rollback e Teste 7 - MigrationManager (ordem de execucao)."""
from __future__ import annotations

from app.application.migration_manager import MigrationManager
from app.domain.enums.migration_state import MigrationState
from app.domain.enums.provider_type import CloudProviderType, DataEngineType
from app.infrastructure.providers.factory import ProviderFactory
from tests.application.conftest import (
    FakeCloudProvider,
    FakeDataMigrationProvider,
    FakeTrafficProvider,
    FakeValidationProvider,
)


def _factory_with_fakes(fail_redirect: bool = False) -> tuple[ProviderFactory, dict]:
    factory = ProviderFactory()
    fakes = {
        "source_cloud": FakeCloudProvider("source"),
        "target_cloud": FakeCloudProvider("target"),
        "traffic": FakeTrafficProvider(fail_redirect=fail_redirect),
        "validation": FakeValidationProvider(),
        "data": FakeDataMigrationProvider(),
    }

    # Registra fakes para AWS (origem e destino usam o mesmo tipo de provider
    # neste cenario de teste, entao alternamos via um contador de chamadas).
    call_state = {"cloud_calls": 0}

    def build_cloud(config):
        call_state["cloud_calls"] += 1
        return fakes["source_cloud"] if call_state["cloud_calls"] == 1 else fakes["target_cloud"]

    factory.register_cloud_provider(CloudProviderType.AWS, build_cloud)
    factory.register_traffic_provider(CloudProviderType.AWS, lambda config: fakes["traffic"])
    factory.register_validation_provider(CloudProviderType.AWS, lambda config: fakes["validation"])
    factory.register_data_provider(DataEngineType.POSTGRESQL, lambda: fakes["data"])
    return factory, fakes


def test_migration_manager_executes_steps_in_order(migration_request):
    factory, fakes = _factory_with_fakes()
    manager = MigrationManager(provider_factory=factory)

    plan = manager.prepare(migration_request)
    result = manager.migrate(plan)

    assert result.success is True
    assert result.final_state == MigrationState.COMPLETED

    operations = [event.operation for event in result.events]
    assert operations == [
        "validate_dependencies",
        "prepare_source_data",
        "prepare_target_data",
        "migrate_data",
        "deploy_target_service",
        "validate_target_service",
        "validate_migrated_data",
        "get_current_route",
        "get_target_endpoint",
        "redirect_traffic",
        "validate_route",
        "validate_application",
        "cleanup_source_data",
    ]
    assert all(event.status.value == "success" for event in result.events)
    assert fakes["target_cloud"].calls[0] == "deploy_service"


def test_rollback_after_failure_during_traffic_redirect(migration_request):
    factory, fakes = _factory_with_fakes(fail_redirect=True)
    manager = MigrationManager(provider_factory=factory)

    plan = manager.prepare(migration_request)
    result = manager.migrate(plan)

    assert result.success is False
    assert result.final_state == MigrationState.FAILED

    rollback_result = manager.rollback(plan)

    assert rollback_result.restored_route is True
    assert rollback_result.target_removed is True
    assert fakes["data"].rolled_back is True
    assert manager.get_state(plan.migration_id) == MigrationState.ROLLED_BACK
