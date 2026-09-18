"""MigrationManager: orquestrador principal do processo de migracao.

Este e o unico ponto de coordenacao de alto nivel do sistema. Ele NAO
executa operacoes de infraestrutura diretamente (Docker, AWS, SQL, HTTP,
DNS): apenas resolve providers via ProviderFactory e delega a execucao para
uma MigrationStrategy, operando exclusivamente sobre os contratos de dominio.
"""
from __future__ import annotations

import logging

from app.application.dependency_manager import DependencyManager
from app.application.migration_context import MigrationContext, ProviderBundle
from app.domain.contracts.migration_strategy import MigrationStrategy
from app.domain.enums.migration_state import MigrationState
from app.domain.models.deployment import RollbackResult
from app.domain.models.migration import (
    MigrationPlan,
    MigrationRequest,
    MigrationResult,
)
from app.domain.models.data import ValidationResult
from app.infrastructure.providers.factory import ProviderFactory

logger = logging.getLogger(__name__)


class MigrationManager:
    """Orquestrador de migracoes de microsservicos multi-cloud.

    Responsabilidades:
      * validar e planejar uma migracao (`prepare`);
      * executar a migracao delegando para uma MigrationStrategy (`migrate`);
      * validar o estado de uma migracao em andamento/concluida (`validate`);
      * coordenar o rollback em caso de falha (`rollback`).

    O MigrationManager decide O QUE, QUANDO e EM QUE ORDEM fazer. Os
    providers (resolvidos via ProviderFactory) decidem COMO executar cada
    operacao no ambiente concreto.
    """

    def __init__(
        self,
        provider_factory: ProviderFactory | None = None,
        strategy: MigrationStrategy | None = None,
        dependency_manager: DependencyManager | None = None,
    ) -> None:
        self._provider_factory = provider_factory or ProviderFactory()
        self._dependency_manager = dependency_manager or DependencyManager()
        if strategy is None:
            # Import local para evitar dependencia circular no modulo.
            from app.application.strategies.continuous_migration_strategy import (
                ContinuousMigrationStrategy,
            )

            strategy = ContinuousMigrationStrategy()
        self._strategy = strategy
        # Contextos ficam em memoria por processo; uma implementacao real
        # poderia persisti-los em um repositorio (ver infrastructure/persistence).
        self._contexts: dict[str, MigrationContext] = {}

    # -- ciclo de vida publico -----------------------------------------

    def prepare(self, request: MigrationRequest) -> MigrationPlan:
        """Valida a solicitacao e monta o contexto/plano de execucao."""
        graph = self._dependency_manager.build_graph_for_service(request.microservice)

        source_bundle = self._build_provider_bundle(request.source.provider, request.source)
        target_bundle = self._build_provider_bundle(request.target.provider, request.target)
        data_provider = self._provider_factory.create_data_provider(request.data.type)

        context = MigrationContext(
            request=request,
            source=source_bundle,
            target=target_bundle,
            data_provider=data_provider,
            dependency_graph=graph,
            current_state=MigrationState.PENDING,
        )
        self._contexts[request.migration_id] = context

        notes = [
            f"{dep.service_id} (obrigatoria)" if dep.required else f"{dep.service_id} (opcional)"
            for dep in request.microservice.dependencies
        ]
        return MigrationPlan(
            migration_id=request.migration_id,
            request=request,
            dependency_order_notes=notes,
        )

    def migrate(self, plan: MigrationPlan) -> MigrationResult:
        """Executa a migracao descrita pelo plano, delegando para a estrategia."""
        context = self._get_context(plan.migration_id)
        result = self._strategy.execute(context)
        return result

    def validate(self, plan: MigrationPlan) -> ValidationResult:
        """Executa validacoes sobre o estado atual da migracao."""
        context = self._get_context(plan.migration_id)
        checks: dict[str, bool] = {}

        if context.target_deployment is not None:
            service_result = context.target.validation_provider.validate_service(
                context.target_deployment
            )
            checks["target_service"] = service_result.success

        data_cfg = context.request.data
        data_result = context.target.validation_provider.validate_data(
            data_cfg.source, data_cfg.target
        )
        checks["data"] = data_result.success

        overall_success = all(checks.values()) if checks else False
        return ValidationResult(
            success=overall_success,
            checks=checks,
            message="Validacao agregada do estado atual da migracao",
        )

    def rollback(self, plan: MigrationPlan) -> RollbackResult:
        """Coordena o rollback de uma migracao com falha.

        Restaura o trafego para a origem, valida a origem, remove o
        deployment de destino e desfaz a migracao de dados, preservando o
        estado original do sistema.
        """
        context = self._get_context(plan.migration_id)
        service_id = context.request.microservice.id
        restored_route = False
        target_removed = False
        message_parts: list[str] = []

        try:
            if context.original_route:
                context.source.traffic_provider.restore(service_id, context.original_route)
                restored_route = context.source.traffic_provider.validate_route(
                    service_id, context.original_route
                )
        except Exception as exc:  # noqa: BLE001
            message_parts.append(f"Falha ao restaurar trafego: {exc}")

        try:
            if context.target_deployment is not None:
                context.target.cloud_provider.remove_service(context.target_deployment)
                target_removed = True
        except Exception as exc:  # noqa: BLE001
            message_parts.append(f"Falha ao remover deployment de destino: {exc}")

        try:
            context.data_provider.rollback(context.request.data.target)
        except Exception as exc:  # noqa: BLE001
            message_parts.append(f"Falha ao reverter migracao de dados: {exc}")

        context.current_state = MigrationState.ROLLED_BACK
        success = restored_route and (target_removed or context.target_deployment is None)

        logger.info(
            "Rollback da migracao %s concluido: sucesso=%s", plan.migration_id, success
        )

        return RollbackResult(
            success=success,
            restored_route=restored_route,
            target_removed=target_removed,
            message="; ".join(message_parts) or "Rollback concluido",
        )

    def get_state(self, migration_id: str) -> MigrationState:
        """Retorna o estado atual (em memoria) de uma migracao."""
        return self._get_context(migration_id).current_state

    def get_events(self, migration_id: str) -> list:
        """Retorna os eventos registrados ate o momento para uma migracao."""
        return list(self._get_context(migration_id).events)

    # -- internos ---------------------------------------------------------

    def _build_provider_bundle(self, provider_type, provider_config) -> ProviderBundle:
        cloud_provider = self._provider_factory.create_cloud_provider(provider_config)
        traffic_provider = self._provider_factory.create_traffic_provider(provider_config)
        validation_provider = self._provider_factory.create_validation_provider(provider_config)
        return ProviderBundle(
            cloud_provider=cloud_provider,
            traffic_provider=traffic_provider,
            validation_provider=validation_provider,
        )

    def _get_context(self, migration_id: str) -> MigrationContext:
        context = self._contexts.get(migration_id)
        if context is None:
            raise KeyError(f"Nenhum contexto encontrado para migration_id={migration_id}")
        return context
