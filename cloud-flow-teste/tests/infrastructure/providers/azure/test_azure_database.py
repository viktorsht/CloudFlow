"""Testes do provider Azure de PostgreSQL sobre Floci-AZ.

Cobrem o cenario que gerou o erro real 'invalid port number: 0': o PUT que
cria o flexible server dispara a criacao do container em segundo plano, e
/connect responde com um placeholder (porta 0) ate ele ficar pronto.
"""
from __future__ import annotations

import httpx
import pytest

from app.domain.models.data import DataTargetConfig
from app.domain.models.provider import AzureCredentials, ProviderConfig
from app.infrastructure.providers.azure.azure_database import AzurePostgreSQLDatabaseProvider


class FakeTransport:
    """Substitui httpx.request: registra as chamadas e responde por rota."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.connect_responses: list[dict] = [{"host": "floci-az-pg-ms2", "port": 55432}]
        self.ensure_database_calls = 0

    def __call__(self, method: str, url: str, **kwargs) -> httpx.Response:
        self.calls.append((method, url))
        if method == "PUT" and "flexibleServers" in url:
            return httpx.Response(200, json={"status": "creating"})
        if method == "GET" and url.endswith("/connect"):
            index = min(len(self.connect_responses) - 1, self._connect_calls())
            return httpx.Response(200, json=self.connect_responses[index])
        if method == "DELETE":
            return httpx.Response(200, json={})
        raise AssertionError(f"chamada inesperada: {method} {url}")

    def _connect_calls(self) -> int:
        return sum(1 for m, u in self.calls if m == "GET" and u.endswith("/connect")) - 1


def make_provider(transport: FakeTransport, monkeypatch: pytest.MonkeyPatch, **runtime_options) -> AzurePostgreSQLDatabaseProvider:
    monkeypatch.setattr(httpx, "request", transport)
    config = ProviderConfig(
        provider="azure", region="eastus", environment="target",
        endpoint="http://floci-az:4577",
        credentials=AzureCredentials(tenant_id="t", client_id="c", client_secret="s", subscription_id="sub"),
        runtime_options={"resource_group": "floci-migrations", "postgres_ready_timeout_seconds": "0.2", **runtime_options},
    )
    return AzurePostgreSQLDatabaseProvider(config)


def target_config(**overrides) -> DataTargetConfig:
    base = dict(host="ignored", port=5432, database="ms2_db", resource_id="ms2-azure-db", username="u", password="p")
    base.update(overrides)
    return DataTargetConfig(**base)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Os testes exercitam o loop de espera de verdade, mas sem gastar tempo real."""
    monkeypatch.setattr("app.infrastructure.providers.azure.azure_database.time.sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    """provision() chama psycopg no final; troca por um fake para nao precisar de um Postgres de verdade."""
    calls: list = []

    def fake_ensure_database(connection):
        calls.append(connection)

    monkeypatch.setattr(AzurePostgreSQLDatabaseProvider, "_ensure_database", staticmethod(fake_ensure_database))
    return calls


def test_provision_waits_past_the_not_ready_placeholder(monkeypatch, no_real_database):
    transport = FakeTransport()
    transport.connect_responses = [
        {"host": "localhost", "port": 0},   # placeholder: container ainda subindo
        {"host": "localhost", "port": 0},
        {"host": "floci-az-pg-ms2-azure-db", "port": 55432},  # pronto
    ]
    provider = make_provider(transport, monkeypatch)

    connection = provider.provision(target_config())

    assert connection.host == "floci-az-pg-ms2-azure-db"
    assert connection.port == 55432
    assert len(no_real_database) == 1
    connect_calls = [c for c in transport.calls if c == ("GET", "http://floci-az:4577/devstoreaccount1-postgres/flexibleServers/ms2-azure-db/connect")]
    assert len(connect_calls) == 3


def test_provision_gives_up_after_timeout_if_always_not_ready(monkeypatch, no_real_database):
    transport = FakeTransport()
    transport.connect_responses = [{"host": "localhost", "port": 0}]  # nunca fica pronto
    provider = make_provider(transport, monkeypatch)

    with pytest.raises(RuntimeError, match="nao ficou pronto"):
        provider.provision(target_config())

    assert not no_real_database  # nunca chegou a tentar criar o database


def test_provision_ready_on_first_try_does_not_wait(monkeypatch, no_real_database):
    transport = FakeTransport()
    transport.connect_responses = [{"host": "floci-az-pg-ms2-azure-db", "port": 55432}]
    provider = make_provider(transport, monkeypatch)

    connection = provider.provision(target_config())

    assert connection.port == 55432
    connect_calls = [c for c in transport.calls if c[0] == "GET" and c[1].endswith("/connect")]
    assert len(connect_calls) == 1


def test_localhost_host_is_translated_for_host_docker_internal(monkeypatch, no_real_database):
    """Quando o Floci-AZ ja devolve 'localhost' com uma porta valida (nao um placeholder), o host ainda precisa virar host.docker.internal."""
    transport = FakeTransport()
    transport.connect_responses = [{"host": "localhost", "port": 55432}]
    provider = make_provider(transport, monkeypatch)

    connection = provider.provision(target_config())

    assert connection.host == "host.docker.internal"
    assert connection.port == 55432
