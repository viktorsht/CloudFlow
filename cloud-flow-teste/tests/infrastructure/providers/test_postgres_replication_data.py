"""PostgreSQLReplicationDataMigrationProvider com conexoes e processos falsos."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import ExecStatus

from app.domain.models.database import DatabaseConnection
from app.domain.models.migration import ReplicationOptions
from app.infrastructure.providers.postgres_replication_data import (
    PostgreSQLReplicationDataMigrationProvider,
    ReplicationNames,
)

SOURCE = DatabaseConnection("src", 5432, "ms2_db", "ms2_user", "p'w\\d", "src-db", "aws")
TARGET = DatabaseConnection("dst", 5433, "ms2_db", "admin", "secret", "dst-db", "azure")
NAMES = ReplicationNames.from_migration("mig-42")


class FakeServer:
    """Um servidor PostgreSQL: responde por trecho do SQL e registra o que rodou."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple | None]] = []
        self.responses: list[tuple[str, object]] = []
        self.replication_commands: list[str] = []
        self.open_replication_connections = 0

    def on(self, fragment: str, response: object) -> "FakeServer":
        self.responses.append((fragment, response))
        return self

    def respond(self, text: str, params: tuple | None) -> list[tuple]:
        self.executed.append((text, params))
        for fragment, response in self.responses:
            if fragment in text:
                value = response(params) if callable(response) else response
                if isinstance(value, Exception):
                    raise value
                return value
        return []

    def sql(self, fragment: str) -> list[tuple[str, tuple | None]]:
        return [(text, params) for text, params in self.executed if fragment in text]


class FakeConnection:
    def __init__(self, server: FakeServer, replication: bool) -> None:
        self.server, self.replication = server, replication
        self.pgconn = SimpleNamespace(exec_=self._exec_replication)

    def __enter__(self):
        if self.replication:
            self.server.open_replication_connections += 1
        return self

    def __exit__(self, *exc):
        if self.replication:
            self.server.open_replication_connections -= 1
        return False

    def execute(self, query, params=None):
        text = query.as_string(None) if isinstance(query, sql.Composable) else query
        rows = self.server.respond(" ".join(text.split()), params)
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)

    def _exec_replication(self, command: bytes):
        self.server.replication_commands.append(command.decode())
        return SimpleNamespace(
            status=ExecStatus.TUPLES_OK, error_message=b"",
            get_value=lambda row, col: [NAMES.slot.encode(), b"0/16B3748", b"00000003-00000002-1", b"pgoutput"][col],
        )


class Harness:
    def __init__(self) -> None:
        source = FakeServer()
        # "src-direct" e o mesmo servidor, alcancado sem o proxy do Floci.
        self.servers = {"src": source, "src-direct": source, "dst": FakeServer()}
        self.connections: list[dict] = []
        self.commands: list[dict] = []
        self.now = 0.0
        self.provider = PostgreSQLReplicationDataMigrationProvider(
            connect=self.connect, runner=self.run, sleep=self.sleep, clock=lambda: self.now,
        )

    @property
    def source(self) -> FakeServer:
        return self.servers["src"]

    @property
    def target(self) -> FakeServer:
        return self.servers["dst"]

    def connect(self, **kwargs):
        self.connections.append(kwargs)
        return FakeConnection(self.servers[kwargs["host"]], kwargs.get("replication") == "database")

    def run(self, args, env, **kwargs):
        self.commands.append({
            "args": args, "password": env["PGPASSWORD"], "timeout": kwargs["timeout"],
            "replication_open": self.source.open_replication_connections,
        })
        return SimpleNamespace(returncode=0, stderr="")

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def h() -> Harness:
    return Harness()


def healthy_source(server: FakeServer) -> FakeServer:
    return server.on("current_setting('wal_level')", [("logical", 10, True)]).on("relreplident", [])


def test_prerequisites_pass_on_logical_source_with_replication_privilege(h):
    healthy_source(h.source)

    h.provider.prepare_source_connection(SOURCE)

    assert h.connections[0]["autocommit"] is True


