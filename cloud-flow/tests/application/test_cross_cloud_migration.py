"""Testes de integracao (fim a fim) da migracao entre AWS e Azure, nos dois
sentidos, usando as implementacoes REAIS registradas por padrao na
ProviderFactory (DockerComputeProvider/AzureContainerComputeProvider e
demais stubs "Floci"), em vez dos fakes usados em test_migration_manager.py.

O objetivo aqui e comprovar que o MigrationManager e a
ContinuousMigrationStrategy sao, de fato, agnosticos de provedor: a mesma
orquestracao funciona tanto para origem=AWS/destino=Azure quanto para
origem=Azure/destino=AWS, sem nenhuma alteracao de codigo - apenas trocando
o `provider` na MigrationRequest.
"""
from __future__ import annotations

import pytest

from app.application.migration_manager import MigrationManager
from app.domain.enums.migration_state import MigrationState
from app.domain.models.migration import MigrationRequest


def _request(migration_id: str, source_provider: str, target_provider: str) -> MigrationRequest:
    return MigrationRequest.model_validate(
        {
            "migration_id": migration_id,
            "microservice": {
                "id": "ms2",
                "name": "ms2-composite",
                "container": {"image": "ms2-composite:1.0.0", "port": 8082, "environment": {}},
                "health_check": {"path": "/health", "port": 8082},
                "dependencies": [
                    {"service_id": "ms3", "protocol": "http", "port": 8083, "required": True}
                ],
            },
            "source": {
                "provider": source_provider,
                "region": "us-east-1" if source_provider == "aws" else "eastus",
                "environment": "source",
            },
            "target": {
                "provider": target_provider,
                "region": "us-east-1" if target_provider == "aws" else "eastus",
                "environment": "target",
            },
            "data": {
                "type": "postgresql",
                "source": {"host": "postgres-ms2", "port": 5432, "database": "ms2_db"},
                "target": {"host": "target-db", "port": 5432, "database": "ms2_db"},
            },
        }
    )


@pytest.mark.parametrize(
    ("source_provider", "target_provider"),
    [
        ("aws", "azure"),
        ("azure", "aws"),
    ],
)
def test_full_migration_between_aws_and_azure(source_provider, target_provider):
    manager = MigrationManager()
    request = _request(
        f"migration-ms2-{source_provider}-to-{target_provider}", source_provider, target_provider
    )

    plan = manager.prepare(request)
    result = manager.migrate(plan)

    assert result.success is True
    assert result.final_state == MigrationState.COMPLETED
    assert all(event.status.value == "success" for event in result.events)

    context = manager._get_context(plan.migration_id)  # noqa: SLF001 - inspecao em teste
    assert context.target_deployment is not None
    assert context.target_deployment.provider == target_provider


def test_rollback_works_for_azure_to_aws_direction():
    """Garante que o rollback tambem funciona quando o destino e AWS (nao
    apenas o caminho feliz), reforcando que nenhuma etapa da migracao ou do
    rollback e especifica de um unico provedor.
    """
    manager = MigrationManager()
    request = _request("migration-ms2-azure-to-aws-rollback", "azure", "aws")

    plan = manager.prepare(request)
    result = manager.migrate(plan)
    assert result.success is True  # stubs nao falham; valida fluxo completo

    # Mesmo em caminho feliz, o rollback deve ser executavel e consistente
    # (remove o deployment de destino e restaura a rota de origem).
    rollback_result = manager.rollback(plan)
    assert rollback_result.target_removed is True
