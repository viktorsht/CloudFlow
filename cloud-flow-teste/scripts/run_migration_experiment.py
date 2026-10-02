#!/usr/bin/env python3
"""Experimento de downtime: repete a migracao do MS2 ate obter N sucessos.

    python3 scripts/run_migration_experiment.py 30
    python3 scripts/run_migration_experiment.py 30 --mode pre-copy-replication

Cada tentativa:
  1. prepara o ambiente: desfaz a tentativa anterior (rollback) e espera o
     MS2 voltar ao estado inicial (origem AWS saudavel, Gateway roteando para
     a AWS e nenhum recurso do MS2 no destino);
  2. inicia a migracao pela API assincrona (``POST /migrate/<modo>``);
  3. acompanha ``GET /migrate/{id}/status`` ate COMPLETED ou FAILED;
  4. confirma pelo caminho publico que o MS2 responde no destino;
  5. registra numero, status, downtime (so em sucesso) e erro.

O experimento so termina com N sucessos. Falhas sao registradas e nao entram
nas estatisticas de downtime. Se o ambiente nao puder ser devolvido ao estado
inicial, o experimento e interrompido: continuar mediria outra coisa.

Saidas em ``--output`` (padrao ``experiments/<timestamp>``), gravadas a cada
tentativa: ``attempts.csv``, ``attempts.jsonl`` e ``summary.json``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

MODES = {
    "stop-and-migrate": "configs/migration-aws-to-azure.json",
    "pre-copy-replication": "configs/migration-aws-to-azure-precopy.json",
}
TERMINAL = {"COMPLETED", "FAILED"}


class PreparationError(RuntimeError):
    """O ambiente nao voltou ao estado inicial; o experimento nao pode seguir."""


# -- HTTP / shell -------------------------------------------------------------


def http(method: str, url: str, payload: dict | None = None, timeout: float = 30) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())


def _decode(raw: bytes) -> dict | str:
    text = raw.decode(errors="replace")
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return text


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


# -- registro -----------------------------------------------------------------


@dataclass
class Attempt:
    attempt: int
    migration_id: str
    status: str = "FAILED"  # SUCCESS | FAILED
    downtime_seconds: float | None = None
    downtime_started_at: str | None = None
    downtime_finished_at: str | None = None
    final_state: str | None = None
    error: str | None = None
    started_at: str = field(default_factory=now)
    finished_at: str | None = None
    duration_seconds: float | None = None


CSV_FIELDS = list(Attempt.__dataclass_fields__)


def downtime_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "min": None, "max": None, "median": None, "stdev": None}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "median": statistics.median(values),
        # Desvio padrao amostral (n-1); com uma unica amostra e indefinido.
        "stdev": statistics.stdev(values) if len(values) > 1 else None,
    }


def summarize(target: int, attempts: list[Attempt], finished: bool) -> dict:
    successes = [a for a in attempts if a.status == "SUCCESS"]
    downtimes = [a.downtime_seconds for a in successes if a.downtime_seconds is not None]
    return {
        "finished": finished,
        "N_target": target,
        "N": len(successes),
        "K": len(attempts),
        "K_minus_N": len(attempts) - len(successes),
        "downtimes_seconds": [{"attempt": a.attempt, "migration_id": a.migration_id, "downtime_seconds": a.downtime_seconds} for a in successes],
        "downtime_stats_seconds": downtime_stats(downtimes),
        "failures": [{"attempt": a.attempt, "migration_id": a.migration_id, "final_state": a.final_state, "error": a.error} for a in attempts if a.status != "SUCCESS"],
    }


class Recorder:
    def __init__(self, output: Path, target: int, meta: dict) -> None:
        self.output, self.target, self.meta = output, target, meta
        self.attempts: list[Attempt] = []
        output.mkdir(parents=True, exist_ok=True)
        (output / "experiment.json").write_text(json.dumps(meta, indent=2))
        with (output / "attempts.csv").open("w", newline="") as fh:
            csv.DictWriter(fh, CSV_FIELDS).writeheader()

    def add(self, attempt: Attempt, raw_status: dict | str | None) -> None:
        self.attempts.append(attempt)
        with (self.output / "attempts.csv").open("a", newline="") as fh:
            csv.DictWriter(fh, CSV_FIELDS).writerow(asdict(attempt))
        with (self.output / "attempts.jsonl").open("a") as fh:
            fh.write(json.dumps({**asdict(attempt), "raw_status": raw_status}) + "\n")
        self.write_summary(finished=False)

    def write_summary(self, finished: bool) -> dict:
        summary = summarize(self.target, self.attempts, finished)
        (self.output / "summary.json").write_text(json.dumps(summary, indent=2))
        return summary


# -- ambiente -----------------------------------------------------------------


class Environment:
    """Acesso ao manager, ao Gateway, ao ECS (Floci) e ao Docker do host."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.base_payload = json.loads((ROOT / (args.config or MODES[args.mode])).read_text())
        self.service_id = self.base_payload["microservice"]["id"]

    # Estado inicial ------------------------------------------------------

    def rollback(self, migration_id: str) -> None:
        try:
            code, body = http("POST", f"{self.args.manager}/migrations/{migration_id}/rollback", timeout=self.args.request_timeout)
        except (urllib.error.URLError, OSError) as exc:
            log(f"  rollback {migration_id}: manager inacessivel ({exc})")
            return
        log(f"  rollback {migration_id}: HTTP {code} {body if code != 200 else body.get('message')}")

    def public_provider(self) -> str | None:
        code, body = http("POST", self.args.public_url, timeout=15)
        return body.get("provider") if code == 200 and isinstance(body, dict) else None

    def source_healthy(self) -> bool:
        try:
            code, _ = http("GET", self.args.source_health_url, timeout=10)
        except (urllib.error.URLError, OSError):
            return False
        return code == 200

    def leftover_target_containers(self) -> list[str]:
        names: list[str] = []
        for prefix in self.args.target_container_prefix:
            try:
                result = run(["docker", "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"])
            except FileNotFoundError:
                return []  # sem Docker CLI no host: a verificacao fica so pelo Gateway
            names += [line for line in result.stdout.split() if line]
        return names

    def initial_state_problems(self) -> list[str]:
        problems = []
        try:
            provider = self.public_provider()
        except (urllib.error.URLError, OSError) as exc:
            provider, problems = None, [f"Gateway inacessivel: {exc}"]
        if provider != self.args.source_provider:
            problems.append(f"rota publica responde provider={provider!r}, esperado {self.args.source_provider!r}")
        if not self.source_healthy():
            problems.append(f"origem sem health em {self.args.source_health_url}")
        leftovers = self.leftover_target_containers()
        if leftovers:
            problems.append(f"recursos do destino ainda existem: {', '.join(leftovers)}")
        return problems

    def prepare(self, previous_migration_id: str | None) -> None:
        if previous_migration_id:
            self.rollback(previous_migration_id)
        if self.args.mode == "pre-copy-replication":
            result = run([str(ROOT / "scripts/enable-logical-replication.sh"), self.service_id])
            if result.returncode:
                raise PreparationError(f"enable-logical-replication falhou: {result.stdout}{result.stderr}")
        deadline = time.monotonic() + self.args.prepare_timeout
        reprovisioned = False
        while True:
            problems = self.initial_state_problems()
            if not problems:
                break
            if not reprovisioned and not self.source_healthy():
                reprovisioned = self.ensure_source_task()
            if time.monotonic() >= deadline:
                raise PreparationError("ambiente fora do estado inicial: " + "; ".join(problems))
            time.sleep(self.args.poll_interval)
        if self.args.settle_seconds:
            time.sleep(self.args.settle_seconds)

    def ensure_source_task(self) -> bool:
        """Recria a task de origem quando o ECS ja nao tem nenhuma em execucao.

        O Floci marca a task como STOPPED e apaga o container assim que o
        manager o para; o ``docker start`` do rollback nao tem o que religar.
        A task nova usa a mesma task definition e o mesmo RDS (que a migracao
        nao altera), entao equivale ao estado inicial. Retorna True se recriou.
        """
        if self.args.task_arn or self.running_task_arn():
            return False
        result = self.aws("ecs", "run-task", "--cluster", self.args.cluster,
                          "--task-definition", self.args.source_task_definition or self.args.family,
                          "--count", "1", "--query", "tasks[0].taskArn")
        arn = result.stdout.strip()
        if result.returncode or not arn or arn == "None":
            raise PreparationError(f"nao foi possivel recriar a task de origem: {result.stderr.strip()}")
        log(f"  origem recriada no ECS: {arn}")
        return True

    # Migracao ------------------------------------------------------------

    def aws(self, *command: str) -> subprocess.CompletedProcess:
        env = {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", **os.environ}
        return subprocess.run(
            ["aws", *command, "--endpoint-url", self.args.aws_endpoint, "--region", self.args.region, "--output", "text"],
            text=True, capture_output=True, check=False, env=env,
        )

    def running_task_arn(self) -> str | None:
        result = self.aws("ecs", "list-tasks", "--cluster", self.args.cluster, "--family", self.args.family,
                          "--desired-status", "RUNNING", "--query", "taskArns[0]")
        arn = result.stdout.strip()
        return None if result.returncode or not arn or arn == "None" else arn

    def task_arn(self) -> str:
        arn = self.args.task_arn or self.running_task_arn()
        if not arn:
            raise PreparationError(f"task ECS de origem nao encontrada (familia {self.args.family})")
        return arn

    def payload(self, migration_id: str, arn: str) -> dict:
        # Mesmos ajustes de scripts/migrate_ms2_aws_to_azure.py.
        payload = json.loads(json.dumps(self.base_payload))
        payload["migration_id"] = migration_id
        workload = payload["source_workload"]
        payload["source_workload"] = {**workload, "resource_id": arn, "container_only": True}
        payload["target"]["runtime_options"].pop("database_host", None)
        payload["target"]["runtime_options"].setdefault("gateway_endpoint", payload["target"]["endpoint"])
        return payload

    def start(self, payload: dict) -> tuple[int, dict | str]:
        return http("POST", f"{self.args.manager}/migrate/{self.args.mode}", payload, timeout=self.args.request_timeout)

    def status(self, migration_id: str) -> dict:
        code, body = http("GET", f"{self.args.manager}/migrate/{migration_id}/status", timeout=self.args.request_timeout)
        if code != 200 or not isinstance(body, dict):
            raise RuntimeError(f"status HTTP {code}: {body}")
        return body


# -- experimento --------------------------------------------------------------


def wait_terminal(env: Environment, migration_id: str, timeout: float, poll: float) -> tuple[dict | None, bool]:
    """Acompanha o status ate COMPLETED/FAILED. Retorna (status, estourou_timeout).

    Erros transitorios de rede sao tolerados ate o prazo.
    """
    deadline = time.monotonic() + timeout
    last: dict | None = None
    last_state = None
    while time.monotonic() < deadline:
        try:
            last = env.status(migration_id)
        except (RuntimeError, urllib.error.URLError, OSError) as exc:
            log(f"  status indisponivel: {exc}")
        else:
            if last.get("state") != last_state:
                last_state = last.get("state")
                log(f"  {last.get('status')} / {last_state}")
            if last.get("status") in TERMINAL:
                return last, False
        time.sleep(poll)
    return last, True


def run_attempt(env: Environment, number: int, prefix: str) -> tuple[Attempt, dict | str | None]:
    attempt = Attempt(attempt=number, migration_id=f"{prefix}-{number:04d}")
    began = time.monotonic()
    raw: dict | str | None = None
    try:
        arn = env.task_arn()
        code, body = env.start(env.payload(attempt.migration_id, arn))
        if code != 202:
            raise RuntimeError(f"inicio recusado: HTTP {code}: {body}")
        raw, timed_out = wait_terminal(env, attempt.migration_id, env.args.attempt_timeout, env.args.poll_interval)
        if timed_out:
            attempt.error = f"timeout de {env.args.attempt_timeout:.0f}s"
            # A migracao ainda roda no manager; e preciso que termine antes do
            # rollback, senao a proxima tentativa nao parte do estado inicial.
            raw, still_running = wait_terminal(env, attempt.migration_id, env.args.attempt_timeout, env.args.poll_interval)
            if still_running:
                raise PreparationError(f"{attempt.migration_id} nao terminou nem apos o prazo extra")
        attempt.final_state = raw.get("state")
        attempt.downtime_started_at = raw.get("downtime_started_at")
        attempt.downtime_finished_at = raw.get("downtime_finished_at")
        if attempt.error is None and raw.get("status") == "COMPLETED":
            provider = env.public_provider()
            if raw.get("downtime_seconds") is None:
                attempt.error = "COMPLETED sem downtime_seconds"
            elif provider != env.args.target_provider:
                attempt.error = f"rota publica responde provider={provider!r} apos a migracao"
            else:
                attempt.status = "SUCCESS"
                attempt.downtime_seconds = raw["downtime_seconds"]
        elif attempt.error is None:
            attempt.error = raw.get("error") or f"status {raw.get('status')}"
    except PreparationError:
        raise
    except Exception as exc:  # noqa: BLE001 - qualquer erro conta como falha da tentativa
        attempt.error = str(exc) or type(exc).__name__
    attempt.finished_at = now()
    attempt.duration_seconds = round(time.monotonic() - began, 3)
    return attempt, raw


def experiment(args: argparse.Namespace, env: Environment, recorder: Recorder) -> dict:
    prefix = args.id_prefix or f"exp-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    previous: str | None = None
    successes = 0
    number = 0
    while successes < args.n:
        if args.max_attempts and number >= args.max_attempts:
            log(f"Limite de {args.max_attempts} tentativas atingido com {successes}/{args.n} sucessos")
            return recorder.write_summary(finished=False)
        number += 1
        log(f"Tentativa {number} (sucessos {successes}/{args.n}): preparando ambiente")
        env.prepare(previous)
        attempt, raw = run_attempt(env, number, prefix)
        previous = attempt.migration_id
        recorder.add(attempt, raw)
        if attempt.status == "SUCCESS":
            successes += 1
            log(f"Tentativa {number}: SUCESSO, downtime {attempt.downtime_seconds:.3f}s")
        else:
            log(f"Tentativa {number}: FALHA ({attempt.error})")
    if args.restore_at_end and previous:
        log("Restaurando o estado inicial")
        env.prepare(previous)
    return recorder.write_summary(finished=True)


def fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f} s"


