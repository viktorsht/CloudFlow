#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# microservices-demo
#
# Inicializa a aplicação na AWS emulada pelo Floci:
#
#   Floci
#     ├── ECS
#     │    └── task microservices-demo
#     │         ├── gateway
#     │         ├── ms1
#     │         ├── ms2
#     │         └── ms3
#     │
#     └── RDS
#          ├── ms1-db
#          ├── ms2-db
#          └── ms3-db
#
# Pré-requisitos:
#   - Docker + Docker Compose
#   - AWS CLI
#   - curl
#
# Os quatro containers da aplicação pertencem à mesma task ECS. O modo bridge
# permite que o Floci publique as portas no host; a comunicação entre os
# containers usa as portas publicadas por host.docker.internal.
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

AWS_ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"
AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"

export AWS_ENDPOINT_URL="$AWS_ENDPOINT"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY"

CLUSTER_NAME="microservices-demo"

IMAGE_MS1="microservices-demo/ms1:1.0"
IMAGE_MS2="microservices-demo/ms2:1.0"
IMAGE_MS3="microservices-demo/ms3:1.0"
IMAGE_GATEWAY="microservices-demo/gateway:1.0"

RDS_MS1="ms1-db"
RDS_MS2="ms2-db"
RDS_MS3="ms3-db"

echo "============================================================"
echo "  Microservices Demo - Floci / AWS"
echo "============================================================"
echo

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERRO: '$1' não encontrado."
    exit 1
  }
}

require_command docker
require_command aws
require_command curl

echo "[1/5] Subindo o Floci..."
cd "$ROOT_DIR"
docker compose up -d floci

echo "      Aguardando Floci em $AWS_ENDPOINT ..."
for i in $(seq 1 60); do
  if curl -fsS "$AWS_ENDPOINT/" >/dev/null 2>&1; then
    echo "      Floci está disponível."
    break
  fi

  if [ "$i" -eq 60 ]; then
    echo "ERRO: Floci não ficou disponível."
    docker compose logs --tail=100 floci
    exit 1
  fi

  sleep 2
done

echo
echo "[2/5] Construindo as imagens dos microsserviços..."

docker build -t "$IMAGE_MS1" "$ROOT_DIR/ms1-simple"
docker build -t "$IMAGE_MS2" "$ROOT_DIR/ms2-composite"
docker build -t "$IMAGE_MS3" "$ROOT_DIR/ms3-simple"
docker build -t "$IMAGE_GATEWAY" "$ROOT_DIR/api-gateway"

echo
echo "[3/5] Criando o cluster ECS emulado..."

aws ecs create-cluster \
  --cluster-name "$CLUSTER_NAME" \
  --endpoint-url "$AWS_ENDPOINT" \
  --region "$AWS_REGION" >/dev/null 2>&1 || true

echo
echo "[4/5] Criando os bancos PostgreSQL em RDS..."

create_rds() {
  local identifier="$1"
  local db_name="$2"
  local username="$3"
  local password="$4"

  if aws rds describe-db-instances \
      --db-instance-identifier "$identifier" \
      --endpoint-url "$AWS_ENDPOINT" \
      --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "      $identifier já existe."
    return
  fi

  aws rds create-db-instance \
    --db-instance-identifier "$identifier" \
    --engine postgres \
    --db-instance-class db.t3.micro \
    --allocated-storage 20 \
    --master-username "$username" \
    --master-user-password "$password" \
    --db-name "$db_name" \
    --endpoint-url "$AWS_ENDPOINT" \
    --region "$AWS_REGION" >/dev/null

  echo "      $identifier criado."
}

create_rds "$RDS_MS1" "ms1_db" "ms1_user" "ms1_pass"
create_rds "$RDS_MS2" "ms2_db" "ms2_user" "ms2_pass"
create_rds "$RDS_MS3" "ms3_db" "ms3_user" "ms3_pass"

echo
echo "      Aguardando os três RDS ficarem AVAILABLE..."

wait_rds() {
  local identifier="$1"

  for i in $(seq 1 90); do
    status="$(
      aws rds describe-db-instances \
        --db-instance-identifier "$identifier" \
        --endpoint-url "$AWS_ENDPOINT" \
        --region "$AWS_REGION" \
        --query 'DBInstances[0].DBInstanceStatus' \
        --output text
    )"

    if [ "$status" = "available" ]; then
      echo "      $identifier: available"
      return
    fi

    if [ "$i" -eq 90 ]; then
      echo "ERRO: RDS $identifier não ficou disponível. Status: $status"
      exit 1
    fi

    sleep 2
  done
}

wait_rds "$RDS_MS1"
wait_rds "$RDS_MS2"
wait_rds "$RDS_MS3"

# ------------------------------------------------------------
# O RDS do Floci expõe o PostgreSQL através de um proxy TCP.
# Capturamos as portas atribuídas pelo próprio Floci.
# ------------------------------------------------------------

get_rds_port() {
  aws rds describe-db-instances \
    --db-instance-identifier "$1" \
    --endpoint-url "$AWS_ENDPOINT" \
    --region "$AWS_REGION" \
    --query 'DBInstances[0].Endpoint.Port' \
    --output text
}

DB_PORT_MS1="$(get_rds_port "$RDS_MS1")"
DB_PORT_MS2="$(get_rds_port "$RDS_MS2")"
DB_PORT_MS3="$(get_rds_port "$RDS_MS3")"

