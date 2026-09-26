"""Contrato para migracao de dados com copia base + replicacao continua."""
from abc import abstractmethod

from app.domain.contracts.data_migration_provider import DataMigrationProvider
from app.domain.models.data import DataMigrationResult, ReplicationLagResult, ValidationResult
from app.domain.models.database import DatabaseConnection
from app.domain.models.migration import ReplicationOptions


class ReplicationDataMigrationProvider(DataMigrationProvider):
    """Mantem o destino sincronizado com a origem enquanto ela serve trafego.

    ``replication_name`` identifica os artefatos criados (publication, slot e
    subscription) e deve ser o mesmo em todas as chamadas de uma migracao.
    """

    @abstractmethod
    def prepare_source_connection(self, source: DatabaseConnection) -> None:
        """Verifica pre-requisitos de replicacao logica na origem."""

    @abstractmethod
    def base_copy(
        self, source: DatabaseConnection, target: DatabaseConnection,
        replication_name: str, options: ReplicationOptions,
    ) -> DataMigrationResult:
        """Cria o ponto de replicacao e copia a base ate ele, com a origem no ar."""

    @abstractmethod
    def start_replication(
        self, source: DatabaseConnection, target: DatabaseConnection,
        replication_name: str, options: ReplicationOptions,
    ) -> None:
        """Passa a aplicar no destino as alteracoes feitas apos a copia base."""

    @abstractmethod
    def replication_lag(
        self, source: DatabaseConnection, replication_name: str, options: ReplicationOptions,
    ) -> ReplicationLagResult:
        """Mede o atraso atual da replicacao."""

    @abstractmethod
    def wait_until_synced(
        self, source: DatabaseConnection, replication_name: str, options: ReplicationOptions,
    ) -> ReplicationLagResult:
        """Aguarda o lag ficar abaixo do limite; levanta TimeoutError se nao convergir."""

    @abstractmethod
    def drain_final_delta(
        self, source: DatabaseConnection, target: DatabaseConnection,
        replication_name: str, options: ReplicationOptions,
    ) -> DataMigrationResult:
        """Com as escritas pausadas, aplica o restante no destino e alinha sequences."""

    @abstractmethod
    def stop_replication(
        self, source: DatabaseConnection, target: DatabaseConnection | None, replication_name: str,
    ) -> None:
        """Remove subscription, slot e publication. Idempotente."""

    @abstractmethod
    def validate_connections(
        self, source: DatabaseConnection, target: DatabaseConnection,
    ) -> ValidationResult:
        """Compara schema e contagem de linhas entre origem e destino."""
