"""Teste 4 - transicoes de estado da migracao."""
import pytest

from app.application.migration_state_machine import (
    InvalidStateTransitionError,
    MigrationStateMachine,
)
from app.domain.enums.migration_state import MigrationState


def test_pending_to_preparing_is_valid():
    sm = MigrationStateMachine()
    assert sm.state == MigrationState.PENDING
    sm.transition_to(MigrationState.PREPARING)
    assert sm.state == MigrationState.PREPARING


def test_pending_to_completed_is_invalid():
    sm = MigrationStateMachine()
    with pytest.raises(InvalidStateTransitionError):
        sm.transition_to(MigrationState.COMPLETED)


def test_failable_state_can_transition_to_failed():
    sm = MigrationStateMachine(initial_state=MigrationState.REDIRECTING_TRAFFIC)
    sm.transition_to(MigrationState.FAILED)
    assert sm.state == MigrationState.FAILED


def test_failed_can_transition_to_rolling_back_then_rolled_back():
    sm = MigrationStateMachine(initial_state=MigrationState.FAILED)
    sm.transition_to(MigrationState.ROLLING_BACK)
    sm.transition_to(MigrationState.ROLLED_BACK)
    assert sm.state == MigrationState.ROLLED_BACK


PRE_COPY_REPLICATION_FLOW = [
    MigrationState.PREPARING,
    MigrationState.PREPARED,
    MigrationState.BASE_COPY_IN_PROGRESS,
    MigrationState.BASE_COPY_DONE,
    MigrationState.DEPLOYING_TARGET,
    MigrationState.TARGET_DEPLOYED,
    MigrationState.VALIDATING_TARGET,
    MigrationState.TARGET_VALID,
    MigrationState.REPLICATING,
    MigrationState.REPLICATION_SYNCED,
    MigrationState.QUIESCING_SOURCE,
    MigrationState.MIGRATING_DATA,
    MigrationState.DATA_MIGRATED,
    MigrationState.REDIRECTING_TRAFFIC,
    MigrationState.TRAFFIC_REDIRECTED,
    MigrationState.VALIDATING_APPLICATION,
    MigrationState.MIGRATION_COMPLETED,
    MigrationState.SOURCE_CLEANUP,
    MigrationState.COMPLETED,
]


def test_pre_copy_replication_full_flow_is_valid():
    sm = MigrationStateMachine()
    for state in PRE_COPY_REPLICATION_FLOW:
        sm.transition_to(state)
    assert sm.state == MigrationState.COMPLETED


@pytest.mark.parametrize("state", [MigrationState.BASE_COPY_IN_PROGRESS, MigrationState.REPLICATING])
def test_pre_copy_replication_in_progress_states_can_fail(state):
    sm = MigrationStateMachine(initial_state=state)
    sm.transition_to(MigrationState.FAILED)
    assert sm.state == MigrationState.FAILED


def test_replication_cannot_skip_sync_before_quiescing():
    sm = MigrationStateMachine(initial_state=MigrationState.REPLICATING)
    with pytest.raises(InvalidStateTransitionError):
        sm.transition_to(MigrationState.QUIESCING_SOURCE)
