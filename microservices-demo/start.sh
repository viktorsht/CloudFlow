#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# microservices-demo - deploy independente por serviço no Floci
#
#   Floci
#     ├── ECS task family: ms1      ── RDS: ms1-db
#     ├── ECS task family: ms2      ── RDS: ms2-db
#     ├── ECS task family: ms3      ── RDS: ms3-db
#     └── ECS task family: gateway  (sem banco)
#
# Uso:
#   ./start.sh                 # sobe tudo (ms1, ms3, ms2, gateway)
#   ./start.sh ms2             # sobe/atualiza só o ms2 (+ seu RDS)
#   ./start.sh ms1 gateway     # sobe uma seleção
#   ./start.sh stop ms2        # para a task do ms2 (mantém o RDS)
#   ./start.sh destroy ms2     # para a task e remove o RDS do ms2
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

AWS_ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"
AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_ENDPOINT_URL="$AWS_ENDPOINT"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"

CLUSTER_NAME="microservices-demo"
DB_HOST="host.docker.internal"

# ---------- catálogo de serviços -----------------------------
# nome -> diretório / porta
dir_of() {
  case "$1" in
    ms1) echo "ms1-simple" ;;
    ms2) echo "ms2-composite" ;;
    ms3) echo "ms3-simple" ;;
    gateway) echo "api-gateway" ;;
    *) return 1 ;;
  esac
}

port_of() {
  case "$1" in
    ms1) echo "8081" ;;
    ms2) echo "8082" ;;
    ms3) echo "8083" ;;
    gateway) echo "8080" ;;
    *) return 1 ;;
  esac
}

has_db() {
  case "$1" in
    ms1|ms2|ms3) echo "1" ;;
    gateway) echo "0" ;;
    *) return 1 ;;
  esac
}

DEFAULT_ORDER=("ms1" "ms3" "ms2" "gateway")

image_of() { echo "microservices-demo/$1:1.0"; }

# ---------- utilitários --------------------------------------
require_command() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERRO: '$1' não encontrado."; exit 1; }
}

awsx() { aws "$@" --endpoint-url "$AWS_ENDPOINT" --region "$AWS_REGION"; }

# ---------- Floci --------------------------------------------
ensure_floci() {
  if curl -fsS "$AWS_ENDPOINT/" >/dev/null 2>&1; then
    return
  fi
  echo "[floci] Subindo o Floci..."
  (cd "$ROOT_DIR" && docker compose up -d floci)

  for i in $(seq 1 60); do
    curl -fsS "$AWS_ENDPOINT/" >/dev/null 2>&1 && { echo "[floci] disponível."; break; }
    if [ "$i" -eq 60 ]; then
      echo "ERRO: Floci não ficou disponível."
      (cd "$ROOT_DIR" && docker compose logs --tail=100 floci)
      exit 1
    fi
    sleep 2
  done

  awsx ecs create-cluster --cluster-name "$CLUSTER_NAME" >/dev/null 2>&1 || true
}

# ---------- RDS ----------------------------------------------
ensure_rds() {
  local svc="$1"
  local id="$svc-db" db="${svc}_db" user="${svc}_user" pass="${svc}_pass"

  if awsx rds describe-db-instances --db-instance-identifier "$id" >/dev/null 2>&1; then
    echo "[$svc] RDS $id já existe."
  else
    echo "[$svc] Criando RDS $id..."
    awsx rds create-db-instance \
      --db-instance-identifier "$id" \
      --engine postgres \
      --db-instance-class db.t3.micro \
      --allocated-storage 20 \
      --master-username "$user" \
      --master-user-password "$pass" \
      --db-name "$db" >/dev/null
  fi

  echo "[$svc] Aguardando RDS $id ficar available..."
  local status
  for i in $(seq 1 90); do
    status="$(awsx rds describe-db-instances \
      --db-instance-identifier "$id" \
      --query 'DBInstances[0].DBInstanceStatus' --output text)"
    [ "$status" = "available" ] && { echo "[$svc] RDS $id: available"; return; }
    sleep 2
  done
  echo "ERRO: RDS $id não ficou disponível (status: $status)"
  exit 1
}

rds_port() {
  awsx rds describe-db-instances \
    --db-instance-identifier "$1-db" \
    --query 'DBInstances[0].Endpoint.Port' --output text
}

# ---------- variáveis de ambiente por serviço ----------------
env_json() {
  local svc="$1"
  case "$svc" in
    gateway)
      cat <<EOF
[
  {"name": "MS1_URI", "value": "http://host.docker.internal:8081"},
  {"name": "MS2_URI", "value": "http://host.docker.internal:8082"},
  {"name": "MS3_URI", "value": "http://host.docker.internal:8083"}
]
EOF
      ;;
    ms1|ms2|ms3)
      local db_port; db_port="$(rds_port "$svc")"
      local extra=""
      if [ "$svc" = "ms2" ]; then
        extra=',{"name": "MS3_URL", "value": "http://host.docker.internal:8080/ms3/api/process"}'
      fi
      cat <<EOF
