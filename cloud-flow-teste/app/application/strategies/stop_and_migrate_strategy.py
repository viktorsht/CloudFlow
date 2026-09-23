"""Migracao com indisponibilidade controlada: para a origem antes do corte."""
from __future__ import annotations

import logging

from app.application.migration_context import MigrationContext
from app.application.migration_executor import MigrationExecutor
from app.application.migration_state_machine import MigrationStateMachine
from app.domain.contracts.migration_strategy import MigrationStrategy
from app.domain.enums.migration_state import MigrationState
from app.domain.enums.provider_type import HealthStatus
from app.domain.models.migration import MigrationResult

logger = logging.getLogger(__name__)


class StopAndMigrateStrategy(MigrationStrategy):
    """Move um microsservico depois de interromper a origem e suas escritas.

    O banco de destino e provisionado antes da janela de indisponibilidade;
    o workload de destino so e iniciado depois do dump/restore final.
    """

    def __init__(self, executor: MigrationExecutor | None = None) -> None:
        self._executor = executor or MigrationExecutor()

    def execute(self, context: MigrationContext) -> MigrationResult:
        sm = MigrationStateMachine(context.current_state)
        request, service_id = context.request, context.request.microservice.id
        try:
            self._transition(sm, context, MigrationState.PREPARING)
            self._executor.run(context, sm.state, "validate_dependencies", lambda: self._require(context.dependency_graph.validate_dependencies(service_id), "Dependencias obrigatorias ausentes no grafo"))
            if request.source_workload is None:
                raise ValueError("source_workload e obrigatorio para stop_and_migrate")
            context.source_deployment = self._executor.run(context, sm.state, "discover_source_workload", lambda: context.source.cloud_provider.discover_service(request.source_workload))
            context.source_database = self._executor.run(context, sm.state, "discover_source_database", lambda: context.source.database_provider.discover(request.data.source))
            self._transition(sm, context, MigrationState.PREPARED)

            self._transition(sm, context, MigrationState.DEPLOYING_TARGET)
            context.target_database = self._executor.run(context, sm.state, "provision_target_database", lambda: context.target.database_provider.provision(request.data.target))
            self._transition(sm, context, MigrationState.TARGET_DATABASE_READY)

            self._transition(sm, context, MigrationState.QUIESCING_SOURCE)
            context.downtime.start()
            context.original_route = self._executor.run(context, sm.state, "get_original_route", lambda: context.traffic_provider.get_current_route(service_id))
            self._executor.run(context, sm.state, "enable_maintenance", lambda: context.traffic_provider.enable_maintenance(service_id))
            context.maintenance_enabled = True
            self._executor.run(context, sm.state, "stop_source_workload", lambda: context.source.cloud_provider.stop_service(context.source_deployment))
            context.source_was_stopped = True
            self._transition(sm, context, MigrationState.SOURCE_STOPPED)

            self._transition(sm, context, MigrationState.MIGRATING_DATA)
            context.data_migration_result = self._executor.run(context, sm.state, "dump_restore_final", lambda: context.data_provider.migrate_connections(context.source_database, context.target_database))
            self._require(context.data_migration_result.success, "Falha no dump/restore PostgreSQL")
            validated = self._executor.run(context, sm.state, "validate_data", lambda: context.data_provider.validate_connections(context.source_database, context.target_database))
            self._require(validated.success, "Schema ou contagem de linhas divergente no destino")
            self._transition(sm, context, MigrationState.DATA_MIGRATED)

            self._transition(sm, context, MigrationState.DEPLOYING_TARGET)
            target_config = self._container_config_with_database(context)
            context.target_deployment = self._executor.run(context, sm.state, "deploy_target_service", lambda: context.target.cloud_provider.deploy_service(target_config))
            self._transition(sm, context, MigrationState.TARGET_DEPLOYED)
            self._transition(sm, context, MigrationState.VALIDATING_TARGET)
            health = self._executor.run(context, sm.state, "validate_target_health", lambda: context.target.cloud_provider.health_check(context.target_deployment))
            self._require(health.status is HealthStatus.HEALTHY, f"Destino nao saudavel: {health.detail}")
            self._transition(sm, context, MigrationState.TARGET_VALID)

            self._transition(sm, context, MigrationState.REDIRECTING_TRAFFIC)
            self._executor.run(context, sm.state, "redirect_gateway_route", lambda: context.traffic_provider.redirect_deployment(service_id, context.target_deployment))
            self._require(self._executor.run(context, sm.state, "validate_gateway_route", lambda: context.traffic_provider.validate_route(service_id, context.target_deployment.endpoint or "")), "Rota do Gateway nao aponta ao destino")
            self._executor.run(context, sm.state, "disable_maintenance", lambda: context.traffic_provider.disable_maintenance(service_id))
            context.maintenance_enabled = False
            self._transition(sm, context, MigrationState.TRAFFIC_REDIRECTED)
            self._transition(sm, context, MigrationState.VALIDATING_APPLICATION)
            application = self._executor.run(context, sm.state, "validate_application", lambda: context.target.validation_provider.validate_application(service_id, context.target_deployment, context.dependency_graph))
            public = self._executor.run(context, sm.state, "validate_public_application", lambda: context.traffic_provider.validate_public_application(service_id))
            self._require(application.success and public, "Validacao da aplicacao apos o corte falhou")
            context.downtime.stop()
            self._transition(sm, context, MigrationState.MIGRATION_COMPLETED)
            self._transition(sm, context, MigrationState.SOURCE_CLEANUP)
            self._transition(sm, context, MigrationState.COMPLETED)
            return MigrationResult(migration_id=context.migration_id, success=True, final_state=sm.state, events=context.events, message="Migracao concluida; origem parada e retida para rollback")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Migracao stop_and_migrate %s falhou", context.migration_id)
            self._compensate(context, service_id)
            context.downtime.stop()
            context.current_state = MigrationState.FAILED
            return MigrationResult(migration_id=context.migration_id, success=False, final_state=MigrationState.FAILED, events=context.events, message=str(exc))

    def _compensate(self, context: MigrationContext, service_id: str) -> None:
        if context.original_route:
            try:
                context.traffic_provider.restore(service_id, context.original_route)
            except Exception:
                logger.exception("Falha ao restaurar rota original")
        if context.maintenance_enabled:
            try:
                context.traffic_provider.disable_maintenance(service_id)
            except Exception:
                logger.exception("Falha ao retirar manutencao")
        if context.source_was_stopped and context.source_deployment:
            try:
                context.source.cloud_provider.start_service(context.source_deployment)
            except Exception:
                logger.exception("Falha ao reativar origem")
        if context.target_deployment:
            try:
                context.target.cloud_provider.remove_service(context.target_deployment)
            except Exception:
                logger.exception("Falha ao remover workload de destino")
        if context.target_database:
            try:
                context.target.database_provider.remove(context.target_database)
            except Exception:
                logger.exception("Falha ao remover banco de destino")

    @staticmethod
    def _container_config_with_database(context: MigrationContext):
        database = context.target_database
        assert database is not None
        container = context.request.microservice.container.model_copy(deep=True)
        container.environment.update({"DB_HOST": database.host, "DB_PORT": str(database.port), "DB_NAME": database.database, "DB_USER": database.username, "DB_PASSWORD": database.password})
        return context.request.microservice.model_copy(update={"container": container})

    @staticmethod
    def _transition(sm: MigrationStateMachine, context: MigrationContext, target: MigrationState) -> None:
        sm.transition_to(target)
        context.current_state = target

    @staticmethod
    def _require(condition: bool, message: str) -> bool:
        if not condition:
            raise RuntimeError(message)
        return condition
