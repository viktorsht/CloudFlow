"""Migracao PostgreSQL com copia base + replicacao logica nativa (pgoutput)."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg
from psycopg import sql
from psycopg.pq import ExecStatus

from app.domain.contracts.replication_data_provider import ReplicationDataMigrationProvider
from app.domain.models.data import DataMigrationResult, ReplicationLagResult
from app.domain.models.database import DatabaseConnection
from app.domain.models.migration import ReplicationOptions
from app.infrastructure.providers.postgres_data import PostgreSQLDataMigrationProvider

_USER_TABLES = """
    SELECT schemaname, tablename FROM pg_tables
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY 1, 2
"""

# Sem PK (ou REPLICA IDENTITY explicita), UPDATE/DELETE na origem passam a
# falhar assim que a tabela entra na publication - quebraria a aplicacao no ar.
_TABLES_WITHOUT_IDENTITY = """
    SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname NOT LIKE 'pg\\_%'
      AND (c.relreplident = 'n'
           OR (c.relreplident = 'd' AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary)))
    ORDER BY 1
"""

_REPLICATION_PREREQUISITES = """
    SELECT current_setting('wal_level'),
           current_setting('max_replication_slots')::int,
           r.rolsuper OR r.rolreplication OR EXISTS (
               SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
               WHERE m.member = r.oid AND g.rolname = 'rds_replication')
    FROM pg_roles r WHERE r.rolname = current_user
