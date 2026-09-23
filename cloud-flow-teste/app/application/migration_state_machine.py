"""Maquina de estados que controla as transicoes validas de uma migracao."""
from __future__ import annotations

from app.domain.enums.migration_state import VALID_TRANSITIONS, MigrationState


class InvalidStateTransitionError(Exception):
    """Levantada quando uma transicao de estado nao permitida e solicitada."""

    def __init__(self, current: MigrationState, target: MigrationState) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Transicao invalida de '{current.value}' para '{target.value}'"
        )


class MigrationStateMachine:
    """Controla explicitamente as transicoes de estado de uma migracao,
    impedindo transicoes que nao fazem parte do fluxo definido.
    """

    def __init__(self, initial_state: MigrationState = MigrationState.PENDING) -> None:
        self._state = initial_state

    @property
    def state(self) -> MigrationState:
        return self._state

    def can_transition_to(self, target: MigrationState) -> bool:
        return target in VALID_TRANSITIONS.get(self._state, set())

    def transition_to(self, target: MigrationState) -> MigrationState:
        """Move a maquina de estados para `target`, se a transicao for valida.

        Levanta InvalidStateTransitionError caso contrario.
        """
        if not self.can_transition_to(target):
            raise InvalidStateTransitionError(self._state, target)
        self._state = target
        return self._state
