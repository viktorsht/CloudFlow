"""Contrato para estrategias de migracao (Continuous, StopAndCopy, ...)."""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.domain.models.migration import MigrationResult

if TYPE_CHECKING:
    from app.application.migration_context import MigrationContext


class MigrationStrategy(ABC):
    """Abstrai o algoritmo/estrategia usada para executar uma migracao.

    Diferentes estrategias (ex: ContinuousMigrationStrategy,
    StopAndCopyMigrationStrategy) podem ser adicionadas sem que o
    MigrationManager precise ser alterado.
    """

    @abstractmethod
    def execute(self, context: "MigrationContext") -> MigrationResult:
        """Executa a migracao completa usando o contexto fornecido."""
