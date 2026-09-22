"""Contrato para provedores de redirecionamento de trafego (DNS, LB, gateway...)."""
from abc import ABC, abstractmethod

from app.domain.models.deployment import Deployment


class TrafficProvider(ABC):
    """Abstrai o mecanismo de roteamento usado para direcionar requisicoes
    para um microsservico.

    O dominio nao deve saber se, por baixo, isso e feito via DNS, load
    balancer, API gateway, service discovery ou qualquer outro mecanismo.
    """

    @abstractmethod
    def get_current_route(self, service_id: str) -> str:
        """Retorna o endpoint atualmente configurado para o servico."""

    @abstractmethod
    def redirect(self, service_id: str, target_endpoint: str) -> None:
        """Redireciona o trafego do servico para um novo endpoint."""

    @abstractmethod
    def validate_route(self, service_id: str, expected_endpoint: str) -> bool:
        """Verifica se a rota atual do servico aponta para o endpoint esperado."""

    @abstractmethod
    def restore(self, service_id: str, previous_endpoint: str) -> None:
        """Restaura a rota anterior do servico (usado em rollback)."""

    @abstractmethod
    def enable_maintenance(self, service_id: str) -> None:
        """Instala uma rota de maior prioridade que responde 503."""

    @abstractmethod
    def disable_maintenance(self, service_id: str) -> None:
        """Remove a rota temporaria de manutencao."""

    @abstractmethod
    def redirect_deployment(self, service_id: str, deployment: Deployment) -> None:
        """Redireciona preservando metadados de roteamento do deployment."""

    @abstractmethod
    def validate_public_application(self, service_id: str) -> bool:
        """Executa a chamada publica do microsservico depois do corte."""
