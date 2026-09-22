"""Implementacao inicial (stub) de DataMigrationProvider para Azure Database/Floci-az.

Simula a migracao de dados de forma identica a `AWSRDSDataMigrationProvider`,
permitindo que o fluxo completo do MigrationManager seja executado e testado
em qualquer direcao (AWS -> Azure, Azure -> AWS) antes que uma estrategia
real de transferencia de dados (ex: dump/restore, CDC, replication) seja
implementada.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.domain.contracts.data_migration_provider import DataMigrationProvider
from app.domain.models.data import (
    DataMigrationResult,
    DataSourceConfig,
    DataTargetConfig,
    ValidationResult,
)


class AzureDatabaseDataMigrationProvider(DataMigrationProvider):
    """Implementacao experimental/stub de migracao de dados via Azure Database/Floci-az."""

    def prepare_source(self, source: DataSourceConfig) -> None:
        # Stub: em uma implementacao real, validaria conectividade e criaria
        # um snapshot/checkpoint consistente da origem.
        return None

    def prepare_target(self, target: DataTargetConfig) -> None:
        # Stub: em uma implementacao real, criaria o schema/estrutura no
        # destino antes da transferencia dos dados.
        return None

    def migrate(
        self, source: DataSourceConfig, target: DataTargetConfig
    ) -> DataMigrationResult:
        started = datetime.now(timezone.utc).isoformat()
        # Stub: simula a copia de dados com um valor fixo de registros.
        finished = datetime.now(timezone.utc).isoformat()
        return DataMigrationResult(
            success=True,
            records_migrated=0,
            started_at=started,
            finished_at=finished,
            message=f"Dados migrados de {source.database} para {target.database} (stub)",
        )

    def validate(
        self, source: DataSourceConfig, target: DataTargetConfig
    ) -> ValidationResult:
        return ValidationResult(
            success=True,
            checks={"target_reachable": True, "record_count_matches": True},
            message="Validacao de dados (stub)",
        )

    def cleanup(self, source: DataSourceConfig) -> None:
        # Stub: em uma implementacao real, removeria dados temporarios,
        # snapshots intermediarios, etc.
        return None

    def rollback(self, target: DataTargetConfig) -> None:
        # Stub: em uma implementacao real, restauraria/limparia o destino.
        return None
