"""Estrategia de migracao continua: prioriza continuidade do servico de origem
ate que o destino esteja implantado e validado.
"""
from __future__ import annotations

import logging

from app.application.migration_context import MigrationContext
from app.application.migration_executor import MigrationExecutor
from app.application.migration_state_machine import MigrationStateMachine
from app.domain.contracts.migration_strategy import MigrationStrategy
from app.domain.enums.migration_state import MigrationState
from app.domain.models.migration import MigrationResult

logger = logging.getLogger(__name__)


class ContinuousMigrationStrategy(MigrationStrategy):
    """Implementa o fluxo:

        prepare -> migrate data -> deploy target -> validate
                -> redirect -> validate -> cleanup source

    O servico de origem permanece ativo e atendendo requisicoes ate que o
    novo servico no destino tenha sido implantado e validado com sucesso.
    Apenas apos o redirecionamento de trafego e a validacao da aplicacao
    e que o servico de origem e encerrado.
    """

    def __init__(self, executor: MigrationExecutor | None = None) -> None:
        self._executor = executor or MigrationExecutor()

    def execute(self, context: MigrationContext) -> MigrationResult:
        sm = MigrationStateMachine(initial_state=context.current_state)
        req = context.request
        service_id = req.microservice.id

        try:
            # Etapa 1 - Preparacao
            self._transition(sm, context, MigrationState.PREPARING)
            self._executor.run(
                context, sm.state, "validate_dependencies",
                lambda: self._require(
                    context.dependency_graph.validate_dependencies(service_id),
                    "Dependencias obrigatorias ausentes no grafo",
                ),
            )
            self._transition(sm, context, MigrationState.PREPARED)

            # Etapa 2 - Migracao dos dados
            self._transition(sm, context, MigrationState.MIGRATING_DATA)
            data_cfg = req.data
            self._executor.run(
                context, sm.state, "prepare_source_data",
                lambda: context.data_provider.prepare_source(data_cfg.source),
            )
            self._executor.run(
                context, sm.state, "prepare_target_data",
                lambda: context.data_provider.prepare_target(data_cfg.target),
            )
            context.data_migration_result = self._executor.run(
                context, sm.state, "migrate_data",
                lambda: context.data_provider.migrate(data_cfg.source, data_cfg.target),
            )
            if not context.data_migration_result.success:
                raise RuntimeError("Falha na migracao dos dados")
            self._transition(sm, context, MigrationState.DATA_MIGRATED)

            # Etapa 3 - Implantacao no destino
            self._transition(sm, context, MigrationState.DEPLOYING_TARGET)
            context.target_deployment = self._executor.run(
                context, sm.state, "deploy_target_service",
                lambda: context.target.cloud_provider.deploy_service(req.microservice),
            )
            self._transition(sm, context, MigrationState.TARGET_DEPLOYED)

            # Etapa 4 - Validacao do destino (servico + dados)
            self._transition(sm, context, MigrationState.VALIDATING_TARGET)
            service_validation = self._executor.run(
                context, sm.state, "validate_target_service",
                lambda: context.target.validation_provider.validate_service(
                    context.target_deployment
                ),
            )
            data_validation = self._executor.run(
                context, sm.state, "validate_migrated_data",
                lambda: context.target.validation_provider.validate_data(
                    data_cfg.source, data_cfg.target
                ),
            )
            if not (service_validation.success and data_validation.success):
                raise RuntimeError("Validacao do destino falhou")
            self._transition(sm, context, MigrationState.TARGET_VALID)

            # Etapa 5 - Redirecionamento de trafego
            self._transition(sm, context, MigrationState.REDIRECTING_TRAFFIC)
            context.original_route = self._executor.run(
                context, sm.state, "get_current_route",
                lambda: context.source.traffic_provider.get_current_route(service_id),
            )
            target_endpoint = self._executor.run(
                context, sm.state, "get_target_endpoint",
                lambda: context.target.cloud_provider.get_service_endpoint(
                    context.target_deployment
                ),
            )
            self._executor.run(
                context, sm.state, "redirect_traffic",
                lambda: context.source.traffic_provider.redirect(service_id, target_endpoint),
            )
            route_ok = self._executor.run(
                context, sm.state, "validate_route",
                lambda: context.source.traffic_provider.validate_route(
                    service_id, target_endpoint
                ),
            )
            if not route_ok:
                raise RuntimeError("Redirecionamento de trafego nao pode ser validado")
            self._transition(sm, context, MigrationState.TRAFFIC_REDIRECTED)

            # Etapa 6 - Validacao da aplicacao
            self._transition(sm, context, MigrationState.VALIDATING_APPLICATION)
            app_validation = self._executor.run(
                context, sm.state, "validate_application",
                lambda: context.target.validation_provider.validate_application(
                    service_id, context.target_deployment, context.dependency_graph
                ),
            )
            if not app_validation.success:
                raise RuntimeError("Validacao da aplicacao apos redirecionamento falhou")
            self._transition(sm, context, MigrationState.MIGRATION_COMPLETED)

            # Etapa 7 - Finalizacao / limpeza da origem
            self._transition(sm, context, MigrationState.SOURCE_CLEANUP)
            if context.source_deployment is not None:
                self._executor.run(
                    context, sm.state, "remove_source_service",
                    lambda: context.source.cloud_provider.remove_service(
                        context.source_deployment
                    ),
                )
            self._executor.run(
                context, sm.state, "cleanup_source_data",
                lambda: context.data_provider.cleanup(data_cfg.source),
            )
            self._transition(sm, context, MigrationState.COMPLETED)

            context.current_state = sm.state
            return MigrationResult(
                migration_id=context.migration_id,
                success=True,
                final_state=sm.state,
                events=context.events,
                message="Migracao concluida com sucesso",
            )

        except Exception as exc:  # noqa: BLE001
            logger.error("Migracao %s falhou: %s", context.migration_id, exc)
            try:
                sm.transition_to(MigrationState.FAILED)
            except Exception:  # noqa: BLE001 - estado ja pode ser terminal
                pass
            context.current_state = MigrationState.FAILED
            return MigrationResult(
                migration_id=context.migration_id,
                success=False,
                final_state=MigrationState.FAILED,
                events=context.events,
                message=str(exc),
            )

    @staticmethod
    def _transition(
        sm: MigrationStateMachine, context: MigrationContext, target: MigrationState
    ) -> None:
        sm.transition_to(target)
        context.current_state = target

    @staticmethod
    def _require(condition: bool, message: str) -> bool:
        if not condition:
            raise RuntimeError(message)
        return condition