def test_prerequisites_report_every_problem_before_touching_anything(h):
    h.source.on("current_setting('wal_level')", [("replica", 10, False)]).on("relreplident", [("public.audit",)])

    with pytest.raises(RuntimeError) as exc:
        h.provider.prepare_source_connection(SOURCE)

    message = str(exc.value)
    assert "wal_level=replica" in message
    assert "sem privilegio REPLICATION" in message
    assert "public.audit" in message
    assert not h.source.sql("CREATE")


def test_base_copy_dumps_from_the_snapshot_exported_by_the_slot(h):
    h.source.on("FROM pg_tables", [("public", "request_log")])
    h.target.on("FROM pg_stat_user_tables", [("public.request_log",)]).on("count(*)", [(7,)])

    result = h.provider.base_copy(SOURCE, TARGET, "mig-42", ReplicationOptions(base_copy_timeout_seconds=900))

    assert result.success and result.records_migrated == 7
    assert h.source.sql(f'CREATE PUBLICATION "{NAMES.publication}" FOR TABLE "public"."request_log"')
    assert h.source.replication_commands == [f"CREATE_REPLICATION_SLOT {NAMES.slot} LOGICAL pgoutput EXPORT_SNAPSHOT"]
    dump, restore = h.commands
    assert dump["args"][0] == "pg_dump"
    assert "--snapshot=00000003-00000002-1" in dump["args"]
    assert {"--no-publications", "--no-subscriptions"} <= set(dump["args"])
    # O snapshot so vale enquanto a conexao de replicacao continua aberta.
    assert dump["replication_open"] == 1
    assert dump["timeout"] == 900
    assert restore["args"][0] == "pg_restore"
    assert restore["args"][restore["args"].index("--host") + 1] == "dst"
    assert restore["password"] == "secret"


def test_start_replication_subscribes_without_copying_data_again(h):
    options = ReplicationOptions(publisher_host="host.docker.internal", publisher_port=15432)

    h.provider.start_replication(SOURCE, TARGET, "mig-42", options)

    [(statement, _)] = h.target.sql("CREATE SUBSCRIPTION")
    assert f'"{NAMES.subscription}"' in statement
    assert f'PUBLICATION "{NAMES.publication}"' in statement
    assert f"slot_name = '{NAMES.slot}'" in statement
    assert "copy_data = false" in statement and "create_slot = false" in statement
    conninfo = PostgreSQLReplicationDataMigrationProvider._publisher_conninfo(SOURCE, options)
    assert sql.Literal(conninfo).as_string(None) in statement
    # A senha com aspas e barra sobrevive ao parser do libpq.
    assert conninfo_to_dict(conninfo) == {
        "host": "host.docker.internal", "port": "15432", "dbname": "ms2_db",
        "user": "ms2_user", "password": "p'w\\d",
    }
    assert not h.source.executed


def test_wait_until_synced_polls_until_lag_is_below_threshold(h):
    lags = iter([[(True, 5_000_000)], [(True, 2_000)]])
    h.source.on("pg_wal_lsn_diff", lambda params: next(lags))

    lag = h.provider.wait_until_synced(SOURCE, "mig-42", ReplicationOptions(lag_threshold_bytes=4096, poll_interval_seconds=2))

    assert lag.synced and lag.lag_bytes == 2_000
    assert h.now == 2


def test_wait_until_synced_times_out_when_lag_does_not_converge(h):
    h.source.on("pg_wal_lsn_diff", [(True, 5_000_000)])

    with pytest.raises(TimeoutError, match="lag=5000000"):
        h.provider.wait_until_synced(SOURCE, "mig-42", ReplicationOptions(sync_timeout_seconds=10, poll_interval_seconds=3))

    assert h.now >= 10


def test_inactive_slot_is_never_considered_synced(h):
    h.source.on("pg_wal_lsn_diff", [(False, 0)])

    assert not h.provider.replication_lag(SOURCE, "mig-42", ReplicationOptions()).synced


