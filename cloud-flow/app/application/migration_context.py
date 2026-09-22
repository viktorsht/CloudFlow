"""MigrationContext: estado mutavel que acompanha uma migracao do inicio ao fim."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.contracts.cloud_provider import CloudProvider
from app.domain.contracts.data_migration_provider import DataMigrationProvider
from app.domain.contracts.database_provider import DatabaseProvider
from app.domain.contracts.traffic_provider import TrafficProvider
from app.domain.contracts.validation_provider import ValidationProvider
from app.domain.enums.migration_state import MigrationState
from app.domain.models.data import DataMigrationResult
from app.domain.models.database import DatabaseConnection
from app.domain.models.dependency import DependencyGraph
from app.domain.models.deployment import Deployment
from app.domain.models.migration import MigrationEvent, MigrationRequest


@dataclass
class ProviderBundle:
    """Agrupa as implementacoes concretas resolvidas pela ProviderFactory
    para um determinado provedor de nuvem (origem ou destino).
    """

    cloud_provider: CloudProvider
    database_provider: DatabaseProvider
    validation_provider: ValidationProvider


@dataclass
class MigrationContext:
    """Contem todas as informacoes necessarias durante a execucao de uma
    migracao. E o objeto que trafega entre o MigrationManager e a
    MigrationStrategy, acumulando estado a medida que as etapas avancam.
    """

    request: MigrationRequest
    source: ProviderBundle
    target: ProviderBundle
    data_provider: DataMigrationProvider
    traffic_provider: TrafficProvider
    dependency_graph: DependencyGraph

    current_state: MigrationState = MigrationState.PENDING
    source_deployment: Deployment | None = None
    target_deployment: Deployment | None = None
    source_database: DatabaseConnection | None = None
    target_database: DatabaseConnection | None = None
    data_migration_result: DataMigrationResult | None = None
    original_route: str | None = None
    maintenance_enabled: bool = False
    source_was_stopped: bool = False
    events: list[MigrationEvent] = field(default_factory=list)

    @property
    def migration_id(self) -> str:
        return self.request.migration_id

    def add_event(self, event: MigrationEvent) -> None:
        self.events.append(event)
