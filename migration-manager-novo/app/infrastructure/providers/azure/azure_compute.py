"""Azure Container Apps real atraves da API ARM do Floci-AZ."""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

from app.domain.contracts.compute_provider import ComputeProvider
from app.domain.enums.provider_type import HealthStatus
from app.domain.models.deployment import Deployment, HealthCheckResult
from app.domain.models.microservice import ContainerConfig
from app.domain.models.migration import WorkloadReference
from app.domain.models.provider import ProviderConfig

API_VERSION = "2025-07-01"


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", value.lower()).strip("-")[:60]


class AzureContainerAppsComputeProvider(ComputeProvider):
    """Cria Container Apps reais (replicas Docker) no Floci-AZ em real mode."""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._endpoint_url = (config.endpoint_override or os.getenv("AZURE_ENDPOINT_URL", "http://floci-az:4577")).rstrip("/")
        options = config.runtime_options
        self._subscription = options.get("subscription_id", "00000000-0000-0000-0000-000000000000")
        self._resource_group = options.get("resource_group", "floci-migrations")
        self._environment = options.get("managed_environment", "floci-migrations")

    def deploy(self, config: ContainerConfig) -> Deployment:
        service_id, app_name = self._service_id(config), _safe_name(self._config.runtime_options.get("container_app_name", self._service_id(config)))
        self._ensure_environment()
        app = self._request("PUT", self._app_path(app_name), json={
            "location": self._config.region,
            "properties": {"managedEnvironmentId": self._environment_path(),
                "configuration": {"ingress": {"external": True, "targetPort": config.port, "transport": "auto"}},
                "template": {"containers": [{"name": app_name, "image": config.image,
                    "env": [{"name": key, "value": value} for key, value in config.environment.items()]}],
                    "scale": {"minReplicas": 1, "maxReplicas": 1}}},
        }).json()
        fqdn = self._fqdn(app)
        if not fqdn:
            app = self._request("GET", self._app_path(app_name)).json()
            fqdn = self._fqdn(app)
        if not fqdn:
            raise RuntimeError("Container App criado sem FQDN de ingress")
        return self._deployment(app, service_id, app_name, fqdn)

    def discover(self, reference: WorkloadReference) -> Deployment:
        app_name = reference.resource_id.rsplit("/", 1)[-1]
        app = self._request("GET", self._app_path(app_name)).json()
        fqdn = self._fqdn(app)
        if not fqdn:
            raise RuntimeError(f"Container App sem ingress: {app_name}")
        return self._deployment(app, self._config.runtime_options.get("service_id", app_name), app_name, fqdn)

    def start(self, deployment: Deployment) -> None:
        revision = deployment.metadata.get("revision")
        if not revision:
            raise RuntimeError("Deployment Azure sem revision para reativacao")
        self._request("POST", f"{self._app_path(self._app_name(deployment))}/revisions/{revision}/activate")

    def stop(self, deployment: Deployment) -> None:
        revision = deployment.metadata.get("revision")
        if not revision:
            app = self._request("GET", self._app_path(self._app_name(deployment))).json()
            revision = app.get("properties", {}).get("latestReadyRevisionName")
        if not revision:
            raise RuntimeError("Container App sem revision ativa para parar")
        self._request("POST", f"{self._app_path(self._app_name(deployment))}/revisions/{revision}/deactivate")

    def remove(self, deployment: Deployment) -> None:
        self._request("DELETE", self._app_path(self._app_name(deployment)))

    def get_endpoint(self, deployment: Deployment) -> str:
        return deployment.endpoint or self._endpoint_url

    def health_check(self, deployment: Deployment) -> HealthCheckResult:
        try:
            response = httpx.get(f"{self.get_endpoint(deployment)}{deployment.metadata.get('health_path', '/health')}", headers={"Host": deployment.metadata["route_host"]}, timeout=float(self._config.runtime_options.get("health_timeout_seconds", "8")))
            return HealthCheckResult(status=HealthStatus.HEALTHY if response.is_success else HealthStatus.UNHEALTHY, detail=f"HTTP {response.status_code}")
        except (httpx.HTTPError, KeyError) as exc:
            return HealthCheckResult(status=HealthStatus.UNHEALTHY, detail=str(exc))

    def _deployment(self, app: dict[str, Any], service_id: str, app_name: str, fqdn: str) -> Deployment:
        return Deployment(deployment_id=app.get("id", self._app_path(app_name)), service_id=service_id, endpoint=self._config.runtime_options.get("gateway_endpoint", self._endpoint_url), provider="azure", region=self._config.region,
            metadata={"container_app_name": app_name, "resource_group": self._resource_group, "subscription_id": self._subscription, "managed_environment": self._environment, "route_host": fqdn, "revision": app.get("properties", {}).get("latestReadyRevisionName", ""), "health_path": self._config.runtime_options.get("health_path", "/health")})

    def _ensure_environment(self) -> None:
        self._request("PUT", self._environment_path(), json={"location": self._config.region, "properties": {}})

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = httpx.request(method, f"{self._endpoint_url}{path}", params={"api-version": API_VERSION}, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"ARM {method} {path} falhou ({response.status_code}): {response.text}")
        return response

    def _environment_path(self) -> str:
        return f"/subscriptions/{self._subscription}/resourceGroups/{self._resource_group}/providers/Microsoft.App/managedEnvironments/{self._environment}"

    def _app_path(self, app_name: str) -> str:
        return f"/subscriptions/{self._subscription}/resourceGroups/{self._resource_group}/providers/Microsoft.App/containerApps/{app_name}"

    @staticmethod
    def _fqdn(app: dict[str, Any]) -> str | None:
        return app.get("properties", {}).get("configuration", {}).get("ingress", {}).get("fqdn")

    @staticmethod
    def _service_id(config: ContainerConfig) -> str:
        return config.image.rsplit("/", 1)[-1].split(":", 1)[0]

    @staticmethod
    def _app_name(deployment: Deployment) -> str:
        return deployment.metadata.get("container_app_name", deployment.service_id)


AzureContainerComputeProvider = AzureContainerAppsComputeProvider
