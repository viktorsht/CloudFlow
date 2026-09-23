#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# floci-az
#
# Sobe o emulador Azure (Floci-AZ) de forma isolada:
#
#   Floci-AZ
#     ├── Container Apps   (destino de compute)
#     └── PostgreSQL       (destino de dados)
#
# Este projeto não conhece microsserviços, orquestrador de migração ou
# qualquer outro provedor — é só o provedor de DESTINO, subido à parte.
# Outros projetos (ex.: cloud-flow) apontam para ele via `target.endpoint`
# na própria requisição, nunca por configuração fixa.
#
# Pré-requisitos:
#   - Docker + Docker Compose
#   - curl
# ============================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FLOCI_AZ_ENDPOINT="${FLOCI_AZ_ENDPOINT_URL:-http://localhost:4577}"
SUBSCRIPTION_ID="00000000-0000-0000-0000-000000000000"
RESOURCE_GROUP="floci-migrations"

echo "============================================================"
echo "  Floci-AZ - emulador Azure (Container Apps + PostgreSQL)"
echo "============================================================"
echo

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERRO: '$1' não encontrado."
    exit 1
  }
}

require_command docker
require_command curl

echo "[1/2] Subindo o Floci-AZ..."
cd "$ROOT_DIR"
docker compose up -d floci-az

echo "      Aguardando Floci-AZ em $FLOCI_AZ_ENDPOINT ..."
for i in $(seq 1 60); do
  if curl -fsS "$FLOCI_AZ_ENDPOINT/" >/dev/null 2>&1; then
    echo "      Floci-AZ está disponível."
    break
  fi

  if [ "$i" -eq 60 ]; then
    echo "ERRO: Floci-AZ não ficou disponível."
    docker compose logs --tail=100 floci-az
    exit 1
  fi

  sleep 2
done

echo
echo "============================================================"
echo "  Floci-AZ pronto"
echo "============================================================"
echo
echo "Endpoint:"
echo "  do host:              http://localhost:4577"
echo "  de outro container:   http://host.docker.internal:4577"
echo
echo "Use esse endpoint como 'target.endpoint' na requisição de migração"
echo "de quem for consumir este destino (ex.: cloud-flow, POST"
echo "/migrate/stop-and-migrate). Bloco 'target' pronto para uso, no mesmo"
echo "formato de configs/migration-aws-to-azure.json:"
echo
cat <<JSON
{
  "provider": "azure",
  "region": "eastus",
  "environment": "target",
  "endpoint": "http://host.docker.internal:4577",
  "credentials": {
    "tenant_id": "local",
    "client_id": "local",
    "client_secret": "local",
    "subscription_id": "$SUBSCRIPTION_ID"
  },
  "runtime_options": {
    "subscription_id": "$SUBSCRIPTION_ID",
    "resource_group": "$RESOURCE_GROUP",
    "managed_environment": "$RESOURCE_GROUP"
  }
}
JSON
echo
echo "Testes:"
echo "  curl http://localhost:4577/"
echo
echo "Containers criados pelo Floci-AZ (se seguir a mesma convenção de"
echo "rótulos do Floci):"
echo "  docker ps --filter label=floci-az=true"
echo
echo "Para consultar os logs:"
echo "  docker logs --tail=200 floci-az"
echo "  docker logs --tail=200 <container-criado-pelo-floci-az>"
echo
echo "Para parar:"
echo "  docker compose down"
echo
echo "============================================================"