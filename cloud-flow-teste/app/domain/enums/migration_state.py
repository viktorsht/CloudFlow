"""Enum que representa o estado do ciclo de vida de uma migracao."""
from enum import Enum


class MigrationState(str, Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    PREPARED = "prepared"
    TARGET_DATABASE_READY = "target_database_ready"
    SOURCE_STOPPED = "source_stopped"
    MIGRATING_DATA = "migrating_data"
    DATA_MIGRATED = "data_migrated"
    DEPLOYING_TARGET = "deploying_target"
    TARGET_DEPLOYED = "target_deployed"
    VALIDATING_TARGET = "validating_target"
    TARGET_VALID = "target_valid"
    QUIESCING_SOURCE = "quiescing_source"
    REDIRECTING_TRAFFIC = "redirecting_traffic"
    TRAFFIC_REDIRECTED = "traffic_redirected"
    VALIDATING_APPLICATION = "validating_application"
    MIGRATION_COMPLETED = "migration_completed"
    SOURCE_CLEANUP = "source_cleanup"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    # Estados exclusivos da estrategia pre_copy_replication.
    BASE_COPY_IN_PROGRESS = "base_copy_in_progress"
    BASE_COPY_DONE = "base_copy_done"
    REPLICATING = "replicating"
    REPLICATION_SYNCED = "replication_synced"


# Transicoes validas do fluxo principal (feliz) + falha a partir de qualquer
# estado "em andamento" + fluxo de rollback.
_HAPPY_PATH: list[tuple[MigrationState, MigrationState]] = [
    (MigrationState.PENDING, MigrationState.PREPARING),
    (MigrationState.PREPARING, MigrationState.PREPARED),
    (MigrationState.PREPARED, MigrationState.MIGRATING_DATA),
    (MigrationState.PREPARED, MigrationState.DEPLOYING_TARGET),
    (MigrationState.DEPLOYING_TARGET, MigrationState.TARGET_DATABASE_READY),
    (MigrationState.TARGET_DATABASE_READY, MigrationState.QUIESCING_SOURCE),
    (MigrationState.QUIESCING_SOURCE, MigrationState.SOURCE_STOPPED),
    (MigrationState.SOURCE_STOPPED, MigrationState.MIGRATING_DATA),
    (MigrationState.MIGRATING_DATA, MigrationState.DATA_MIGRATED),
    (MigrationState.DATA_MIGRATED, MigrationState.DEPLOYING_TARGET),
    (MigrationState.DEPLOYING_TARGET, MigrationState.TARGET_DEPLOYED),
    (MigrationState.TARGET_DEPLOYED, MigrationState.VALIDATING_TARGET),
    (MigrationState.VALIDATING_TARGET, MigrationState.TARGET_VALID),
    (MigrationState.TARGET_VALID, MigrationState.REDIRECTING_TRAFFIC),
    (MigrationState.TARGET_VALID, MigrationState.QUIESCING_SOURCE),
    (MigrationState.QUIESCING_SOURCE, MigrationState.MIGRATING_DATA),
    (MigrationState.DATA_MIGRATED, MigrationState.REDIRECTING_TRAFFIC),
    (MigrationState.REDIRECTING_TRAFFIC, MigrationState.TRAFFIC_REDIRECTED),
    (MigrationState.TRAFFIC_REDIRECTED, MigrationState.VALIDATING_APPLICATION),
    (MigrationState.VALIDATING_APPLICATION, MigrationState.MIGRATION_COMPLETED),
    (MigrationState.MIGRATION_COMPLETED, MigrationState.SOURCE_CLEANUP),
    (MigrationState.SOURCE_CLEANUP, MigrationState.COMPLETED),
]

# Pre-copy + replicacao logica: copia base e sincronizacao com a origem no ar;
# o corte reaproveita QUIESCING_SOURCE -> MIGRATING_DATA do fluxo principal.
_PRE_COPY_REPLICATION_PATH: list[tuple[MigrationState, MigrationState]] = [
    (MigrationState.PREPARED, MigrationState.BASE_COPY_IN_PROGRESS),
    (MigrationState.BASE_COPY_IN_PROGRESS, MigrationState.BASE_COPY_DONE),
    (MigrationState.BASE_COPY_DONE, MigrationState.DEPLOYING_TARGET),
    (MigrationState.TARGET_VALID, MigrationState.REPLICATING),
    (MigrationState.REPLICATING, MigrationState.REPLICATION_SYNCED),
    (MigrationState.REPLICATION_SYNCED, MigrationState.QUIESCING_SOURCE),
]

# Estados "em andamento" a partir dos quais uma falha pode ocorrer.
_FAILABLE_STATES: list[MigrationState] = [
    MigrationState.PREPARING,
    MigrationState.MIGRATING_DATA,
    MigrationState.DEPLOYING_TARGET,
    MigrationState.TARGET_DATABASE_READY,
    MigrationState.VALIDATING_TARGET,
    MigrationState.QUIESCING_SOURCE,
    MigrationState.SOURCE_STOPPED,
    MigrationState.REDIRECTING_TRAFFIC,
    MigrationState.VALIDATING_APPLICATION,
    MigrationState.SOURCE_CLEANUP,
    MigrationState.BASE_COPY_IN_PROGRESS,
    MigrationState.REPLICATING,
]

_FAILURE_PATH: list[tuple[MigrationState, MigrationState]] = [
    (state, MigrationState.FAILED) for state in _FAILABLE_STATES
]

_ROLLBACK_PATH: list[tuple[MigrationState, MigrationState]] = [
    (MigrationState.FAILED, MigrationState.ROLLING_BACK),
    (MigrationState.ROLLING_BACK, MigrationState.ROLLED_BACK),
]

VALID_TRANSITIONS: dict[MigrationState, set[MigrationState]] = {}
for _from, _to in [*_HAPPY_PATH, *_PRE_COPY_REPLICATION_PATH, *_FAILURE_PATH, *_ROLLBACK_PATH]:
    VALID_TRANSITIONS.setdefault(_from, set()).add(_to)


TERMINAL_STATES: set[MigrationState] = {
    MigrationState.COMPLETED,
    MigrationState.ROLLED_BACK,
}
