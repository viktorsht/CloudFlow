"""RDS PostgreSQL real sobre Floci."""
from __future__ import annotations

import re
import time

import boto3
import psycopg

from app.domain.contracts.database_provider import DatabaseProvider
from app.domain.models.data import DataTargetConfig
from app.domain.models.database import DatabaseConnection
from app.domain.models.provider import AwsCredentials, ProviderConfig


class AWSRDSDatabaseProvider(DatabaseProvider):
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._endpoint_url = config.endpoint
        credentials = config.credentials
        if not isinstance(credentials, AwsCredentials):
            raise ValueError("Credenciais AWS obrigatorias para provider aws")
        self._client = boto3.client("rds", region_name=config.region, endpoint_url=self._endpoint_url,
            aws_access_key_id=credentials.access_key_id, aws_secret_access_key=credentials.secret_access_key,
            aws_session_token=credentials.session_token)

    def discover(self, config: DataTargetConfig) -> DatabaseConnection:
        username, password = config.username, config.password
        host, port = config.host, config.port
        resource_id = config.resource_id or self._config.runtime_options.get("db_instance_identifier")
        if resource_id:
            response = self._client.describe_db_instances(DBInstanceIdentifier=resource_id)
            item = response["DBInstances"][0]
            endpoint = item.get("Endpoint", {})
            host = self._external_host(endpoint.get("Address", host))
            port = int(endpoint.get("Port", port))
        return DatabaseConnection(host, port, config.database, username, password, resource_id or config.database, "aws")

    def provision(self, config: DataTargetConfig) -> DatabaseConnection:
        username, password = config.username, config.password
        identifier = config.resource_id or self._config.runtime_options.get("db_instance_identifier") or self._db_identifier(config.database)
        try:
            item = self._client.describe_db_instances(DBInstanceIdentifier=identifier)["DBInstances"][0]
        except self._client.exceptions.DBInstanceNotFoundFault:
            item = self._client.create_db_instance(
                DBInstanceIdentifier=identifier, Engine="postgres", DBInstanceClass=self._config.runtime_options.get("db_instance_class", "db.t3.micro"),
                MasterUsername=username, MasterUserPassword=password, AllocatedStorage=int(self._config.runtime_options.get("allocated_storage", "20")),
            )["DBInstance"]
        endpoint = item.get("Endpoint", {})
        deadline = time.monotonic() + float(self._config.runtime_options.get("db_ready_timeout_seconds", "90"))
        while not endpoint.get("Address") and time.monotonic() < deadline:
            time.sleep(2)
            item = self._client.describe_db_instances(DBInstanceIdentifier=identifier)["DBInstances"][0]
            endpoint = item.get("Endpoint", {})
        if not endpoint.get("Address"):
            raise RuntimeError(f"RDS {identifier} nao disponibilizou endpoint")
        connection = DatabaseConnection(self._external_host(endpoint["Address"]), int(endpoint.get("Port", 5432)), config.database, username, password, identifier, "aws")
        self._ensure_database(connection)
        return connection

    def remove(self, connection: DatabaseConnection) -> None:
        self._client.delete_db_instance(DBInstanceIdentifier=connection.resource_id, SkipFinalSnapshot=True, DeleteAutomatedBackups=True)

    @staticmethod
    def _db_identifier(database: str) -> str:
        return re.sub(r"[^a-z0-9-]", "-", f"migration-{database}".lower())[:63]

    def _external_host(self, host: str) -> str:
        return self._config.runtime_options.get("database_host", host).replace("localhost", "host.docker.internal")

    @staticmethod
    def _ensure_database(connection: DatabaseConnection) -> None:
        admin = connection.database if connection.database == "postgres" else "postgres"
        with psycopg.connect(host=connection.host, port=connection.port, dbname=admin, user=connection.username, password=connection.password, autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (connection.database,)).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{connection.database.replace(chr(34), chr(34) * 2)}"')
