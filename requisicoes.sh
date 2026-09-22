#!/bin/bash

last_provider=""

while true; do
    # Faz a requisição POST e armazena a resposta
    response=$(curl -s -X POST http://localhost:8080/ms2/api/process)
    
    # Extrai os campos ms_name e provider usando o jq
    ms_name=$(echo "$response" | jq -r '.ms_name')
    provider=$(echo "$response" | jq -r '.provider')
    
    # Se o provider atual for diferente do último impresso, exibe a mensagem
    if [ "$provider" != "$last_provider" ]; then
        echo "API ${ms_name} rodando em ${provider}"
        last_provider="$provider"
    fi
    
    sleep 1
done