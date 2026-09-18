Criei o executável [migrate_ms2_aws_to_azure.py](/Users/macbookair/Projetos/mestrado/migration-manager/migration-manager-novo/scripts/migrate_ms2_aws_to_azure.py). Ele usa a task ECS existente, mas para somente o container do MS2 — preservando MS1, MS3 e o gateway da sua listagem.

Execute assim:

```bash
cd /Users/macbookair/Projetos/mestrado/migration-manager/migration-manager-novo

export SECRET_MS2_DATABASE_SOURCE_USER=ms2_user
export SECRET_MS2_DATABASE_SOURCE_PASSWORD=ms2_pass
export SECRET_MS2_DATABASE_TARGET_USER=ms2_user
export SECRET_MS2_DATABASE_TARGET_PASSWORD=ms2_pass

export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
```

Como você já possui o Floci AWS e o Gateway ocupando as portas `4566` e `8080`, suba somente o Floci-AZ:

```bash
docker compose up -d floci-az
```

Suba o manager apontando para o Floci AWS que já está em execução:

```bash
docker compose run -d \
  --name migration-manager-ms2 \
  --service-ports \
  --no-deps \
  -e AWS_ENDPOINT_URL=http://host.docker.internal:4566 \
  migration-manager
```

Confirme que ele iniciou:

```bash
curl -f http://localhost:8000/docs
```

Agora execute a migração completa:

```bash
python3 scripts/migrate_ms2_aws_to_azure.py
```

Valide pelo mesmo endpoint público:

```bash
curl -sS -X POST http://localhost:8080/ms2/api/process
```

O retorno esperado é `provider: "azure"`.

Para acompanhar detalhes:

```bash
curl -sS \
  http://localhost:8000/migrations/migration-ms2-aws-to-azure-001
```

Se houver falha, o CLI já tenta compensar durante a execução. Para solicitar rollback explicitamente:

```bash
python3 scripts/migrate_ms2_aws_to_azure.py --rollback
```

O MS2 AWS fica parado, não removido. Após validar a migração, a remoção definitiva da origem é:

```bash
curl -i -X POST \
  http://localhost:8000/migrations/migration-ms2-aws-to-azure-001/finalize
```