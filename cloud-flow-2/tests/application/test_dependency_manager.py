"""Testes para DependencyManager (construcao do grafo a partir de uma
MigrationRequest / MicroserviceConfig)."""
from app.application.dependency_manager import DependencyManager
from app.domain.models.dependency import Dependency
from app.domain.models.microservice import ContainerConfig, HealthCheckConfig, MicroserviceConfig


def _microservice_with_dependencies() -> MicroserviceConfig:
    return MicroserviceConfig(
        id="ms-b",
        name="ms-b",
        container=ContainerConfig(image="ms-b:1.0.0", port=8080),
        health_check=HealthCheckConfig(path="/health", port=8080),
        dependencies=[
            Dependency(service_id="ms-a", port=8080, required=True),
            Dependency(service_id="ms-c", port=8080, required=True),
        ],
    )


def test_build_graph_registers_declared_dependencies_as_known_services():
    manager = DependencyManager()
    graph = manager.build_graph_for_service(_microservice_with_dependencies())

    assert graph.validate_dependencies("ms-b") is True
    assert {dep.service_id for dep in graph.get_dependencies("ms-b")} == {"ms-a", "ms-c"}
    assert graph.get_dependents("ms-a") == ["ms-b"]


def test_required_dependencies_filters_only_required_ones():
    manager = DependencyManager()
    microservice = _microservice_with_dependencies()
    microservice.dependencies.append(Dependency(service_id="ms-optional", port=8080, required=False))
    graph = manager.build_graph_for_service(microservice)

    required = manager.required_dependencies(graph, "ms-b")
    assert {dep.service_id for dep in required} == {"ms-a", "ms-c"}