def test_drain_waits_for_final_lsn_and_syncs_sequences(h):
    confirmations = iter([[(False,)], [(True,)]])
    h.source.on("pg_current_wal_flush_lsn", [("0/3000060",)])
    h.source.on("confirmed_flush_lsn >=", lambda params: next(confirmations))
    h.source.on("FROM pg_sequences", [("public.request_log_id_seq", 1234)])

    result = h.provider.drain_final_delta(SOURCE, TARGET, "mig-42", ReplicationOptions(poll_interval_seconds=0.5))

    assert result.success
    assert h.source.sql("confirmed_flush_lsn >=")[0][1] == ("0/3000060", NAMES.slot)
    assert h.target.sql("setval") == [("SELECT setval(%s::regclass, %s, true)", ("public.request_log_id_seq", 1234))]


def test_drain_times_out_if_target_never_confirms(h):
    h.source.on("pg_current_wal_flush_lsn", [("0/3000060",)]).on("confirmed_flush_lsn >=", [(False,)])

    with pytest.raises(TimeoutError):
        h.provider.drain_final_delta(SOURCE, TARGET, "mig-42", ReplicationOptions(drain_timeout_seconds=1))

    assert not h.target.sql("setval")


def test_stop_replication_drops_subscription_then_slot_and_publication(h):
    h.target.on("FROM pg_subscription", [(1,)])
    slot_states = iter([[(4242,)], [(None,)], []])
    h.source.on("SELECT active_pid", lambda params: next(slot_states))

    h.provider.stop_replication(SOURCE, TARGET, "mig-42")

    assert [text for text, _ in h.target.executed[1:]] == [
        f'ALTER SUBSCRIPTION "{NAMES.subscription}" DISABLE',
        f'ALTER SUBSCRIPTION "{NAMES.subscription}" SET (slot_name = NONE)',
        f'DROP SUBSCRIPTION "{NAMES.subscription}"',
    ]
    assert h.source.sql("pg_terminate_backend")[0][1] == (4242,)
    assert h.source.sql("pg_drop_replication_slot")[0][1] == (NAMES.slot,)
    assert h.source.sql(f'DROP PUBLICATION IF EXISTS "{NAMES.publication}"')


def test_stop_replication_still_cleans_source_when_target_fails(h):
    h.target.on("FROM pg_subscription", RuntimeError("destino inacessivel"))
    h.source.on("SELECT active_pid", [(None,)])

    with pytest.raises(RuntimeError, match="destino inacessivel"):
        h.provider.stop_replication(SOURCE, TARGET, "mig-42")

    assert h.source.sql("pg_drop_replication_slot")
    assert h.source.sql("DROP PUBLICATION")


def test_replication_names_are_valid_slot_identifiers():
    names = ReplicationNames.from_migration("Migration-MS2/AWS→Azure 001" * 3)

    for name in (names.publication, names.slot, names.subscription):
        assert len(name) <= 63
        assert all(ch.isascii() and (ch.islower() or ch.isdigit() or ch == "_") for ch in name)


def test_factory_selects_provider_by_migration_mode():
    from app.domain.enums.provider_type import DataEngineType
    from app.domain.models.migration import MigrationMode
    from app.infrastructure.providers.factory import ProviderFactory
    from app.infrastructure.providers.postgres_data import PostgreSQLDataMigrationProvider

    factory = ProviderFactory()

    replication = factory.create_data_provider(DataEngineType.POSTGRESQL, MigrationMode.PRE_COPY_REPLICATION)
    classic = factory.create_data_provider(DataEngineType.POSTGRESQL, MigrationMode.STOP_AND_MIGRATE)

    assert isinstance(replication, PostgreSQLReplicationDataMigrationProvider)
    assert type(classic) is PostgreSQLDataMigrationProvider


def test_replication_connections_bypass_the_sql_proxy_when_publisher_is_set(h):
    options = ReplicationOptions(publisher_host="src-direct", publisher_port=5432)

    h.provider.base_copy(SOURCE, TARGET, "mig-42", options)

    replication = [c for c in h.connections if c.get("replication") == "database"]
    assert [(c["host"], c["port"]) for c in replication] == [("src-direct", 5432)]
    # O pg_dump continua pelo endpoint normal; o snapshot vale para qualquer sessao do servidor.
    dump = h.commands[0]["args"]
    assert dump[dump.index("--host") + 1] == "src"
