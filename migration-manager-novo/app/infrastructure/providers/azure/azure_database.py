"""Azure Database for PostgreSQL real sobre Floci-AZ."""
from __future__ import annotations

import os
import re

import httpx
import psycopg

from app.domain.contracts.database_provider import DatabaseProvider
from app.domain.models.data import DataTargetConfig
from app.domain.models.database import DatabaseConnection
from app.domain.models.provider import ProviderConfig
from app.infrastructure.secrets.environment import EnvironmentSecretResolver

API_VERSION = "2025-08-01"


class AzurePostgreSQLDatabaseProvider(DatabaseProvider):
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._endpoint = (config.endpoint_override or os.getenv("AZURE_ENDPOINT_URL", "http://floci-az:4577")).rstrip("/")
        options = config.runtime_options
        self._subscription = options.get("subscription_id", "00000000-0000-0000-0000-000000000000")
        self._resource_group = options.get("resource_group", "floci-migrations")

    def discover(self, config: DataTargetConfig) -> DatabaseConnection:
        username, password = EnvironmentSecretResolver.credentials(config.credentials_ref)
        resource_id = config.resource_id or self._config.runtime_options.get("postgres_server_name")
        if not resource_id:
            return DatabaseConnection(config.host, config.port, config.database, username, password, config.database, "azure")
        return self._connection(resource_id, config.database, username, password)

    def provision(self, config: DataTargetConfig) -> DatabaseConnection:
        username, password = EnvironmentSecretResolver.credentials(config.credentials_ref)
        server = config.resource_id or self._config.runtime_options.get("postgres_server_name") or self._safe_name(f"migration-{config.database}")
        self._request("PUT", self._server_path(server), json={
            "location": self._config.region,
            "sku": {"name": self._config.runtime_options.get("postgres_sku", "Standard_B1ms"), "tier": "Burstable"},
            "properties": {"version": "16", "administratorLogin": username, "administratorLoginPassword": password,
                           "storage": {"storageSizeGB": int(self._config.runtime_options.get("postgres_storage_gb", "32"))}},
        })
        connection = self._connection(server, config.database, username, password)
        self._ensure_database(connection)
        return connection

    def remove(self, connection: DatabaseConnection) -> None:
        self._request("DELETE", self._server_path(connection.resource_id))

    def _connection(self, server: str, database: str, username: str, password: str) -> DatabaseConnection:
        response = self._request("GET", f"/devstoreaccount1-postgres/flexibleServers/{server}/connect").json()
        host = response.get("host") or response.get("hostname")
        port = int(response.get("port", 5432))
        if not host:
            raise RuntimeError(f"Floci-AZ nao retornou conexao para PostgreSQL {server}")
        host = self._config.runtime_options.get("database_host", host).replace("localhost", "host.docker.internal")
        return DatabaseConnection(host, port, database, username, password, server, "azure")

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = httpx.request(method, f"{self._endpoint}{path}", params={"api-version": API_VERSION}, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"ARM PostgreSQL {method} {path} falhou ({response.status_code}): {response.text}")
        return response

    def _server_path(self, server: str) -> str:
        return f"/subscriptions/{self._subscription}/resourceGroups/{self._resource_group}/providers/Microsoft.DBforPostgreSQL/flexibleServers/{server}"

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^a-z0-9-]", "-", value.lower())[:63].strip("-")

    @staticmethod
    def _ensure_database(connection: DatabaseConnection) -> None:
        with psycopg.connect(host=connection.host, port=connection.port, dbname="postgres", user=connection.username, password=connection.password, autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (connection.database,)).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{connection.database.replace(chr(34), chr(34) * 2)}"')
