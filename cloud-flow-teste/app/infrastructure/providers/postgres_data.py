"""Migracao consistente PostgreSQL usando os clientes oficiais do PostgreSQL."""
from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime, timezone

import psycopg

from app.domain.contracts.data_migration_provider import DataMigrationProvider
from app.domain.models.data import DataMigrationResult, DataSourceConfig, DataTargetConfig, ValidationResult
from app.domain.models.database import DatabaseConnection


class PostgreSQLDataMigrationProvider(DataMigrationProvider):
    """Executa dump/restore durante a janela de manutencao.

    A API herdada recebe configuracoes; o fluxo real usa ``migrate_connections``
    e nunca escreve senhas em arquivos de configuracao ou eventos.
    """

    def prepare_source(self, source: DataSourceConfig) -> None:
        return None

    def prepare_target(self, target: DataTargetConfig) -> None:
        return None

    def migrate(self, source: DataSourceConfig, target: DataTargetConfig) -> DataMigrationResult:
        raise RuntimeError("Use migrate_connections com conexoes resolvidas")

    def migrate_connections(self, source: DatabaseConnection, target: DatabaseConnection) -> DataMigrationResult:
        started = datetime.now(timezone.utc)
        fd, dump_path = tempfile.mkstemp(prefix="migration-", suffix=".dump")
        os.close(fd)
        try:
            self._run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", dump_path], source)
            self._run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", dump_path], target)
            records = sum(self._table_counts(target).values())
            return DataMigrationResult(success=True, records_migrated=records, started_at=started.isoformat(), finished_at=datetime.now(timezone.utc).isoformat(), message="Dump/restore PostgreSQL concluido")
        finally:
            try:
                os.unlink(dump_path)
            except FileNotFoundError:
                pass

    def validate(self, source: DataSourceConfig, target: DataTargetConfig) -> ValidationResult:
        raise RuntimeError("Use validate_connections com conexoes resolvidas")

    def validate_connections(self, source: DatabaseConnection, target: DatabaseConnection) -> ValidationResult:
        source_counts, target_counts = self._table_counts(source), self._table_counts(target)
        source_tables, target_tables = set(source_counts), set(target_counts)
        same_schema = source_tables == target_tables
        same_counts = source_counts == target_counts
        return ValidationResult(success=same_schema and same_counts, checks={"schema_tables": same_schema, "row_counts": same_counts, "source_accessible": True, "target_accessible": True}, message="Validacao PostgreSQL por tabela")

    def cleanup(self, source: DataSourceConfig) -> None:
        # Origem e dados sao deliberadamente retidos para rollback explicito.
        return None

    def rollback(self, target: DataTargetConfig) -> None:
        # A remocao do recurso e feita pelo DatabaseProvider no MigrationManager.
        return None

    def _run(self, command: list[str], connection: DatabaseConnection, include_connection: bool = True) -> None:
        args = list(command)
        if include_connection:
            args.extend(["--host", connection.host, "--port", str(connection.port), "--username", connection.username, "--dbname", connection.database])
        env = {**os.environ, "PGPASSWORD": connection.password}
        completed = subprocess.run(args, env=env, text=True, capture_output=True, timeout=180, check=False)
        if completed.returncode:
            raise RuntimeError(f"{' '.join(args[:1])} falhou: {completed.stderr.strip()}")

    @staticmethod
    def _table_counts(connection: DatabaseConnection) -> dict[str, int]:
        query = """
            SELECT quote_ident(schemaname) || '.' || quote_ident(relname)
            FROM pg_stat_user_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY 1
        """
        with psycopg.connect(host=connection.host, port=connection.port, dbname=connection.database, user=connection.username, password=connection.password) as conn:
            tables = [row[0] for row in conn.execute(query).fetchall()]
            return {table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables}
