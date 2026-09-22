"""Teste 3 - DependencyGraph: MS-A -> MS-B -> MS-C."""
from app.domain.models.dependency import Dependency, DependencyGraph


def _build_graph() -> DependencyGraph:
    graph = DependencyGraph()
    graph.add_service("ms-a", [Dependency(service_id="ms-b", port=8080)])
    graph.add_service("ms-b", [Dependency(service_id="ms-c", port=8080)])
    graph.add_service("ms-c", [])
    return graph


def test_get_dependencies_of_ms_b_returns_ms_c():
    graph = _build_graph()
    deps = graph.get_dependencies("ms-b")
    assert [dep.service_id for dep in deps] == ["ms-c"]


def test_get_dependents_of_ms_b_returns_ms_a():
    graph = _build_graph()
    assert graph.get_dependents("ms-b") == ["ms-a"]


def test_validate_dependencies_true_when_all_present():
    graph = _build_graph()
    assert graph.validate_dependencies("ms-b") is True


def test_validate_dependencies_false_when_required_dependency_missing():
    graph = DependencyGraph()
    graph.add_service("ms-b", [Dependency(service_id="ms-z", port=8080, required=True)])
    assert graph.validate_dependencies("ms-b") is False
