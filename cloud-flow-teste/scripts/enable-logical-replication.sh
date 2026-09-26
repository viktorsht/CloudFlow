#!/usr/bin/env bash
# Prepara o RDS (Floci) de um microsservico para a estrategia pre_copy_replication.
#
#   ./scripts/enable-logical-replication.sh ms2 [rede-do-destino]
#
# 1. Liga wal_level=logical (ALTER SYSTEM + restart; persiste no volume do banco).
# 2. Conecta o container do banco a rede do destino com o alias <svc>-db-direct.
#    O proxy TCP do Floci (portas 7001-7099) nao repassa o protocolo de
#    replicacao, entao o manager e a subscription no destino precisam falar
#    direto com o PostgreSQL: use "publisher_host": "<svc>-db-direct" e
#    "publisher_port": 5432 em "replication".
#
# Idempotente: pode ser executado de novo sem efeito colateral.
set -euo pipefail

svc="${1:?uso: $0 <microsservico> [rede-do-destino]}"
network="${2:-floci-az}"
db="${svc}_db" user="${svc}_user" alias="${svc}-db-direct"

container=""
for candidate in $(docker ps --filter name=floci-rds-db --format '{{.Names}}'); do
  if docker inspect "$candidate" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -qx "POSTGRES_DB=$db"; then
    container="$candidate"
    break
  fi
done
[ -n "$container" ] || { echo "ERRO: nenhum container floci-rds-db com POSTGRES_DB=$db em execucao"; exit 1; }
echo "[$svc] banco: $container"

psql_in() { docker exec "$container" psql -U "$user" -d "$db" -Atc "$1"; }

if [ "$(psql_in 'SHOW wal_level')" = "logical" ]; then
  echo "[$svc] wal_level ja e logical"
else
  psql_in "ALTER SYSTEM SET wal_level = 'logical'" >/dev/null
  echo "[$svc] reiniciando o banco para aplicar wal_level=logical..."
  docker restart "$container" >/dev/null
  for _ in $(seq 1 30); do
    docker exec "$container" pg_isready -U "$user" -q && break
    sleep 1
  done
  [ "$(psql_in 'SHOW wal_level')" = "logical" ] || { echo "ERRO: wal_level nao mudou"; exit 1; }
  echo "[$svc] wal_level=logical"
fi

if docker inspect "$container" --format '{{range $k, $v := .NetworkSettings.Networks}}{{println $k}}{{end}}' | grep -qx "$network"; then
  echo "[$svc] ja conectado a rede $network"
else
  docker network connect --alias "$alias" "$network" "$container"
  echo "[$svc] conectado a rede $network como $alias"
fi

echo "[$svc] pronto: use \"replication\": {\"publisher_host\": \"$alias\", \"publisher_port\": 5432}"