[
  {"name": "DB_HOST", "value": "$DB_HOST"},
  {"name": "DB_PORT", "value": "$db_port"},
  {"name": "DB_NAME", "value": "${svc}_db"},
  {"name": "DB_USER", "value": "${svc}_user"},
  {"name": "DB_PASSWORD", "value": "${svc}_pass"},
  {"name": "APP_PROVIDER", "value": "aws"},
  {"name": "APP_MS_NAME", "value": "$svc"}
  $extra
]
EOF
      ;;
  esac
}

# ---------- ECS ----------------------------------------------
stop_service_tasks() {
  local svc="$1" arn
  for arn in $(awsx ecs list-tasks --cluster "$CLUSTER_NAME" --family "$svc" \
                 --query 'taskArns[]' --output text 2>/dev/null || true); do
    echo "[$svc] Parando task $arn"
    awsx ecs stop-task --cluster "$CLUSTER_NAME" --task "$arn" >/dev/null 2>&1 || true
  done
}

register_and_run() {
  local svc="$1"
  local port
  local image
  local tmp

  port="$(port_of "$svc")"
  image="$(image_of "$svc")"
  tmp="$(mktemp)"

  cat > "$tmp" <<EOF
{
  "family": "$svc",
  "networkMode": "bridge",
  "containerDefinitions": [
    {
      "name": "$svc",
      "image": "$image",
      "essential": true,
      "memory": 512,
      "cpu": 256,
      "portMappings": [
        {"containerPort": $port, "hostPort": $port, "protocol": "tcp"}
      ],
      "environment": $(env_json "$svc")
    }
  ]
}
EOF

  awsx ecs register-task-definition --cli-input-json "file://$tmp" >/dev/null
  rm -f "$tmp"
  echo "[$svc] task definition registrada."

  # Re-deploy: derruba a task anterior para liberar a porta do host
  stop_service_tasks "$svc"

  local arn
  arn="$(awsx ecs run-task \
    --cluster "$CLUSTER_NAME" \
    --task-definition "$svc" \
    --count 1 \
    --query 'tasks[0].taskArn' --output text)"
  echo "[$svc] task: $arn"
}

wait_http() {
  local svc="$1"
  local port

  port="$(port_of "$svc")"
  echo "[$svc] Aguardando porta $port..."
  # Sem -f: qualquer resposta HTTP (mesmo 404) prova que o serviço está de pé
  for i in $(seq 1 60); do
    if curl -s -o /dev/null --max-time 2 "http://localhost:$port/"; then
      echo "[$svc] respondendo em http://localhost:$port"
      return
    fi
    sleep 2
  done
  echo "AVISO: [$svc] não respondeu a tempo. Veja: docker ps -a --filter label=floci=true"
}

# ---------- deploy de um serviço -----------------------------
deploy_service() {
  local svc="$1"
  local dir

  dir="$(dir_of "$svc")" || {
    echo "Serviço desconhecido: $svc"
    exit 1
  }

  echo
  echo "=================== $svc ==================="
  echo "[$svc] Build da imagem..."

  docker build \
    -t "$(image_of "$svc")" \
    "$ROOT_DIR/$dir"

  [ "$(has_db "$svc")" = "1" ] && ensure_rds "$svc"

  register_and_run "$svc"
  wait_http "$svc"
}

destroy_service() {
  local svc="$1"
  stop_service_tasks "$svc"
  # if [ "${HAS_DB[$svc]}" = "1" ]; then
  if [ "$(has_db "$svc")" = "1" ]; then
    echo "[$svc] Removendo RDS $svc-db..."
    awsx rds delete-db-instance \
      --db-instance-identifier "$svc-db" --skip-final-snapshot >/dev/null 2>&1 || true
  fi
}

# ---------- main ---------------------------------------------
require_command docker
require_command aws
require_command curl

ACTION="deploy"
case "${1:-}" in
  stop|destroy) ACTION="$1"; shift ;;
esac

SERVICES=("$@")
[ ${#SERVICES[@]} -eq 0 ] && SERVICES=("${DEFAULT_ORDER[@]}")

ensure_floci
awsx ecs create-cluster --cluster-name "$CLUSTER_NAME" >/dev/null 2>&1 || true

for svc in "${SERVICES[@]}"; do
  case "$ACTION" in
    deploy)  deploy_service "$svc" ;;
    stop)    stop_service_tasks "$svc" ;;
    destroy) destroy_service "$svc" ;;
  esac
done

if [ "$ACTION" = "deploy" ]; then
  echo
  echo "============================================================"
  echo "  Serviços no ar"
  echo "============================================================"
  echo "  Gateway : http://localhost:8080"
  echo "  MS1     : http://localhost:8081  (direto)"
  echo "  MS2     : http://localhost:8082  (direto)"
  echo "  MS3     : http://localhost:8083  (direto)"
  echo
  echo "  curl -X POST http://localhost:8080/ms1/api/process"
  echo "  curl -X POST http://localhost:8080/ms2/api/process"
  echo "  curl -X POST http://localhost:8080/ms3/api/process"
  echo
  echo "  aws ecs list-tasks --cluster $CLUSTER_NAME"
  echo "  aws rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier'"
  echo "  docker ps --filter label=floci=true"
fi