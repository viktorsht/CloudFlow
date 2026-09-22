# microservices-demo

Aplicação de microsserviços rodando em uma AWS emulada pelo [Floci](https://github.com/floci-io/floci). Cada serviço sobe de forma **independente** (task ECS própria) e, quando precisa de banco, tem o seu **próprio RDS PostgreSQL**.

## Arquitetura

```
Floci (AWS emulada, porta 4566)
  ├── ECS task: gateway  ── porta 8080   (sem banco)
  ├── ECS task: ms1      ── porta 8081   ── RDS: ms1-db
  ├── ECS task: ms2      ── porta 8082   ── RDS: ms2-db
  └── ECS task: ms3      ── porta 8083   ── RDS: ms3-db
```

| Serviço   | Diretório       | Porta | Banco    |
|-----------|-----------------|-------|----------|
| `gateway` | `api-gateway`   | 8080  | não tem  |
| `ms1`     | `ms1-simple`    | 8081  | `ms1-db` |
| `ms2`     | `ms2-composite` | 8082  | `ms2-db` |
| `ms3`     | `ms3-simple`    | 8083  | `ms3-db` |

O `ms2` chama o `ms3` através do gateway (`http://host.docker.internal:8080/ms3/api/process`).

## Pré-requisitos

- Docker + Docker Compose
- AWS CLI
- curl

## Estrutura esperada

```
.
├── docker-compose.yml     # sobe apenas o Floci
├── start.sh
├── api-gateway/
├── ms1-simple/
├── ms2-composite/
└── ms3-simple/
```

O `docker-compose.yml` sobe somente o Floci. Os containers dos serviços e dos bancos são criados pelo próprio Floci, via `docker.sock`, e usam a rede Docker `microservices-demo`.

## Como usar o `start.sh`

Dê permissão de execução uma vez:

```bash
chmod +x start.sh
```

### Subir tudo

```bash
./start.sh
```

Sobe os serviços na ordem `ms1 → ms3 → ms2 → gateway` (o `ms2` depende do `ms3` via gateway).

### Subir ou atualizar serviços específicos

```bash
./start.sh ms2              # só o ms2 (e o RDS dele)
./start.sh ms1 gateway      # uma seleção
```

Use esse comando também para **redeploy**: ele refaz o build da imagem, registra uma nova revisão da task, para a task antiga e sobe a nova. Os outros serviços não são afetados.

### Parar um serviço (mantém o banco)

```bash
./start.sh stop ms2
```

### Remover um serviço (para a task e apaga o banco)

```bash
./start.sh destroy ms2
```

> `destroy` remove o RDS. Os dados desse banco são perdidos.

## O que o script faz

Para cada serviço, o `start.sh` executa:

1. **Floci**: se não estiver no ar, roda `docker compose up -d floci` e espera ficar disponível. Também cria o cluster ECS `microservices-demo`.
2. **Build**: `docker build` da imagem `microservices-demo/<serviço>:1.0`.
3. **RDS** (apenas ms1, ms2, ms3): cria o `<serviço>-db` se ainda não existir e espera o status `available`. A porta do banco é a que o Floci atribuiu (faixa 7001-7099).
4. **Task definition**: gera e registra a definição do serviço (um container, modo `bridge`, porta publicada no host), já com as variáveis de ambiente (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, etc.).
5. **Run**: para qualquer task anterior do serviço (libera a porta) e executa `run-task`.
6. **Health check**: espera a porta do serviço responder em `localhost`.

## Testando

```bash
curl -X POST http://localhost:8080/ms1/api/process
curl -X POST http://localhost:8080/ms2/api/process
curl -X POST http://localhost:8080/ms3/api/process
```

Os serviços também respondem diretamente em `localhost:8081`, `8082` e `8083`.

## Comandos úteis

```bash
# Tasks em execução no ECS
aws ecs list-tasks --cluster microservices-demo --endpoint-url http://localhost:4566

# Bancos RDS criados
aws rds describe-db-instances \
  --query 'DBInstances[].DBInstanceIdentifier' \
  --endpoint-url http://localhost:4566

# Containers criados pelo Floci
docker ps --filter label=floci=true

# Logs de um container
docker logs <container>
```

## Variáveis de ambiente

O script usa estes valores por padrão e aceita sobrescrita:

| Variável                | Padrão                  |
|-------------------------|-------------------------|
| `AWS_ENDPOINT_URL`      | `http://localhost:4566` |
| `AWS_DEFAULT_REGION`    | `us-east-1`             |
| `AWS_ACCESS_KEY_ID`     | `test`                  |
| `AWS_SECRET_ACCESS_KEY` | `test`                  |

## Problemas comuns

- **`host.docker.internal` não resolve (Linux):** esse nome só existe por padrão no Docker Desktop. No Linux, ajuste `DB_HOST` e as URLs no `start.sh` para o IP da bridge (por exemplo `172.17.0.1`) ou o gateway da rede `microservices-demo`.
- **Serviço não respondeu a tempo:** veja os containers com `docker ps -a --filter label=floci=true` e os logs com `docker logs <container>`.
- **Porta já em uso:** rode `./start.sh stop <serviço>` ou faça um novo deploy do serviço, que já para a task anterior.
- **`ms2` falha ao chamar o `ms3`:** confirme que o `gateway` e o `ms3` estão no ar (a ordem padrão já cuida disso).