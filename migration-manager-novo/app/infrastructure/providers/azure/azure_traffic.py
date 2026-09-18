"""Implementacao inicial (stub) de TrafficProvider para Azure/Floci-az.

Mantem um mapa em memoria de service_id -> endpoint atual, simulando um
mecanismo de roteamento (ex: Azure Front Door / Application Gateway) sem
depender de uma integracao real neste momento. Segue exatamente o mesmo
padrao de `AWSTrafficProvider`.
"""
from __future__ import annotations

from app.domain.contracts.traffic_provider import TrafficProvider
from app.domain.models.provider import ProviderConfig
from app.domain.models.deployment import Deployment


class AzureTrafficProvider(TrafficProvider):
    """Provedor de trafego experimental/stub para Azure/Floci-az."""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._routes: dict[str, str] = {}

    def get_current_route(self, service_id: str) -> str:
        return self._routes.get(service_id, "")

    def redirect(self, service_id: str, target_endpoint: str) -> None:
        self._routes[service_id] = target_endpoint

    def validate_route(self, service_id: str, expected_endpoint: str) -> bool:
        return self._routes.get(service_id) == expected_endpoint

    def restore(self, service_id: str, previous_endpoint: str) -> None:
        self._routes[service_id] = previous_endpoint

    def enable_maintenance(self, service_id: str) -> None:
        return None

    def disable_maintenance(self, service_id: str) -> None:
        return None

    def redirect_deployment(self, service_id: str, deployment: Deployment) -> None:
        self.redirect(service_id, deployment.endpoint or "")

    def validate_public_application(self, service_id: str) -> bool:
        return True