echo
echo "      RDS ports:"
echo "        MS1 -> $DB_PORT_MS1"
echo "        MS2 -> $DB_PORT_MS2"
echo "        MS3 -> $DB_PORT_MS3"

# O RDS do Floci publica proxies TCP no host. No Docker Desktop, os
# containers ECS alcançam essas portas por host.docker.internal.
DB_HOST="host.docker.internal"

echo
echo "[5/5] Registrando e iniciando a task ECS..."

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

register_task() {
  local family="$1"
  local file="$2"

  aws ecs register-task-definition \
    --cli-input-json "file://$file" \
    --endpoint-url "$AWS_ENDPOINT" \
    --region "$AWS_REGION" >/dev/null

  echo "      $family registrado."
}

cat > "$TMP_DIR/microservices-demo.json" <<EOF
{
  "family": "microservices-demo",
  "networkMode": "bridge",
  "containerDefinitions": [
    {
      "name": "gateway",
      "image": "$IMAGE_GATEWAY",
      "essential": true,
      "memory": 512,
      "cpu": 256,
      "portMappings": [
        {
          "containerPort": 8080,
          "hostPort": 8080,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {"name": "MS1_URI", "value": "http://host.docker.internal:8081"},
        {"name": "MS2_URI", "value": "http://host.docker.internal:8082"},
        {"name": "MS3_URI", "value": "http://host.docker.internal:8083"}
      ]
    },
    {
      "name": "ms1",
      "image": "$IMAGE_MS1",
      "essential": true,
      "memory": 512,
      "cpu": 256,
      "portMappings": [
        {
          "containerPort": 8081,
          "hostPort": 8081,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {"name": "DB_HOST", "value": "$DB_HOST"},
        {"name": "DB_PORT", "value": "$DB_PORT_MS1"},
        {"name": "DB_NAME", "value": "ms1_db"},
        {"name": "DB_USER", "value": "ms1_user"},
        {"name": "DB_PASSWORD", "value": "ms1_pass"},
        {"name": "APP_PROVIDER", "value": "aws"},
        {"name": "APP_MS_NAME", "value": "ms1"}
      ]
    },
    {
      "name": "ms2",
      "image": "$IMAGE_MS2",
      "essential": true,
      "memory": 512,
      "cpu": 256,
      "portMappings": [
        {
          "containerPort": 8082,
          "hostPort": 8082,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {"name": "DB_HOST", "value": "$DB_HOST"},
        {"name": "DB_PORT", "value": "$DB_PORT_MS2"},
        {"name": "DB_NAME", "value": "ms2_db"},
        {"name": "DB_USER", "value": "ms2_user"},
        {"name": "DB_PASSWORD", "value": "ms2_pass"},
        {"name": "APP_PROVIDER", "value": "aws"},
        {"name": "APP_MS_NAME", "value": "ms2"},
        {"name": "MS3_URL", "value": "http://host.docker.internal:8080/ms3/api/process"}
      ]
    },
    {
      "name": "ms3",
      "image": "$IMAGE_MS3",
      "essential": true,
      "memory": 512,
      "cpu": 256,
      "portMappings": [
        {
          "containerPort": 8083,
          "hostPort": 8083,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {"name": "DB_HOST", "value": "$DB_HOST"},
        {"name": "DB_PORT", "value": "$DB_PORT_MS3"},
        {"name": "DB_NAME", "value": "ms3_db"},
        {"name": "DB_USER", "value": "ms3_user"},
        {"name": "DB_PASSWORD", "value": "ms3_pass"},
        {"name": "APP_PROVIDER", "value": "aws"},
        {"name": "APP_MS_NAME", "value": "ms3"}
      ]
    }
  ]
}
EOF

register_task "microservices-demo" "$TMP_DIR/microservices-demo.json"

run_task() {
  aws ecs run-task \
    --cluster "$CLUSTER_NAME" \
    --task-definition microservices-demo \
    --count 1 \
    --endpoint-url "$AWS_ENDPOINT" \
    --region "$AWS_REGION" \
    --query 'tasks[0].taskArn' \
    --output text
}

TASK_ARN="$(run_task)"
echo "      task: $TASK_ARN"

echo
echo "      Aguardando a aplicação..."

for i in $(seq 1 60); do
  if curl -fsS -X POST \
      http://localhost:8080/ms1/api/process \
      >/dev/null 2>&1; then
    echo "      Gateway + MS1 estão respondendo."
    break
  fi

  if [ "$i" -eq 60 ]; then
    echo "AVISO: MS1 não respondeu dentro do tempo esperado."
    echo
    echo "Logs disponíveis com:"
    echo "  docker ps -a --filter label=floci=true"
    echo "  docker logs <container>"
    break
  fi

  sleep 2
done

echo
echo "============================================================"
echo "  AWS emulada pelo Floci pronta"
echo "============================================================"
echo
echo "Gateway:"
echo "  http://localhost:8080"
echo
echo "Testes:"
echo "  curl -X POST http://localhost:8080/ms1/api/process"
echo "  curl -X POST http://localhost:8080/ms2/api/process"
echo "  curl -X POST http://localhost:8080/ms3/api/process"
echo
echo "ECS:"
echo "  aws ecs list-tasks --cluster $CLUSTER_NAME"
echo
echo "RDS:"
echo "  aws rds describe-db-instances"
echo
echo "Containers criados pelo Floci:"
echo "  docker ps --filter label=floci=true"
echo
echo "Para consultar os logs:"
echo "  docker ps --filter label=floci=true"
echo "  docker logs <container>"
echo
echo "============================================================"
