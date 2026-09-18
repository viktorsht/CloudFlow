"""Controller REST para operacoes de migracao."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.application.migration_manager import MigrationManager
from app.domain.models.data import ValidationResult
from app.domain.models.deployment import RollbackResult
from app.domain.models.migration import MigrationRequest, MigrationResult

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
