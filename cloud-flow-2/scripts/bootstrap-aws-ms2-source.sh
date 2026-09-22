#!/usr/bin/env bash
# Sobe a origem AWS em tasks independentes para que somente MS2 seja parada.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_DIR="$ROOT_DIR/Código antigo/microservices-demo"
AWS_ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"
AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="$AWS_REGION"

aws_cmd() { aws --endpoint-url "$AWS_ENDPOINT" --region "$AWS_REGION" "$@"; }

docker build -t microservices-demo/ms2:1.0 "$DEMO_DIR/ms2-composite"
docker build -t microservices-demo/ms3:1.0 "$DEMO_DIR/ms3-simple"
aws_cmd ecs create-cluster --cluster-name microservices-demo >/dev/null 2>&1 || true

create_db() {
  local id="$1" db="$2" user="$3" password="$4"
  aws_cmd rds describe-db-instances --db-instance-identifier "$id" >/dev/null 2>&1 || \
    aws_cmd rds create-db-instance --db-instance-identifier "$id" --engine postgres \
      --db-instance-class db.t3.micro --allocated-storage 20 --master-username "$user" \
      --master-user-password "$password" --db-name "$db" >/dev/null
  for _ in $(seq 1 90); do
    local status
    status="$(aws_cmd rds describe-db-instances --db-instance-identifier "$id" --query 'DBInstances[0].DBInstanceStatus' --output text)"
    [ "$status" = available ] && break
    sleep 2
  done
  aws_cmd rds describe-db-instances --db-instance-identifier "$id" --query 'DBInstances[0].Endpoint.Port' --output text
}

MS2_DB_PORT="$(create_db ms2-db ms2_db ms2_user ms2_pass)"
MS3_DB_PORT="$(create_db ms3-db ms3_db ms3_user ms3_pass)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat > "$TMP_DIR/ms3.json" <<EOF
{"family":"ms3-source","networkMode":"bridge","containerDefinitions":[{"name":"ms3","image":"microservices-demo/ms3:1.0","essential":true,"memory":512,"portMappings":[{"containerPort":8083,"hostPort":8083,"protocol":"tcp"}],"environment":[{"name":"DB_HOST","value":"host.docker.internal"},{"name":"DB_PORT","value":"$MS3_DB_PORT"},{"name":"DB_NAME","value":"ms3_db"},{"name":"DB_USER","value":"ms3_user"},{"name":"DB_PASSWORD","value":"ms3_pass"},{"name":"APP_PROVIDER","value":"aws"},{"name":"APP_MS_NAME","value":"ms3"}]}]}
EOF
cat > "$TMP_DIR/ms2.json" <<EOF
{"family":"ms2-source","networkMode":"bridge","containerDefinitions":[{"name":"ms2","image":"microservices-demo/ms2:1.0","essential":true,"memory":512,"portMappings":[{"containerPort":8082,"hostPort":8082,"protocol":"tcp"}],"environment":[{"name":"DB_HOST","value":"host.docker.internal"},{"name":"DB_PORT","value":"$MS2_DB_PORT"},{"name":"DB_NAME","value":"ms2_db"},{"name":"DB_USER","value":"ms2_user"},{"name":"DB_PASSWORD","value":"ms2_pass"},{"name":"APP_PROVIDER","value":"aws"},{"name":"APP_MS_NAME","value":"ms2"},{"name":"MS3_URL","value":"http://host.docker.internal:8080/ms3/api/process"}]}]}
EOF

for service in ms3 ms2; do
  arn="$(aws_cmd ecs register-task-definition --cli-input-json "file://$TMP_DIR/$service.json" --query 'taskDefinition.taskDefinitionArn' --output text)"
  aws_cmd ecs run-task --cluster microservices-demo --task-definition "$arn" --count 1 >/dev/null
done

MS2_TASK_ARN="$(aws_cmd ecs list-tasks --cluster microservices-demo --family ms2-source --query 'taskArns[0]' --output text)"
echo "MS2_TASK_ARN=$MS2_TASK_ARN"
echo "Aguarde o health: curl -f http://localhost:8082/health"
