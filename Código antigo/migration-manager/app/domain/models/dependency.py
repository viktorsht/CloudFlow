"""Modelos e estruturas para representar dependencias entre microsservicos."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Dependency(BaseModel):
    """Uma dependencia de um microsservico em relacao a outro servico."""

    service_id: str = Field(..., min_length=1)
    protocol: str = Field(default="http")
    port: int = Field(..., gt=0, lt=65536)
    required: bool = Field(default=True)


class DependencyGraph:
    """Grafo de dependencias entre microsservicos de uma aplicacao.

    O grafo e construido a partir de uma lista de (service_id, [Dependency]),
    representando arestas dirigidas de "service_id depende de dependency.service_id".

    Convencao adotada (compativel com o exemplo do prompt, onde
    MS-A -> MS-B -> MS-C representa o fluxo de chamadas):
      - get_dependencies(x): servicos que x invoca a jusante (fluxo abaixo, "downstream").
      - get_dependents(x): servicos que invocam x a montante ("upstream"/callers).
    """

    def __init__(self, edges: dict[str, list[Dependency]] | None = None) -> None:
        # service_id -> lista de Dependency (quem esse servico chama)
        self._downstream: dict[str, list[Dependency]] = {}
        # service_id -> lista de service_ids que o chamam
        self._upstream: dict[str, list[str]] = {}
        # servicos explicitamente declarados via add_service (diferente de
        # servicos apenas referenciados como alvo de uma dependencia).
        self._known_services: set[str] = set()
        if edges:
            for service_id, dependencies in edges.items():
                self.add_service(service_id, dependencies)

    def add_service(self, service_id: str, dependencies: list[Dependency]) -> None:
        self._known_services.add(service_id)
        self._downstream.setdefault(service_id, [])
        self._downstream[service_id].extend(dependencies)
        for dep in dependencies:
            self._upstream.setdefault(dep.service_id, [])
            if service_id not in self._upstream[dep.service_id]:
                self._upstream[dep.service_id].append(service_id)
            self._downstream.setdefault(dep.service_id, [])

    def get_dependencies(self, service_id: str) -> list[Dependency]:
        """Retorna os servicos dos quais `service_id` depende (downstream)."""
        return list(self._downstream.get(service_id, []))

    def get_dependents(self, service_id: str) -> list[str]:
        """Retorna os servicos que dependem de `service_id` (upstream/callers)."""
        return list(self._upstream.get(service_id, []))

    def validate_dependencies(self, service_id: str) -> bool:
        """Verifica se todas as dependencias obrigatorias de um servico foram
        explicitamente declaradas como servicos conhecidos do grafo (via
        `add_service`), e nao apenas referenciadas como alvo de uma aresta.
        """
        for dep in self._downstream.get(service_id, []):
            if dep.required and dep.service_id not in self._known_services:
                return False
        return True

    def known_services(self) -> list[str]:
        return list(self._known_services)
