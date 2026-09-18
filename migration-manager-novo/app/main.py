"""Ponto de entrada da aplicacao FastAPI do MigrationManager."""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.controllers.migration_controller import router as migration_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

app = FastAPI(
    title="MigrationManager",
    description="Orquestrador de migracao seletiva de microsservicos multi-cloud.",
    version="0.1.0",
)

app.include_router(migration_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
