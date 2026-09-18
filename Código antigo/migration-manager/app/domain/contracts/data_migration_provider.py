"""Contrato para provedores de migracao de dados (PostgreSQL, MySQL, RDS...)."""
from abc import ABC, abstractmethod

from app.domain.models.data import (
    DataMigrationResult,
    DataSourceConfig,
    DataTargetConfig,
    ValidationResult,
)


class DataMigrationProvider(ABC):
    """Abstrai a estrategia concreta usada para migrar dados de um servico.

    O MigrationManager apenas coordena a chamada destas operacoes, sem
    conhecer detalhes de driver, protocolo ou motor de banco de dados.
    """

    @abstractmethod
    def prepare_source(self, source: DataSourceConfig) -> None:
        """Prepara a origem para a migracao (ex: validar acesso, criar snapshot)."""

    @abstractmethod
    def prepare_target(self, target: DataTargetConfig) -> None:
        """Prepara o destino para receber os dados (ex: criar schema)."""

    @abstractmethod
    def migrate(
        self, source: DataSourceConfig, target: DataTargetConfig
    ) -> DataMigrationResult:
        """Executa a transferencia de dados de origem para destino."""

    @abstractmethod
    def validate(
        self, source: DataSourceConfig, target: DataTargetConfig
    ) -> ValidationResult:
        """Valida a integridade/consistencia dos dados migrados."""

    @abstractmethod
    def cleanup(self, source: DataSourceConfig) -> None:
        """Executa limpeza pos-migracao na origem, quando aplicavel."""

    @abstractmethod
    def rollback(self, target: DataTargetConfig) -> None:
        """Desfaz a migracao de dados no destino em caso de falha."""
