#!/usr/bin/env python3
"""Executa a migracao do MS2 AWS/Floci para Azure/Floci-AZ.

Funciona tambem com a task legada que contem gateway/MS1/MS2/MS3: nesse caso
o manager controla somente o container Docker do MS2, nunca a task completa.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ID = "migration-ms2-aws-to-azure-001"


def http(method: str, url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url}: HTTP {exc.code}: {detail}") from exc


def task_arn(endpoint: str, region: str, cluster: str) -> str:
    command = ["aws", "ecs", "list-tasks", "--cluster", cluster, "--endpoint-url", endpoint, "--region", region, "--query", "taskArns[0]", "--output", "text"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode or not result.stdout.strip() or result.stdout.strip() == "None":
        raise RuntimeError(f"Nao foi possivel localizar task ECS de origem: {result.stderr.strip()}")
    return result.stdout.strip()


def request_payload(arn: str, manager_gateway_url: str) -> dict:
    payload = json.loads((ROOT / "configs/migration-aws-to-azure.json").read_text())
    payload["source_workload"] = {"resource_id": arn, "container_name": "ms2", "port": 8082, "container_only": True}
    payload["ingress"]["gateway_admin_url"] = manager_gateway_url
    # /connect devolve o hostname da sidecar PostgreSQL na rede Docker do
    # Floci-AZ; nao o substitua por host.docker.internal:5432.
    payload["target"]["runtime_options"].pop("database_host", None)
    # O gateway atualmente em ECS esta fora da rede do Floci-AZ.
    payload["target"]["runtime_options"]["gateway_endpoint"] = "http://host.docker.internal:4577"
    payload["microservice"]["container"]["environment"]["MS3_URL"] = "http://host.docker.internal:8080/ms3/api/process"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manager", default="http://localhost:8000")
    parser.add_argument("--aws-endpoint", default="http://localhost:4566")
    parser.add_argument("--cluster", default="microservices-demo")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--gateway-admin-url", default="http://host.docker.internal:8080/actuator/gateway")
    parser.add_argument("--rollback", action="store_true", help="executa rollback da migracao existente")
    args = parser.parse_args()

    for name in ("SECRET_MS2_DATABASE_SOURCE_USER", "SECRET_MS2_DATABASE_SOURCE_PASSWORD", "SECRET_MS2_DATABASE_TARGET_USER", "SECRET_MS2_DATABASE_TARGET_PASSWORD"):
        if not os.getenv(name):
            raise SystemExit(f"Variavel obrigatoria ausente: {name}")
    if args.rollback:
        print(json.dumps(http("POST", f"{args.manager}/migrations/{MIGRATION_ID}/rollback"), indent=2))
        return 0
    arn = task_arn(args.aws_endpoint, args.region, args.cluster)
    payload = request_payload(arn, args.gateway_admin_url)
    print(f"Migrando MS2 da task {arn}")
    http("POST", f"{args.manager}/migrations", payload)
    result = http("POST", f"{args.manager}/migrations/{MIGRATION_ID}/execute")
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(1)