"""


@dataclass(frozen=True)
class ReplicationNames:
    publication: str
    slot: str
    subscription: str

    @classmethod
    def from_migration(cls, replication_name: str) -> "ReplicationNames":
        # Slots aceitam apenas [a-z0-9_]; o mesmo prefixo nomeia os tres artefatos.
        base = "mm_" + re.sub(r"[^a-z0-9_]", "_", replication_name.lower())[:40]
        return cls(publication=f"{base}_pub", slot=f"{base}_slot", subscription=f"{base}_sub")


class PostgreSQLReplicationDataMigrationProvider(
    PostgreSQLDataMigrationProvider, ReplicationDataMigrationProvider
):
    """Copia base consistente seguida de replicacao logica origem -> destino.

    O slot e criado com ``EXPORT_SNAPSHOT`` e o ``pg_dump`` usa esse snapshot:
    a copia base termina exatamente onde a replicacao comeca, sem perder nem
    duplicar escritas feitas pela aplicacao enquanto o dump roda.

    Pre-requisitos: origem com ``wal_level=logical``, usuario com REPLICATION
    e todas as tabelas com PK; destino com permissao para CREATE SUBSCRIPTION
    e acesso de rede a origem. DDL durante a migracao nao e replicado.
    """

    def __init__(
        self,
        connect: Callable[..., Any] = psycopg.connect,
        runner: Callable[..., Any] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect = connect
        self._runner = runner
        self._sleep = sleep
        self._clock = clock

    # -- pre-requisitos e copia base -----------------------------------

    def prepare_source_connection(self, source: DatabaseConnection) -> None:
        with self._connect_to(source) as conn:
            wal_level, max_slots, can_replicate = conn.execute(_REPLICATION_PREREQUISITES).fetchone()
            without_identity = [row[0] for row in conn.execute(_TABLES_WITHOUT_IDENTITY).fetchall()]
        problems = []
        if wal_level != "logical":
            problems.append(f"wal_level={wal_level} (requer logical)")
        if int(max_slots) < 1:
            problems.append("max_replication_slots=0")
        if not can_replicate:
            problems.append(f"usuario '{source.username}' sem privilegio REPLICATION")
        if without_identity:
            problems.append("tabelas sem PRIMARY KEY/REPLICA IDENTITY: " + ", ".join(without_identity))
        if problems:
            raise RuntimeError("Origem nao suporta replicacao logica: " + "; ".join(problems))

    def base_copy(
        self, source: DatabaseConnection, target: DatabaseConnection,
        replication_name: str, options: ReplicationOptions,
    ) -> DataMigrationResult:
        names = ReplicationNames.from_migration(replication_name)
        started = datetime.now(timezone.utc)
        # Restos de uma tentativa anterior com o mesmo migration_id.
        self._drop_source_artifacts(source, names)
        tables = self._fetch(source, _USER_TABLES)
        self._execute(source, self._create_publication(names.publication, tables))

        fd, dump_path = tempfile.mkstemp(prefix="migration-", suffix=".dump")
        os.close(fd)
        try:
            publisher = self._publisher_endpoint(source, options)
            with self._connect_to(publisher, replication="database") as replication_conn:
                snapshot = self._create_slot_with_snapshot(replication_conn, names.slot)
                # O snapshot exportado so vale enquanto esta conexao segue aberta e ociosa.
                self._run(
                    ["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                     "--no-publications", "--no-subscriptions", f"--snapshot={snapshot}",
                     "--file", dump_path],
                    source, timeout=options.base_copy_timeout_seconds,
                )
            self._run(
                ["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", dump_path],
                target, timeout=options.base_copy_timeout_seconds,
            )
        finally:
            try:
                os.unlink(dump_path)
            except FileNotFoundError:
                pass
        records = sum(self._table_counts(target).values())
        return DataMigrationResult(
            success=True, records_migrated=records, started_at=started.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            message=f"Copia base concluida a partir do slot {names.slot}",
        )

    # -- replicacao continua -------------------------------------------

    def start_replication(
        self, source: DatabaseConnection, target: DatabaseConnection,
        replication_name: str, options: ReplicationOptions,
    ) -> None:
        names = ReplicationNames.from_migration(replication_name)
        # copy_data=false: a copia base ja trouxe tudo ate o ponto do slot.
        self._execute(target, sql.SQL(
            "CREATE SUBSCRIPTION {sub} CONNECTION {conninfo} PUBLICATION {pub} "
            "WITH (create_slot = false, slot_name = {slot}, copy_data = false, enabled = true)"
        ).format(
            sub=sql.Identifier(names.subscription),
            conninfo=sql.Literal(self._publisher_conninfo(source, options)),
            pub=sql.Identifier(names.publication),
            slot=sql.Literal(names.slot),
        ))

    def replication_lag(
        self, source: DatabaseConnection, replication_name: str, options: ReplicationOptions,
    ) -> ReplicationLagResult:
        names = ReplicationNames.from_migration(replication_name)
        rows = self._fetch(
            source,
            "SELECT active, pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint "
            "FROM pg_replication_slots WHERE slot_name = %s",
            (names.slot,),
        )
        if not rows:
            return ReplicationLagResult()
        active, lag = rows[0]
        lag_bytes = None if lag is None else max(int(lag), 0)
        synced = bool(active) and lag_bytes is not None and lag_bytes <= options.lag_threshold_bytes
        return ReplicationLagResult(lag_bytes=lag_bytes, slot_active=bool(active), synced=synced)

    def wait_until_synced(
        self, source: DatabaseConnection, replication_name: str, options: ReplicationOptions,
    ) -> ReplicationLagResult:
        deadline = self._clock() + options.sync_timeout_seconds
        while True:
            lag = self.replication_lag(source, replication_name, options)
            if lag.synced:
                return lag
            if self._clock() >= deadline:
                raise TimeoutError(
                    f"Replicacao nao sincronizou em {options.sync_timeout_seconds:g}s "
                    f"(lag={lag.lag_bytes} bytes, slot ativo={lag.slot_active})"
                )
            self._sleep(options.poll_interval_seconds)

    def drain_final_delta(
        self, source: DatabaseConnection, target: DatabaseConnection,
        replication_name: str, options: ReplicationOptions,
    ) -> DataMigrationResult:
        names = ReplicationNames.from_migration(replication_name)
        started = datetime.now(timezone.utc)
        # Com a manutencao ligada, tudo que foi confirmado na origem esta antes desta LSN.
        final_lsn = self._fetch(source, "SELECT pg_current_wal_flush_lsn()::text")[0][0]
        deadline = self._clock() + options.drain_timeout_seconds
        while True:
            rows = self._fetch(
                source,
                "SELECT confirmed_flush_lsn >= %s::pg_lsn FROM pg_replication_slots WHERE slot_name = %s",
                (final_lsn, names.slot),
            )
            if not rows:
                raise RuntimeError(f"Slot {names.slot} desapareceu durante a drenagem")
            if rows[0][0]:
                break
            if self._clock() >= deadline:
                raise TimeoutError(
                    f"Destino nao confirmou a LSN {final_lsn} em {options.drain_timeout_seconds:g}s"
                )
            self._sleep(options.poll_interval_seconds)
        sequences = self._sync_sequences(source, target)
        return DataMigrationResult(
            success=True, started_at=started.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            message=f"Delta final aplicado ate {final_lsn}; {sequences} sequence(s) sincronizada(s)",
        )

    def stop_replication(
        self, source: DatabaseConnection, target: DatabaseConnection | None, replication_name: str,
    ) -> None:
        names = ReplicationNames.from_migration(replication_name)
        errors: list[str] = []
        if target is not None:
            try:
                self._drop_subscription(target, names)
            except Exception as exc:  # noqa: BLE001 - a origem precisa ser limpa mesmo assim
                errors.append(f"destino: {exc}")
        try:
            # Slot esquecido retem WAL na origem indefinidamente.
            self._drop_source_artifacts(source, names)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"origem: {exc}")
        if errors:
            raise RuntimeError("Falha ao encerrar replicacao: " + "; ".join(errors))

    # -- internos ---------------------------------------------------------

    def _drop_subscription(self, target: DatabaseConnection, names: ReplicationNames) -> None:
        exists = self._fetch(
            target,
            "SELECT 1 FROM pg_subscription WHERE subname = %s "
            "AND subdbid = (SELECT oid FROM pg_database WHERE datname = current_database())",
            (names.subscription,),
        )
        if not exists:
            return
        sub = sql.Identifier(names.subscription)
        # Desassociar o slot deixa o DROP independente da conectividade com a origem;
        # o slot e removido pelo lado da origem.
        self._execute(
            target,
            sql.SQL("ALTER SUBSCRIPTION {} DISABLE").format(sub),
            sql.SQL("ALTER SUBSCRIPTION {} SET (slot_name = NONE)").format(sub),
            sql.SQL("DROP SUBSCRIPTION {}").format(sub),
        )

    def _drop_source_artifacts(self, source: DatabaseConnection, names: ReplicationNames) -> None:
        with self._connect_to(source) as conn:
            for _ in range(20):
                row = conn.execute(
                    "SELECT active_pid FROM pg_replication_slots WHERE slot_name = %s", (names.slot,)
                ).fetchone()
                if row is None:
                    break
                if row[0] is not None:
                    # O walsender da subscription desabilitada pode demorar a sair.
                    conn.execute("SELECT pg_terminate_backend(%s)", (row[0],))
                    self._sleep(0.5)
                    continue
                try:
                    conn.execute("SELECT pg_drop_replication_slot(%s)", (names.slot,))
                    break
                except psycopg.errors.ObjectInUse:
                    self._sleep(0.5)
            else:
                raise RuntimeError(f"Slot {names.slot} continua em uso")
            conn.execute(sql.SQL("DROP PUBLICATION IF EXISTS {}").format(sql.Identifier(names.publication)))

    @staticmethod
    def _create_publication(publication: str, tables: list[tuple]) -> sql.Composable:
        statement = sql.SQL("CREATE PUBLICATION {}").format(sql.Identifier(publication))
        if not tables:
            return statement
        # FOR TABLE (e nao FOR ALL TABLES): nao exige superusuario, que RDS/Azure nao concedem.
        return sql.SQL("{} FOR TABLE {}").format(
            statement, sql.SQL(", ").join(sql.Identifier(schema, table) for schema, table in tables)
        )

    @staticmethod
    def _create_slot_with_snapshot(replication_conn: Any, slot: str) -> str:
        # Comando do protocolo de replicacao: so aceito via simple query.
        result = replication_conn.pgconn.exec_(
            f"CREATE_REPLICATION_SLOT {slot} LOGICAL pgoutput EXPORT_SNAPSHOT".encode()
        )
        if result.status != ExecStatus.TUPLES_OK:
            detail = (result.error_message or b"").decode(errors="replace").strip()
            raise RuntimeError(f"Falha ao criar slot de replicacao {slot}: {detail}")
        return result.get_value(0, 2).decode()

    def _sync_sequences(self, source: DatabaseConnection, target: DatabaseConnection) -> int:
        # Replicacao logica nao propaga sequences; sem isto o destino repetiria IDs.
        sequences = self._fetch(
            source,
            "SELECT quote_ident(schemaname) || '.' || quote_ident(sequencename), last_value "
            "FROM pg_sequences WHERE last_value IS NOT NULL "
            "AND schemaname NOT IN ('pg_catalog', 'information_schema')",
        )
        if sequences:
            with self._connect_to(target) as conn:
                for name, value in sequences:
                    conn.execute("SELECT setval(%s::regclass, %s, true)", (name, value))
        return len(sequences)

    @staticmethod
    def _publisher_endpoint(source: DatabaseConnection, options: ReplicationOptions) -> DatabaseConnection:
        # Conexoes de replicacao nao atravessam proxies que so falam SQL comum.
        return replace(source, host=options.publisher_host or source.host, port=options.publisher_port or source.port)

    @classmethod
    def _publisher_conninfo(cls, source: DatabaseConnection, options: ReplicationOptions) -> str:
        def quote(value: object) -> str:
            return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

        publisher = cls._publisher_endpoint(source, options)
        params = {
            "host": publisher.host,
            "port": publisher.port,
            "dbname": source.database,
            "user": source.username,
            "password": source.password,
        }
        return " ".join(f"{key}={quote(value)}" for key, value in params.items())

    def _connect_to(self, connection: DatabaseConnection, **extra: Any) -> Any:
        return self._connect(
            host=connection.host, port=connection.port, dbname=connection.database,
            user=connection.username, password=connection.password, autocommit=True, **extra,
        )

    def _fetch(self, connection: DatabaseConnection, query: Any, params: tuple | None = None) -> list[tuple]:
        with self._connect_to(connection) as conn:
            return list(conn.execute(query, params).fetchall())

    def _execute(self, connection: DatabaseConnection, *statements: Any) -> None:
        # autocommit: CREATE/DROP SUBSCRIPTION nao rodam dentro de transacao.
        with self._connect_to(connection) as conn:
            for statement in statements:
                conn.execute(statement)

    def _run(
        self, command: list[str], connection: DatabaseConnection,
        include_connection: bool = True, timeout: float = 180,
    ) -> None:
        args = list(command)
        if include_connection:
            args.extend(["--host", connection.host, "--port", str(connection.port),
                         "--username", connection.username, "--dbname", connection.database])
        env = {**os.environ, "PGPASSWORD": connection.password}
        completed = self._runner(args, env=env, text=True, capture_output=True, timeout=timeout, check=False)
        if completed.returncode:
            raise RuntimeError(f"{args[0]} falhou: {completed.stderr.strip()}")

    def _table_counts(self, connection: DatabaseConnection) -> dict[str, int]:
        query = """
            SELECT quote_ident(schemaname) || '.' || quote_ident(relname)
            FROM pg_stat_user_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY 1
        """
        with self._connect_to(connection) as conn:
            tables = [row[0] for row in conn.execute(query).fetchall()]
            return {table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables}
