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
