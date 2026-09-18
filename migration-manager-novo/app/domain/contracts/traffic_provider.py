"""Contrato para provedores de redirecionamento de trafego (DNS, LB, gateway...)."""
from abc import ABC, abstractmethod


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
