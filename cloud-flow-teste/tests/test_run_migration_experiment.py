"""Pipeline de experimento (scripts/run_migration_experiment.py) com ambiente simulado."""
import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_migration_experiment.py"
_spec = importlib.util.spec_from_file_location("run_migration_experiment", _PATH)
experiment = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = experiment
_spec.loader.exec_module(experiment)


class FakeEnvironment(experiment.Environment):
    """Simula manager/Gateway: cada tentativa segue o roteiro de ``outcomes``."""

    def __init__(self, args, outcomes):
        super().__init__(args)
        self.outcomes = list(outcomes)
        self.prepared = []
        self.current = None

    def prepare(self, previous_migration_id):
        self.prepared.append(previous_migration_id)

    def task_arn(self):
        return "arn:aws:ecs:us-east-1:000000000000:task/microservices-demo/abc"

    def start(self, payload):
        assert payload["source_workload"]["resource_id"].endswith("/abc")
        assert payload["source_workload"]["container_only"] is True
        self.current = self.outcomes.pop(0)
        return 202, {"migrationId": payload["migration_id"]}

    def status(self, migration_id):
        if self.current is None:
            return {"status": "COMPLETED", "state": "completed", "downtime_seconds": None}
        if isinstance(self.current, float):
            return {"status": "COMPLETED", "state": "completed", "downtime_seconds": self.current}
        return {"status": "FAILED", "state": "failed", "error": self.current, "downtime_seconds": 0.5}

    def public_provider(self):
        return "azure"


def _run(tmp_path, n, outcomes, *extra):
    args = ["--output", str(tmp_path), "--poll-interval", "0", "--id-prefix", "t", *extra, str(n)]
    envs = []

    def factory(parsed):
        envs.append(FakeEnvironment(parsed, outcomes))
        return envs[0]

    code = experiment.main(args, env_factory=factory)
    return code, json.loads((tmp_path / "summary.json").read_text()), envs[0]


def test_repete_ate_n_sucessos_e_ignora_falhas_no_downtime(tmp_path):
    code, summary, env = _run(tmp_path, 3, [2.0, "pg_restore falhou", 4.0, None, 6.0])

    assert code == 0
    assert summary["finished"] is True
    assert (summary["N"], summary["K"], summary["K_minus_N"]) == (3, 5, 2)
    assert [d["attempt"] for d in summary["downtimes_seconds"]] == [1, 3, 5]
    stats = summary["downtime_stats_seconds"]
    assert stats["mean"] == pytest.approx(4.0)
    assert (stats["min"], stats["max"], stats["median"]) == (2.0, 6.0, 4.0)
    assert stats["stdev"] == pytest.approx(2.0)
    assert [f["error"] for f in summary["failures"]] == ["pg_restore falhou", "COMPLETED sem downtime_seconds"]
    # Antes de cada tentativa o ambiente e preparado desfazendo a anterior;
    # ao final, o estado inicial e restaurado.
    assert env.prepared == [None, "t-0001", "t-0002", "t-0003", "t-0004", "t-0005"]

    rows = list(csv.DictReader((tmp_path / "attempts.csv").open()))
    assert [r["status"] for r in rows] == ["SUCCESS", "FAILED", "SUCCESS", "FAILED", "SUCCESS"]
    assert rows[1]["downtime_seconds"] == ""


def test_max_attempts_interrompe_sem_concluir(tmp_path):
    code, summary, _ = _run(tmp_path, 2, ["x", "y", 1.0], "--max-attempts", "2")

    assert code == 1
    assert summary["finished"] is False
    assert (summary["N"], summary["K"]) == (0, 2)
    assert summary["downtime_stats_seconds"]["mean"] is None


def test_falha_na_preparacao_aborta(tmp_path):
    class Broken(FakeEnvironment):
        def prepare(self, previous_migration_id):
            raise experiment.PreparationError("origem sem health")

    args = ["--output", str(tmp_path), "--poll-interval", "0", "1"]
    code = experiment.main(args, env_factory=lambda parsed: Broken(parsed, []))

    assert code == 2
    assert json.loads((tmp_path / "summary.json").read_text())["K"] == 0


def test_preparacao_recria_origem_apagada_pelo_ecs(tmp_path):
    """Floci apaga o container parado; o rollback nao consegue religa-lo."""
    args = experiment.parse_args(["--output", str(tmp_path), "--poll-interval", "0", "--settle-seconds", "0", "1"])
    env = experiment.Environment(args)
    calls = []
    healthy = iter([False, False, True])
    env.rollback = lambda migration_id: calls.append(("rollback", migration_id))
    env.source_healthy = lambda: next(healthy)
    env.initial_state_problems = lambda: [] if calls[-1][0] == "run-task" else ["origem sem health"]
    env.running_task_arn = lambda: None

    class Result:
        returncode, stdout, stderr = 0, "arn:aws:ecs:us-east-1:0:task/microservices-demo/new\n", ""

    env.aws = lambda *command: calls.append((command[1], command)) or Result()

    env.prepare("t-0001")

    assert [name for name, _ in calls] == ["rollback", "run-task"]
    assert calls[1][1][calls[1][1].index("--task-definition") + 1] == "ms2"
