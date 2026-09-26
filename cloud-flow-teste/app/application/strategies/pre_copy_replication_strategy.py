"""Migracao PostgreSQL com copia base + replicacao logica e corte minimo."""
from __future__ import annotations

import logging

from app.application.migration_context import MigrationContext
from app.application.migration_state_machine import MigrationStateMachine
from app.application.strategies.continuous_migration_strategy import ContinuousMigrationStrategy
from app.domain.enums.migration_state import MigrationState
from app.domain.enums.provider_type import HealthStatus
from app.domain.models.migration import MigrationResult

logger = logging.getLogger(__name__)


class PreCopyReplicationStrategy(ContinuousMigrationStrategy):
    """Copia e sincroniza os dados com a origem servindo trafego normalmente.

    A manutencao so e ligada depois que a replicacao alcancou a origem, para
    drenar o pequeno delta final: o downtime deixa de depender do tamanho do
    banco. Se a replicacao nao convergir, a migracao falha antes de qualquer
    mudanca no trafego.
    """

    def execute(self, context: MigrationContext) -> MigrationResult:
        sm = MigrationStateMachine(initial_state=context.current_state)
        req, service_id = context.request, context.request.microservice.id
        data, options, name = context.data_provider, context.request.replication, context.migration_id
        try:
            self._transition(sm, context, MigrationState.PREPARING)
            self._executor.run(context, sm.state, "validate_dependencies", lambda: self._require(context.dependency_graph.validate_dependencies(service_id), "Dependencias obrigatorias ausentes no grafo"))
            if not req.source_workload:
                raise ValueError("source_workload e obrigatorio para migracao efetiva")
            context.source_deployment = self._executor.run(context, sm.state, "discover_source_workload", lambda: context.source.cloud_provider.discover_service(req.source_workload))
            context.source_database = self._executor.run(context, sm.state, "discover_source_database", lambda: context.source.database_provider.discover(req.data.source))
            self._executor.run(context, sm.state, "check_replication_prerequisites", lambda: data.prepare_source_connection(context.source_database))
            self._transition(sm, context, MigrationState.PREPARED)

            # Copia base com a origem no ar; o slot criado aqui guarda as escritas seguintes.
            self._transition(sm, context, MigrationState.BASE_COPY_IN_PROGRESS)
            context.target_database = self._executor.run(context, sm.state, "provision_target_database", lambda: context.target.database_provider.provision(req.data.target))
            context.replication_started = True
            context.data_migration_result = self._executor.run(context, sm.state, "base_copy", lambda: data.base_copy(context.source_database, context.target_database, name, options))
            self._require(context.data_migration_result.success, "Falha na copia base PostgreSQL")
            self._transition(sm, context, MigrationState.BASE_COPY_DONE)

            self._transition(sm, context, MigrationState.DEPLOYING_TARGET)
            target_config = self._container_config_with_database(context)
            context.target_deployment = self._executor.run(context, sm.state, "deploy_target_service", lambda: context.target.cloud_provider.deploy_service(target_config))
            self._transition(sm, context, MigrationState.TARGET_DEPLOYED)

            self._transition(sm, context, MigrationState.VALIDATING_TARGET)
            target_health = self._executor.run(context, sm.state, "validate_target_health", lambda: context.target.cloud_provider.health_check(context.target_deployment))
            self._require(target_health.status is HealthStatus.HEALTHY, f"Destino nao saudavel: {target_health.detail}")
            self._transition(sm, context, MigrationState.TARGET_VALID)

            # Ainda sem manutencao: timeout aqui nao afeta quem usa a origem.
            self._transition(sm, context, MigrationState.REPLICATING)
            self._executor.run(context, sm.state, "start_replication", lambda: data.start_replication(context.source_database, context.target_database, name, options))
            context.replication_lag = self._executor.run(context, sm.state, "wait_replication_sync", lambda: data.wait_until_synced(context.source_database, name, options))
            self._transition(sm, context, MigrationState.REPLICATION_SYNCED)

            # Corte: so o delta final roda com a manutencao ligada.
            self._transition(sm, context, MigrationState.QUIESCING_SOURCE)
            context.downtime.start()
            context.original_route = self._executor.run(context, sm.state, "get_original_route", lambda: context.traffic_provider.get_current_route(service_id))
            self._executor.run(context, sm.state, "enable_maintenance", lambda: context.traffic_provider.enable_maintenance(service_id))
            context.maintenance_enabled = True

            self._transition(sm, context, MigrationState.MIGRATING_DATA)
            drained = self._executor.run(context, sm.state, "drain_final_delta", lambda: data.drain_final_delta(context.source_database, context.target_database, name, options))
            self._require(drained.success, "Falha ao drenar o delta final da replicacao")
            self._executor.run(context, sm.state, "stop_replication", lambda: data.stop_replication(context.source_database, context.target_database, name))
            context.replication_stopped = True
            data_validation = self._executor.run(context, sm.state, "validate_data", lambda: data.validate_connections(context.source_database, context.target_database))
            self._require(data_validation.success, "Schema ou contagem de linhas divergente no destino")
            self._transition(sm, context, MigrationState.DATA_MIGRATED)

            self._transition(sm, context, MigrationState.REDIRECTING_TRAFFIC)
            self._executor.run(context, sm.state, "redirect_gateway_route", lambda: context.traffic_provider.redirect_deployment(service_id, context.target_deployment))
            self._require(self._executor.run(context, sm.state, "validate_gateway_route", lambda: context.traffic_provider.validate_route(service_id, context.target_deployment.endpoint or "")), "Rota do Gateway nao aponta ao destino")
            self._executor.run(context, sm.state, "disable_maintenance", lambda: context.traffic_provider.disable_maintenance(service_id))
            context.maintenance_enabled = False
            self._transition(sm, context, MigrationState.TRAFFIC_REDIRECTED)

            self._transition(sm, context, MigrationState.VALIDATING_APPLICATION)
            application = self._executor.run(context, sm.state, "validate_application", lambda: context.target.validation_provider.validate_application(service_id, context.target_deployment, context.dependency_graph))
            gateway_application = self._executor.run(context, sm.state, "validate_public_application", lambda: context.traffic_provider.validate_public_application(service_id))
            self._require(application.success and gateway_application, "Validacao da aplicacao apos o corte falhou")
            context.downtime.stop()
            self._transition(sm, context, MigrationState.MIGRATION_COMPLETED)

            self._transition(sm, context, MigrationState.SOURCE_CLEANUP)
            self._executor.run(context, sm.state, "stop_source_workload", lambda: context.source.cloud_provider.stop_service(context.source_deployment))
            context.source_was_stopped = True
            self._transition(sm, context, MigrationState.COMPLETED)
            return MigrationResult(migration_id=context.migration_id, success=True, final_state=sm.state, events=context.events, message="Migracao concluida; origem parada e retida para rollback")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Migracao pre_copy_replication %s falhou", context.migration_id)
            self._compensate(context, service_id)
            context.downtime.stop()
            try:
                sm.transition_to(MigrationState.FAILED)
            except Exception:
                pass
            context.current_state = MigrationState.FAILED
            return MigrationResult(migration_id=context.migration_id, success=False, final_state=MigrationState.FAILED, events=context.events, message=str(exc))

    def _compensate(self, context: MigrationContext, service_id: str) -> None:
        """Devolve o trafego a origem antes de desmontar replicacao e destino."""
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
            context.maintenance_enabled = False
        if context.source_was_stopped and context.source_deployment:
            try:
                context.source.cloud_provider.start_service(context.source_deployment)
            except Exception:
                logger.exception("Falha ao reativar origem")
        # Antes de remover o banco de destino: um slot esquecido retem WAL na origem.
        if context.replication_started and not context.replication_stopped and context.source_database:
            try:
                context.data_provider.stop_replication(context.source_database, context.target_database, context.migration_id)
                context.replication_stopped = True
            except Exception:
                logger.exception("Falha ao encerrar replicacao")
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
