"""Controller REST para operacoes de migracao."""
from __future__ import annotations

import logging
import threading
from enum import Enum
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from app.application.migration_manager import MigrationManager
from app.domain.enums.migration_state import MigrationState
from app.domain.models.data import DataMigrationConfig, ValidationResult
from app.domain.models.deployment import RollbackResult
from app.domain.models.microservice import MicroserviceConfig
from app.domain.models.migration import (
    IngressConfig,
    MigrationMode,
    MigrationPlan,
    MigrationRequest,
    MigrationResult,
    WorkloadReference,
)
from app.domain.models.provider import ProviderConfig
from app.infrastructure.providers.factory import UnsupportedProviderError
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/migrations", tags=["migrations"])

# Instancia unica compartilhada do MigrationManager e dos planos preparados.
# Em uma aplicacao maior, isso seria resolvido via um container de DI/FastAPI
# Depends com escopo de aplicacao; aqui mantemos simples e explicito.
_manager = MigrationManager()
_plans: dict[str, object] = {}


def get_manager() -> MigrationManager:
    return _manager


@router.post("", status_code=201)
def create_migration(
    request: MigrationRequest, manager: MigrationManager = Depends(get_manager)
) -> dict:
    """Recebe uma MigrationRequest, prepara o plano e o mantem disponivel
    para as proximas etapas (execute/validate/rollback).
    """
    plan = manager.prepare(request)
    _plans[request.migration_id] = plan
    return {
        "migration_id": plan.migration_id,
        "state": manager.get_state(plan.migration_id).value,
        "dependency_order_notes": plan.dependency_order_notes,
    }


@router.post("/stop-and-migrate", status_code=202)
def stop_and_migrate(
    request: MigrationRequest,
    background_tasks: BackgroundTasks,
    manager: MigrationManager = Depends(get_manager),
) -> dict:
    """Recebe toda a configuracao no body e inicia uma parada/migracao unica.

    A resposta e assincrona para que a conexao HTTP nao determine o limite de
    duracao da migracao. O estado e acompanhado por ``GET /migrations/{id}``.
    """
    if request.migration_id in _plans:
        raise HTTPException(status_code=409, detail=f"migration_id '{request.migration_id}' ja existe")
    if request.mode is not MigrationMode.STOP_AND_MIGRATE:
        request = request.model_copy(update={"mode": MigrationMode.STOP_AND_MIGRATE})
    try:
        plan = manager.prepare(request)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _plans[plan.migration_id] = plan
    background_tasks.add_task(manager.migrate, plan)
    return {
        "migration_id": plan.migration_id,
        "state": manager.get_state(plan.migration_id).value,
        "status_url": f"/migrations/{plan.migration_id}",
    }


@router.get("/{migration_id}")
def get_migration(
    migration_id: str, manager: MigrationManager = Depends(get_manager)
) -> dict:
    _require_plan(migration_id)
    try:
        state = manager.get_state(migration_id)
        events = manager.get_events(migration_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "migration_id": migration_id,
        "state": state.value,
        "events": [event.model_dump(mode="json") for event in events],
    }


@router.post("/{migration_id}/prepare")
def prepare_migration(
    migration_id: str, manager: MigrationManager = Depends(get_manager)
) -> dict:
    plan = _require_plan(migration_id)
    plan = manager.prepare(plan.request)
    _plans[migration_id] = plan
    return {"migration_id": migration_id, "state": manager.get_state(migration_id).value}


@router.post("/{migration_id}/execute", response_model=MigrationResult)
def execute_migration(
    migration_id: str, manager: MigrationManager = Depends(get_manager)
) -> MigrationResult:
    plan = _require_plan(migration_id)
    return manager.migrate(plan)


@router.post("/{migration_id}/validate", response_model=ValidationResult)
def validate_migration(
    migration_id: str, manager: MigrationManager = Depends(get_manager)
) -> ValidationResult:
    plan = _require_plan(migration_id)
    return manager.validate(plan)


@router.post("/{migration_id}/rollback", response_model=RollbackResult)
def rollback_migration(
    migration_id: str, manager: MigrationManager = Depends(get_manager)
) -> RollbackResult:
    plan = _require_plan(migration_id)
    return manager.rollback(plan)


@router.post("/{migration_id}/finalize", status_code=204)
def finalize_source(
    migration_id: str, manager: MigrationManager = Depends(get_manager)
) -> None:
    """Remove definitivamente a origem retida depois de validacao manual."""
    try:
        manager.finalize_source(_require_plan(migration_id))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _require_plan(migration_id: str):
    plan = _plans.get(migration_id)
    if plan is None:
        raise HTTPException(
            status_code=404, detail=f"Migracao '{migration_id}' nao encontrada. Chame POST /migrations primeiro."
        )
    return plan


# ---------------------------------------------------------------------------
# API assincrona por microsservico:
#   POST /migrate/stop-and-migrate   aceita e executa em segundo plano
#   GET  /migrate/{id}/status        acompanhamento ate COMPLETED ou FAILED
#
# Cada requisicao migra UM microsservico, com todos os dados no proprio body.
# Microsservicos diferentes migram em paralelo; o mesmo microsservico nao.
# Reaproveita o MigrationManager e o registro de planos acima, entao
# rollback/finalize/validate de /migrations continuam valendo para estas
# migracoes.
# ---------------------------------------------------------------------------

migrate_router = APIRouter(prefix="/migrate", tags=["migrate"])

_lock = threading.Lock()
_active: dict[str, str] = {}  # microservice.id -> migration_id em andamento
# migration_id -> None (concluida com sucesso) ou mensagem de erro (falhou).
# So existe depois que a execucao terminou, e e o que define COMPLETED/FAILED.
_results: dict[str, MigrationResult] = {}


class MigrationStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StopAndMigrateRequest(BaseModel):
    """Body de POST /migrate/stop-and-migrate.

    O destino pode ser enviado como ``destination`` ou ``target``. O
    ``migration_id`` e opcional: quando ausente, o servico gera um.
    """

    migration_id: str | None = Field(default=None, min_length=1)
    microservice: MicroserviceConfig
    source: ProviderConfig
    target: ProviderConfig = Field(validation_alias=AliasChoices("target", "destination"))
    data: DataMigrationConfig
    source_workload: WorkloadReference
    ingress: IngressConfig

    def to_migration_request(self, migration_id: str) -> MigrationRequest:
        return MigrationRequest(
            migration_id=migration_id,
            mode=MigrationMode.STOP_AND_MIGRATE,
            microservice=self.microservice,
            source=self.source,
            target=self.target,
            data=self.data,
            source_workload=self.source_workload,
            ingress=self.ingress,
        )


class MigrationSummary(BaseModel):
    migrationId: str
    microservice: str
    source: str
    destination: str
    status: MigrationStatus


class MigrationStatusResponse(MigrationSummary):
    state: str
    error: str | None = None
    downtime_seconds: float | None = None
    downtime_started_at: datetime | None = None
    downtime_finished_at: datetime | None = None


@migrate_router.post("/stop-and-migrate", status_code=202, response_model=MigrationSummary)
def start_stop_and_migrate(
    body: StopAndMigrateRequest, manager: MigrationManager = Depends(get_manager)
) -> MigrationSummary:
    """Aceita a migracao, responde na hora e executa stop-and-migrate em segundo plano."""
    migration_id = body.migration_id or str(uuid4())
    if migration_id in _plans:
        raise HTTPException(status_code=409, detail=f"migration_id '{migration_id}' ja existe")
    try:
        plan = manager.prepare(body.to_migration_request(migration_id))
    except (ValueError, RuntimeError, UnsupportedProviderError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    service_id = plan.request.microservice.id
    with _lock:
        if service_id in _active:
            raise HTTPException(
                status_code=409,
                detail=f"Ja existe a migracao '{_active[service_id]}' em andamento para '{service_id}'",
            )
        _active[service_id] = migration_id
    _plans[migration_id] = plan

    # Thread propria: uma migracao leva minutos e nao deve ocupar o pool que
    # atende as demais requisicoes HTTP.
    threading.Thread(
        target=_run_migration, args=(manager, plan), name=f"migration-{migration_id}", daemon=True
    ).start()
    return _summary(plan, MigrationStatus.PENDING)


# @migrate_router.get("/{migration_id}/status", response_model=MigrationStatusResponse)
# def get_migration_status(
#     migration_id: str, manager: MigrationManager = Depends(get_manager)
# ) -> MigrationStatusResponse:
#     plan = _plans.get(migration_id)
#     if plan is None:
#         raise HTTPException(status_code=404, detail=f"Migracao '{migration_id}' nao encontrada")
#     state = manager.get_state(migration_id)
#     summary = _summary(plan, _status_of(migration_id, state))
#     return MigrationStatusResponse(**summary.model_dump(), state=state.value, error=_results.get(migration_id))

@migrate_router.get("/{migration_id}/status",response_model=MigrationStatusResponse)
def get_migration_status(migration_id: str,manager: MigrationManager = Depends(get_manager)) -> MigrationStatusResponse:
    plan = _plans.get(migration_id)

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"Migracao '{migration_id}' nao encontrada",
        )

    state = manager.get_state(migration_id)
    status = _status_of(migration_id, state)
    summary = _summary(plan, status)
    result = _results.get(migration_id)

    return MigrationStatusResponse(
        **summary.model_dump(),
        state=state.value,
        error=(
            result.message
            if result and not result.success
            else None
        ),
        downtime_seconds=(
            result.downtime_seconds
            if result
            else None
        ),
        downtime_started_at=(
            result.downtime_started_at
            if result
            else None
        ),
        downtime_finished_at=(
            result.downtime_finished_at
            if result
            else None
        ),
    )


def _run_migration(manager: MigrationManager, plan: MigrationPlan) -> None:
    try:
        result = manager.migrate(plan)

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Migracao %s terminou com erro inesperado",
            plan.migration_id,
        )

        result = MigrationResult(
            migration_id=plan.migration_id,
            success=False,
            final_state=MigrationState.FAILED,
            events=[],
            message=str(exc) or type(exc).__name__,
        )

    with _lock:
        _active.pop(plan.request.microservice.id, None)
        _results[plan.migration_id] = result


def _status_of(migration_id: str, state: MigrationState) -> MigrationStatus:
    if state in (MigrationState.ROLLING_BACK, MigrationState.ROLLED_BACK):
        return MigrationStatus.FAILED
    # if migration_id in _results:
        # return MigrationStatus.FAILED if _results[migration_id] else MigrationStatus.COMPLETED
    if migration_id in _results:
        return (MigrationStatus.COMPLETED if _results[migration_id].success else MigrationStatus.FAILED)
    return MigrationStatus.PENDING if state is MigrationState.PENDING else MigrationStatus.IN_PROGRESS


def _summary(plan: MigrationPlan, status: MigrationStatus) -> MigrationSummary:
    request = plan.request
    return MigrationSummary(
        migrationId=plan.migration_id,
        microservice=request.microservice.id,
        source=request.source.provider.value,
        destination=request.target.provider.value,
        status=status,
    )
