#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# seed-ms2.sh - popula o ms2_db (RDS do Floci) com histórico de
# requisições, simulando um MS2 que já está em utilização.
#
# Os dados entram direto no banco via SQL (generate_series), sem passar
# pela aplicação. O seed fica fora da imagem e da task definition, para
# que o destino de uma migração nunca o execute.
#
# Uso:
#   ./seed-ms2.sh                         # 10000 linhas, últimos 30 dias
#   SEED_ROWS=100000 ./seed-ms2.sh
#   SEED_ROWS=1000000 SEED_DAYS=90 ./seed-ms2.sh
#   SEED_FORCE=1 ./seed-ms2.sh            # insere mesmo com a tabela populada
#
# Pré-requisitos: Floci no ar, RDS ms2-db available, Docker e AWS CLI.
# O psql roda num container postgres, então não precisa estar instalado.
# ============================================================

AWS_ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"
AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"

SEED_ROWS="${SEED_ROWS:-10000}"
SEED_DAYS="${SEED_DAYS:-30}"
SEED_FORCE="${SEED_FORCE:-0}"
PSQL_IMAGE="${PSQL_IMAGE:-postgres:16-alpine}"

DB_HOST="host.docker.internal"
DB_NAME="ms2_db"
DB_USER="ms2_user"
DB_PASSWORD="ms2_pass"

case "$SEED_ROWS" in ''|*[!0-9]*) echo "ERRO: SEED_ROWS deve ser inteiro."; exit 1 ;; esac
case "$SEED_DAYS" in ''|*[!0-9]*) echo "ERRO: SEED_DAYS deve ser inteiro."; exit 1 ;; esac

DB_PORT="$(aws rds describe-db-instances \
  --db-instance-identifier ms2-db \
  --endpoint-url "$AWS_ENDPOINT" --region "$AWS_REGION" \
  --query 'DBInstances[0].Endpoint.Port' --output text)" || {
  echo "ERRO: RDS ms2-db não encontrado. Rode ./start.sh ms2 antes."
  exit 1
}

psql_ms2() {
  docker run --rm -i \
    -e PGPASSWORD="$DB_PASSWORD" \
    "$PSQL_IMAGE" \
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
         -v ON_ERROR_STOP=1 -q "$@"
}

echo "[seed-ms2] ms2_db em $DB_HOST:$DB_PORT"

# Mesmo schema que o Hibernate gera para RequestLog: com os tipos idênticos,
# o ddl-auto=update do MS2 encontra a tabela pronta e não altera nada.
psql_ms2 <<'SQL'
CREATE TABLE IF NOT EXISTS request_log (
  id         uuid         PRIMARY KEY,
  ip_request varchar(255) NOT NULL,
  provider   varchar(255) NOT NULL,
  ms_name    varchar(255) NOT NULL,
  hash       varchar(512) NOT NULL,
  created_at timestamp(6) NOT NULL
);
SQL

existing="$(psql_ms2 -tA -c 'SELECT count(*) FROM request_log')"
if [ "$existing" -gt 0 ] && [ "$SEED_FORCE" != "1" ]; then
  echo "[seed-ms2] request_log já tem $existing linhas; nada a fazer (SEED_FORCE=1 para inserir mesmo assim)."
  exit 0
fi

echo "[seed-ms2] Inserindo $SEED_ROWS linhas distribuídas nos últimos $SEED_DAYS dias..."
started="$(date +%s)"

# hash = sha256 "do MS2" || sha256 "do MS3": 128 caracteres hex, como o MS2 grava.
psql_ms2 -v rows="$SEED_ROWS" -v days="$SEED_DAYS" <<'SQL'
INSERT INTO request_log (id, ip_request, provider, ms_name, hash, created_at)
SELECT gen_random_uuid(),
       '10.' || (g / 65536 % 256) || '.' || (g / 256 % 256) || '.' || (g % 256),
       'aws',
       'ms2',
       encode(sha256(('ms2-' || g)::bytea), 'hex') || encode(sha256(('ms3-' || g)::bytea), 'hex'),
       now()::timestamp - random() * make_interval(days => :days)
FROM generate_series(1, :rows) AS g;

ANALYZE request_log;
SQL

elapsed=$(( $(date +%s) - started ))
echo "[seed-ms2] Concluído em ${elapsed}s."

psql_ms2 -c "
SELECT count(*)                                  AS linhas,
       min(created_at)                           AS mais_antiga,
       max(created_at)                           AS mais_recente,
       pg_size_pretty(pg_database_size('ms2_db')) AS tamanho_db
FROM request_log;"
