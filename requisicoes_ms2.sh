#!/bin/bash
# Faz 1 requisicao no MS2 por segundo e imprime o resultado de cada uma.
#
#   ./requisicoes_ms2.sh
#   URL=http://localhost:8080/ms2/api/process INTERVAL=1 ./requisicoes_ms2.sh
#
# A requisicao roda em segundo plano para que uma resposta lenta nao atrase
# a seguinte: o ritmo fica fixo mesmo durante a janela de manutencao.

URL="${URL:-http://localhost:8080/ms2/api/process}"
INTERVAL="${INTERVAL:-1}"
TIMEOUT="${TIMEOUT:-5}"

request() {
  local seq="$1" started body status latency provider
  started="$(date +%H:%M:%S)"
  body="$(curl -s -X POST --max-time "$TIMEOUT" -w '\n%{http_code} %{time_total}' "$URL")"
  read -r status latency <<< "$(tail -n 1 <<< "$body")"
  provider="$(sed '$d' <<< "$body" | jq -r '.provider // "-"' 2>/dev/null)"
  printf '%s #%-5s HTTP %s  %ss  provider=%s\n' "$started" "$seq" "$status" "$latency" "${provider:--}"
}

trap 'wait; exit 0' INT TERM
echo "POST $URL a cada ${INTERVAL}s (Ctrl+C para parar)"

seq=0
while true; do
  seq=$((seq + 1))
  request "$seq" &
  sleep "$INTERVAL"
done
