"""MigrationExecutor: executa operacoes individuais registrando eventos e tempo."""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

from app.application.migration_context import MigrationContext
from app.domain.enums.migration_state import MigrationState
from app.domain.enums.provider_type import OperationStatus
from app.domain.models.migration import MigrationEvent

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MigrationExecutor:
    """Executa uma operacao de uma etapa da migracao, cuidando de:

    * medir a duracao da operacao;
    * registrar um MigrationEvent de sucesso ou falha no contexto;
    * logar a operacao;
    * propagar excecoes para que a estrategia decida sobre rollback.

    Isso mantem o codigo das estrategias limpo de logica repetitiva de
    observabilidade.
    """

    def run(
        self,
        context: MigrationContext,
        state: MigrationState,
        operation: str,
        func: Callable[[], T],
    ) -> T:
        started = time.perf_counter()
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 - queremos registrar qualquer falha
            duration_ms = (time.perf_counter() - started) * 1000
            event = MigrationEvent(
                migration_id=context.migration_id,
                state=state,
                operation=operation,
                status=OperationStatus.FAILURE,
                message=str(exc),
                duration_ms=duration_ms,
            )
            context.add_event(event)
            logger.error(
                "migration=%s state=%s operation=%s status=failure error=%s",
                context.migration_id, state.value, operation, exc,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000
            event = MigrationEvent(
                migration_id=context.migration_id,
                state=state,
                operation=operation,
                status=OperationStatus.SUCCESS,
                duration_ms=duration_ms,
            )
            context.add_event(event)
            logger.info(
                "migration=%s state=%s operation=%s status=success duration_ms=%.2f",
                context.migration_id, state.value, operation, duration_ms,
            )
            return result
