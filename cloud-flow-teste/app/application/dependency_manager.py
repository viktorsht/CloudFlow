"""Servico de aplicacao responsavel por construir e consultar o DependencyGraph."""
from __future__ import annotations

from app.domain.models.dependency import Dependency, DependencyGraph
from app.domain.models.microservice import MicroserviceConfig


class DependencyManager:
    """Constroi o DependencyGraph a partir da configuracao de microsservicos
    envolvidos em uma migracao, e oferece operacoes de conveniencia sobre ele.
    """

    def build_graph_for_service(self, microservice: MicroserviceConfig) -> DependencyGraph:
        """Constroi um grafo de dependencias a partir de um unico microsservico
        e suas dependencias declaradas.

        As dependencias listadas na MigrationRequest sao consideradas servicos
        conhecidos (ja existentes na aplicacao, fora do escopo desta
        migracao), entao sao registradas como servicos do grafo mesmo sem
        dependencias proprias declaradas. Isso permite validar a migracao de
        um unico microsservico sem exigir a topologia completa da aplicacao.

        Em um cenario com multiplos servicos conhecidos pelo MigrationManager,
        este metodo poderia ser estendido para agregar varias configuracoes.
        """
        graph = DependencyGraph()
        graph.add_service(microservice.id, microservice.dependencies)
        for dependency in microservice.dependencies:
            graph.add_service(dependency.service_id, [])
        return graph

    def required_dependencies(self, graph: DependencyGraph, service_id: str) -> list[Dependency]:
        return [dep for dep in graph.get_dependencies(service_id) if dep.required]
