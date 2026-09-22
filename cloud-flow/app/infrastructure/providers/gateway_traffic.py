"""Controle de rotas do Spring Cloud Gateway pelo actuator."""
from __future__ import annotations

import httpx

from app.domain.contracts.traffic_provider import TrafficProvider
from app.domain.models.deployment import Deployment
from app.domain.models.migration import IngressConfig


class GatewayTrafficProvider(TrafficProvider):
    def __init__(self, ingress: IngressConfig) -> None:
        self._ingress = ingress
        self._base = ingress.gateway_admin_url.rstrip("/")
        self._routes: dict[str, dict] = {}

    def get_current_route(self, service_id: str) -> str:
        route = self._get_route(self._route_id(service_id))
        self._routes[service_id] = route
        return route.get("uri", "")

    def redirect(self, service_id: str, target_endpoint: str) -> None:
        route = self._route_definition(service_id, target_endpoint)
        route["order"] = -10
        self._put_route(self._migration_id(service_id), route)

    def redirect_deployment(self, service_id: str, deployment: Deployment) -> None:
        route = self._route_definition(service_id, deployment.endpoint or "")
        route_host = deployment.metadata.get("route_host")
        if route_host:
            route["filters"].append({"name": "SetRequestHostHeader", "args": {"_genkey_0": route_host}})
        route["order"] = -10
        self._put_route(self._migration_id(service_id), route)

    def validate_public_application(self, service_id: str) -> bool:
        public_base = self._base.removesuffix("/actuator/gateway")
        try:
            response = httpx.post(f"{public_base}{self._ingress.public_path.removesuffix('/**')}/api/process", timeout=15)
            return response.is_success
        except httpx.HTTPError:
            return False

    def validate_route(self, service_id: str, expected_endpoint: str) -> bool:
        return self._get_route(self._migration_id(service_id)).get("uri") == expected_endpoint

    def restore(self, service_id: str, previous_endpoint: str) -> None:
        # Rotas declaradas no application.yml nao sao mutaveis pelo actuator.
        # Remover nossa sobreposicao devolve o trafego a definicao original.
        response = httpx.delete(f"{self._base}/routes/{self._migration_id(service_id)}", timeout=10)
        if response.status_code not in (200, 404):
            raise RuntimeError(f"Nao foi possivel restaurar rota: HTTP {response.status_code} {response.text}")
        self._refresh()

    def enable_maintenance(self, service_id: str) -> None:
        maintenance = self._route_definition(service_id, "no://op")
        maintenance["id"] = self._maintenance_id(service_id)
        maintenance["order"] = -100
        maintenance["filters"] = [{"name": "SetStatus", "args": {"_genkey_0": "503"}}]
        self._put_route(self._maintenance_id(service_id), maintenance)

    def disable_maintenance(self, service_id: str) -> None:
        response = httpx.delete(f"{self._base}/routes/{self._maintenance_id(service_id)}", timeout=10)
        if response.status_code not in (200, 404):
            raise RuntimeError(f"Nao foi possivel remover manutencao: HTTP {response.status_code} {response.text}")
        self._refresh()

    def _route_definition(self, service_id: str, endpoint: str) -> dict:
        return {"id": self._route_id(service_id), "uri": endpoint, "predicates": [{"name": "Path", "args": {"_genkey_0": self._ingress.public_path}}], "filters": [{"name": "StripPrefix", "args": {"_genkey_0": "1"}}], "order": 0}

    def _put_route(self, route_id: str, route: dict) -> None:
        route["id"] = route_id
        response = httpx.post(f"{self._base}/routes/{route_id}", json=route, timeout=10)
        if response.status_code >= 300:
            raise RuntimeError(f"Nao foi possivel atualizar rota {route_id}: HTTP {response.status_code} {response.text}")
        self._refresh()

    def _get_route(self, route_id: str) -> dict:
        # GET /routes/{id} falha quando um RouteDefinitionLocator possui
        # duplicatas de id. A listagem de routedefinitions e estavel.
        response = httpx.get(f"{self._base}/routedefinitions", timeout=10)
        if response.status_code >= 300:
            raise RuntimeError(f"Nao foi possivel listar rotas: HTTP {response.status_code} {response.text}")
        matches = [route for route in response.json() if route.get("id") == route_id]
        if not matches:
            raise RuntimeError(f"Rota {route_id} nao encontrada")
        return matches[0]

    def _refresh(self) -> None:
        response = httpx.post(f"{self._base}/refresh", timeout=10)
        if response.status_code >= 300:
            raise RuntimeError(f"Falha ao recarregar rotas do Gateway: HTTP {response.status_code}")

    def _route_id(self, service_id: str) -> str:
        return self._ingress.route_id

    def _maintenance_id(self, service_id: str) -> str:
        return f"{self._route_id(service_id)}-maintenance"

    def _migration_id(self, service_id: str) -> str:
        return f"{self._route_id(service_id)}-migration"
