"""ECS real sobre a API AWS exposta pelo Floci."""
from __future__ import annotations

import re
from typing import Any

import boto3
import httpx
import docker

from app.domain.contracts.compute_provider import ComputeProvider
from app.domain.enums.provider_type import HealthStatus
from app.domain.models.deployment import Deployment, HealthCheckResult
from app.domain.models.microservice import ContainerConfig
from app.domain.models.migration import WorkloadReference
from app.domain.models.provider import AwsCredentials, ProviderConfig


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9-]", "-", value).strip("-")[:120]


class ECSComputeProvider(ComputeProvider):
    """Registra uma task bridge-mode por microsservico no ECS do Floci."""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._endpoint_url = config.endpoint
        self._cluster = config.runtime_options.get("cluster", "default")
        credentials = config.credentials
        if not isinstance(credentials, AwsCredentials):
            raise ValueError("Credenciais AWS obrigatorias para provider aws")
        self._client = boto3.client(
            "ecs", region_name=config.region, endpoint_url=self._endpoint_url,
            aws_access_key_id=credentials.access_key_id,
            aws_secret_access_key=credentials.secret_access_key,
            aws_session_token=credentials.session_token,
        )

    def deploy(self, config: ContainerConfig) -> Deployment:
        service_id = self._service_id(config)
        container_name = _safe_name(service_id)
        host_port = self._host_port()
        response = self._client.register_task_definition(
            family=_safe_name(self._config.runtime_options.get("task_family", f"migration-{service_id}")),
            networkMode="bridge", requiresCompatibilities=["EC2"],
            containerDefinitions=[{
                "name": container_name, "image": config.image, "essential": True,
                "memory": int(self._config.runtime_options.get("memory", "512")),
                "environment": [{"name": key, "value": value} for key, value in config.environment.items()],
                "portMappings": [{"containerPort": config.port, "hostPort": host_port, "protocol": "tcp"}],
            }],
        )
        task_definition = response["taskDefinition"]["taskDefinitionArn"]
        task = self._run_task(task_definition)
        task_arn = task["taskArn"]
        return self._deployment(task_arn, task_definition, service_id, container_name, host_port, config.port)

    def discover(self, reference: WorkloadReference) -> Deployment:
        task = self._describe_task(reference.resource_id)
        containers = task.get("containers", [])
        container = next((item for item in containers if item.get("name") == reference.container_name), containers[0] if containers else None)
        if not container:
            raise RuntimeError("Container da task ECS nao encontrado")
        bindings = container.get("networkBindings") or []
        host_port = reference.port or self._int_option("host_port")
        if bindings:
            host_port = int(bindings[0].get("hostPort") or host_port or 0)
        if not host_port:
            raise RuntimeError("Nao foi possivel resolver a porta publicada da task ECS")
        deployment = self._deployment(
            reference.resource_id, task.get("taskDefinitionArn", ""),
            self._config.runtime_options.get("service_id", container.get("name", "service")),
            container.get("name", "service"), host_port, 0,
        )
        if reference.container_only:
            runtime_id = container.get("runtimeId") or self._find_runtime_id(reference, container.get("name", ""))
            if not runtime_id:
                raise RuntimeError("ECS nao retornou runtimeId do container para parada seletiva")
            deployment.metadata["runtime_id"] = runtime_id
            deployment.metadata["container_only"] = "true"
        return deployment

    def start(self, deployment: Deployment) -> None:
        if deployment.metadata.get("container_only") == "true":
            self._docker_container(deployment).start()
            return
        task_definition = deployment.metadata.get("task_definition")
        if not task_definition:
            raise RuntimeError("Deployment ECS sem task_definition para reativacao")
        task = self._run_task(task_definition)
        deployment.deployment_id = task["taskArn"]
        deployment.metadata["task_arn"] = deployment.deployment_id

    def stop(self, deployment: Deployment) -> None:
        if deployment.metadata.get("container_only") == "true":
            self._docker_container(deployment).stop(timeout=20)
            return
        self._client.stop_task(cluster=deployment.metadata.get("cluster", self._cluster), task=deployment.metadata.get("task_arn", deployment.deployment_id))

    def remove(self, deployment: Deployment) -> None:
        if deployment.metadata.get("container_only") == "true":
            self._docker_container(deployment).remove(force=True)
            return
        try:
            self.stop(deployment)
        except Exception:
            pass
        task_definition = deployment.metadata.get("task_definition")
        if task_definition:
            self._client.deregister_task_definition(taskDefinition=task_definition)

    def get_endpoint(self, deployment: Deployment) -> str:
        return deployment.endpoint or ""

    def health_check(self, deployment: Deployment) -> HealthCheckResult:
        try:
            response = httpx.get(
                f"{self.get_endpoint(deployment).rstrip('/')}{deployment.metadata.get('health_path', '/health')}",
                timeout=float(self._config.runtime_options.get("health_timeout_seconds", "5")),
            )
            return HealthCheckResult(status=HealthStatus.HEALTHY if response.is_success else HealthStatus.UNHEALTHY, detail=f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            return HealthCheckResult(status=HealthStatus.UNHEALTHY, detail=str(exc))

    def _deployment(self, task_arn: str, task_definition: str, service_id: str, container_name: str, host_port: int, container_port: int) -> Deployment:
        return Deployment(
            deployment_id=task_arn, service_id=service_id,
            endpoint=f"http://{self._config.runtime_options.get('published_host', 'host.docker.internal')}:{host_port}",
            provider="aws", region=self._config.region,
            metadata={"task_arn": task_arn, "task_definition": task_definition, "cluster": self._cluster,
                      "container_name": container_name, "container_port": str(container_port), "host_port": str(host_port),
                      "health_path": self._config.runtime_options.get("health_path", "/health")},
        )

    def _run_task(self, task_definition: str) -> dict[str, Any]:
        response = self._client.run_task(cluster=self._cluster, taskDefinition=task_definition, count=1)
        if response.get("failures") or not response.get("tasks"):
            raise RuntimeError(f"Falha ao iniciar task ECS: {response.get('failures', [])}")
        return response["tasks"][0]

    def _describe_task(self, task_arn: str) -> dict[str, Any]:
        response = self._client.describe_tasks(cluster=self._cluster, tasks=[task_arn])
        if not response.get("tasks"):
            raise RuntimeError(f"Task ECS nao encontrada: {task_arn}")
        return response["tasks"][0]

    def _host_port(self) -> int:
        port = self._int_option("host_port")
        if not port:
            raise ValueError("runtime_options.host_port e obrigatorio para o destino ECS")
        return port

    def _int_option(self, name: str) -> int | None:
        value = self._config.runtime_options.get(name)
        return int(value) if value else None

    @staticmethod
    def _docker_container(deployment: Deployment):
        runtime_id = deployment.metadata.get("runtime_id")
        if not runtime_id:
            raise RuntimeError("Deployment sem runtime_id Docker")
        return docker.from_env().containers.get(runtime_id)

    @staticmethod
    def _find_runtime_id(reference: WorkloadReference, container_name: str) -> str | None:
        """Fallback para Floci que ainda nao preenche runtimeId no ECS."""
        task_fragment = reference.resource_id.rsplit("/", 1)[-1]
        suffix = f"-{container_name}"
        for candidate in docker.from_env().containers.list(all=True):
            name = candidate.name
            if task_fragment in name and name.endswith(suffix):
                return candidate.id
        return None

    @staticmethod
    def _service_id(config: ContainerConfig) -> str:
        return config.image.rsplit("/", 1)[-1].split(":", 1)[0]


DockerComputeProvider = ECSComputeProvider