def print_report(summary: dict, output: Path) -> None:
    stats = summary["downtime_stats_seconds"]
    print("\n================ RESULTADO DO EXPERIMENTO ================")
    print(f"N     (migracoes bem-sucedidas): {summary['N']}" + ("" if summary["finished"] else f"  (meta {summary['N_target']}, INCOMPLETO)"))
    print(f"K     (tentativas realizadas):   {summary['K']}")
    print(f"K - N (falhas):                  {summary['K_minus_N']}")
    print("\nDowntime por migracao bem-sucedida:")
    for index, item in enumerate(summary["downtimes_seconds"], 1):
        print(f"  #{index:<3} tentativa {item['attempt']:<4} {fmt(item['downtime_seconds'])}")
    print("\nDowntime (somente sucessos):")
    for label, key in (("media", "mean"), ("minimo", "min"), ("maximo", "max"), ("mediana", "median"), ("desvio padrao (amostral)", "stdev")):
        print(f"  {label:<25} {fmt(stats[key])}")
    if summary["failures"]:
        print("\nFalhas:")
        for item in summary["failures"]:
            print(f"  tentativa {item['attempt']:<4} [{item['final_state']}] {item['error']}")
    print(f"\nArquivos: {output}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("n", type=int, help="numero N de migracoes bem-sucedidas desejadas")
    parser.add_argument("--mode", choices=sorted(MODES), default="stop-and-migrate")
    parser.add_argument("--config", help="JSON da migracao (padrao: o de configs/ para o modo)")
    parser.add_argument("--manager", default="http://localhost:8000")
    parser.add_argument("--public-url", default="http://localhost:8080/ms2/api/process")
    parser.add_argument("--source-health-url", default="http://localhost:8082/health")
    parser.add_argument("--source-provider", default="aws")
    parser.add_argument("--target-provider", default="azure")
    parser.add_argument("--target-container-prefix", action="append", default=None,
                        help="prefixo de container do destino que nao pode existir no estado inicial (repetivel)")
    parser.add_argument("--aws-endpoint", default="http://localhost:4566")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--cluster", default="microservices-demo")
    parser.add_argument("--family", default="ms2", help="familia da task ECS de origem")
    parser.add_argument("--task-arn", help="ARN fixo da task de origem (pula a descoberta e a recriacao)")
    parser.add_argument("--source-task-definition", help="task definition usada para recriar a origem (padrao: --family)")
    parser.add_argument("--attempt-timeout", type=float, default=900, help="segundos por tentativa")
    parser.add_argument("--prepare-timeout", type=float, default=180, help="segundos para o ambiente voltar ao estado inicial")
    parser.add_argument("--settle-seconds", type=float, default=5, help="pausa apos o estado inicial confirmado")
    parser.add_argument("--poll-interval", type=float, default=2)
    parser.add_argument("--request-timeout", type=float, default=240)
    parser.add_argument("--max-attempts", type=int, default=0, help="trava de seguranca (0 = sem limite)")
    parser.add_argument("--id-prefix", help="prefixo dos migration_id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-restore-at-end", dest="restore_at_end", action="store_false",
                        help="deixa o MS2 migrado apos a ultima tentativa")
    args = parser.parse_args(argv)
    if args.n < 1:
        parser.error("N deve ser >= 1")
    args.target_container_prefix = args.target_container_prefix or ["floci-az-ca-ms2", "floci-az-pg-ms2"]
    args.output = args.output or ROOT / "experiments" / datetime.now().strftime("%Y%m%d-%H%M%S")
    return args


def main(argv: list[str] | None = None, env_factory: Callable[[argparse.Namespace], Environment] = Environment) -> int:
    args = parse_args(argv)
    env = env_factory(args)
    meta = {key: (str(value) if isinstance(value, Path) else value) for key, value in vars(args).items()}
    recorder = Recorder(args.output, args.n, {**meta, "started_at": now()})
    log(f"Experimento: N={args.n}, modo {args.mode}, saida {args.output}")
    try:
        summary = experiment(args, env, recorder)
        code = 0 if summary["finished"] else 1
    except PreparationError as exc:
        log(f"ABORTADO: {exc}")
        summary, code = recorder.write_summary(finished=False), 2
    except KeyboardInterrupt:
        log("Interrompido pelo usuario")
        summary, code = recorder.write_summary(finished=False), 130
    print_report(summary, args.output)
    return code


if __name__ == "__main__":
    sys.exit(main())
