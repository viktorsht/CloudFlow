"""Contrato para provisionamento e remocao de bancos do provedor."""
from abc import ABC, abstractmethod

from app.domain.models.data import DataTargetConfig
from app.domain.models.database import DatabaseConnection


class DatabaseProvider(ABC):
    @abstractmethod
    def discover(self, config: DataTargetConfig) -> DatabaseConnection:
        """Resolve uma base existente em uma conexao utilizavel pelo manager."""

    @abstractmethod
    def provision(self, config: DataTargetConfig) -> DatabaseConnection:
        """Provisiona servidor/base de destino e retorna a conexao resolvida."""

    @abstractmethod
    def remove(self, connection: DatabaseConnection) -> None:
        """Remove definitivamente um banco criado para a migracao."""
