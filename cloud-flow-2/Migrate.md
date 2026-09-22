# Teste completo: migração do MS2 AWS → Azure

Este roteiro migra o MS2 do ECS/RDS no Floci para Azure Container Apps e
Azure PostgreSQL no Floci-AZ, preservando a URL pública:

```text
POST http://localhost:8080/ms2/api/process
```

A origem AWS fica parada e retida ao final. Não execute a limpeza definitiva
antes de concluir a validação manual.

## Pré-requisitos

- Docker Desktop em execução.
- AWS CLI instalada.
- A origem AWS já está funcional no Floci, com Gateway em `localhost:8080`,
  Floci em `localhost:4566`, MS2 em `localhost:8082` e MS3 em
  `localhost:8083`.
- O arquivo `scripts/migrate_ms2_aws_to_azure.py` está presente neste
  diretório.

## 1. Preparar as credenciais

No terminal, dentro de `migration-manager-novo`:

```bash
export SECRET_MS2_DATABASE_SOURCE_USER=ms2_user
export SECRET_MS2_DATABASE_SOURCE_PASSWORD=ms2_pass
export SECRET_MS2_DATABASE_TARGET_USER=ms2_user
export SECRET_MS2_DATABASE_TARGET_PASSWORD=ms2_pass

export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
```

As senhas não são gravadas nos JSONs; o manager resolve as referências por
essas variáveis de ambiente.

## 2. Confirmar a origem AWS

```bash
curl -f http://localhost:8082/health
curl -f http://localhost:8083/health

curl -sS -X POST http://localhost:8080/ms2/api/process
```

O retorno deve conter `provider: "aws"`.

Confirme ainda que o Gateway permite alteração de rotas:

```bash
curl -sS http://localhost:8080/actuator/gateway
curl -sS http://localhost:8080/actuator/gateway/routedefinitions
```

Deve existir uma rota estática `ms2-route` apontando a
`http://host.docker.internal:8082`.

## 3. Subir o Floci-AZ e o manager

O Floci AWS e o Gateway atuais já ocupam `4566` e `8080`. Por isso, suba
somente o Floci-AZ pelo Compose:

```bash
docker compose up -d floci-az
```

Crie o manager apontando para o Floci AWS já existente no host:

```bash
docker compose run -d \
  --name migration-manager-ms2 \
  --service-ports \
  --no-deps \
  -e AWS_ENDPOINT_URL=http://host.docker.internal:4566 \
  migration-manager
```

Espere a API ficar disponível:

```bash
curl -f http://localhost:8000/docs
```

## 4. Executar a migração

```bash
python3 scripts/migrate_ms2_aws_to_azure.py
```

O executável realiza, nesta ordem:

1. Descobre a task ECS e o RDS de origem.
2. Cria Azure PostgreSQL e o Container App MS2.
3. Valida o health do destino.
4. Instala uma rota temporária de manutenção `503`.
5. Executa `pg_dump`/`pg_restore` e compara schema/contagem de linhas.
6. Cria a sobreposição `ms2-route-migration` no Gateway.
7. Valida a chamada pública do MS2.
8. Para somente o container AWS do MS2, preservando MS1, MS3 e o Gateway.

O resultado esperado termina com:

```json
{
  "success": true,
  "final_state": "completed"
}
```

## 5. Validar a migração

Valide o caminho público — ele não deve mudar:

```bash
curl -sS -X POST http://localhost:8080/ms2/api/process
```

O resultado deve conter:

```json
"provider": "azure"
```

Valide também o estado registrado pelo manager:

```bash
curl -sS \
  http://localhost:8000/migrations/migration-ms2-aws-to-azure-001

curl -sS -X POST \
  http://localhost:8000/migrations/migration-ms2-aws-to-azure-001/validate
```

Inspecione recursos Azure no Docker:

```bash
docker ps --format '{{.Image}} {{.Status}} {{.Names}}' | \
  rg 'floci-az-ca-ms2|floci-az-pg-ms2'
```

Devem existir um Container App com prefixo `floci-az-ca-ms2` e um PostgreSQL
com prefixo `floci-az-pg-ms2`.

## Rollback

Se a migração falhar ou a validação manual não for aprovada, restaure a AWS:

```bash
python3 scripts/migrate_ms2_aws_to_azure.py --rollback
```

O rollback remove a sobreposição de rota, reativa o container AWS do MS2 e
remove o Container App e banco do destino. Confirme:

```bash
curl -sS -X POST http://localhost:8080/ms2/api/process
```

O resultado deve voltar a conter `provider: "aws"`.

## Limpeza definitiva da origem

Só depois da validação manual bem-sucedida, remova definitivamente o MS2 e
o RDS de origem:

```bash
curl -i -X POST \
  http://localhost:8000/migrations/migration-ms2-aws-to-azure-001/finalize
```

Essa operação é destrutiva e não deve ser usada como parte do teste normal.

## Problemas conhecidos e correções rápidas

### Actuator Gateway retorna 404

O Gateway precisa expor o endpoint `gateway`. Recrie a imagem/task de origem
após confirmar que `api-gateway/src/main/resources/application.yml` contém:

```yaml
management:
  endpoint:
    gateway:
      enabled: true
```

### `ms2-route` aparece duas vezes

Remova a definição dinâmica residual e recarregue o Gateway. A rota estática
definida em YAML permanece:

```bash
curl -i -X DELETE http://localhost:8080/actuator/gateway/routes/ms2-route
curl -i -X POST http://localhost:8080/actuator/gateway/refresh
```

### O Container App responde 500 ao chamar MS3

O executável atual injeta `MS3_URL=http://host.docker.internal:8080/ms3/api/process`.
Recrie o manager, faça rollback e execute novamente para usar essa correção.

### Logs úteis

```bash
docker logs --tail=200 migration-manager-ms2
docker logs --tail=200 migration-manager-novo-floci-az-1
docker logs --tail=200 <container-do-container-app-ms2>
docker logs --tail=200 <container-do-gateway>
```
