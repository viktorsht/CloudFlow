# MigrationManager

## Operação efetiva AWS ↔ Azure

Esta versão migra um microsserviço PostgreSQL por vez entre ECS/RDS no Floci
e Container Apps/Azure PostgreSQL no Floci-AZ. O Gateway Spring roda fora do
workload migrado e é atualizado pelo actuator. O corte instala uma rota 503,
executa `pg_dump`/`pg_restore`, confere tabelas e contagens, troca a rota e
remove a manutenção. A origem é apenas parada e continua disponível para
rollback; a exclusão definitiva exige `POST /migrations/{id}/finalize`.

Cada solicitação é autossuficiente: credenciais de cloud e de banco, URLs de
API e opções de runtime são enviadas no mesmo JSON. A API não consulta
variáveis de ambiente nem secret manager para completar uma migração.

> **Este repositório só migra.** Os microsserviços (`ms1`, `ms2`, `ms3`) e o
> Gateway não são criados nem conhecidos por aqui — eles vivem no projeto
> **microservices-demo**, um repositório separado com seu próprio Floci
> (emulador AWS) e `start.sh`. O `MigrationManager` apenas descobre a task
> ECS de origem já em execução ali, migra os dados e implanta o destino;
> ver "Executando localmente" e "Docker" abaixo para o passo a passo com os
> dois projetos.

Para AWS → Azure, substitua `REPLACE_WITH_ECS_TASK_ARN` no exemplo pelo ARN
de MS2 retornado pelo ECS do projeto microservices-demo (`aws ecs list-tasks
--cluster microservices-demo`). Os arquivos em `configs/` usam as imagens
`microservices-demo/ms1`, `ms2` e `ms3`; MS2 chama MS3 pelo Gateway neutro.

Orquestrador de **migração seletiva de microsserviços em ambientes multi-cloud**.

O `MigrationManager` coordena todas as etapas necessárias para migrar um
único microsserviço de um provedor de nuvem de origem para um provedor de
destino — infraestrutura, implantação, dados, validação, redirecionamento
de tráfego e encerramento da origem — **sem conhecer detalhes concretos**
de nenhuma tecnologia (AWS, Azure, GCP, Docker, Floci, PostgreSQL, etc).

> **Princípio arquitetural:** *"O MigrationManager conhece o processo de
> migração. Os Providers conhecem a infraestrutura."*

---

## Por que este design

Todo o núcleo (`app/domain` e `app/application`) depende exclusivamente de
**abstrações** (`CloudProvider`, `ComputeProvider`, `DataMigrationProvider`,
`TrafficProvider`, `ValidationProvider`, `MigrationStrategy`). As
implementações concretas ficam isoladas em `app/infrastructure` e só são
conectadas ao núcleo através da `ProviderFactory` — a única camada que
conhece o mapeamento entre tipo de provedor/tecnologia e implementação.

Isso permite:

- trocar AWS/Floci por Azure ou GCP sem tocar no `MigrationManager`;
- trocar Docker por ECS, VM ou Kubernetes sem tocar na lógica de migração;
- trocar PostgreSQL por MySQL/RDS sem `if`s espalhados pelo domínio;
- adicionar novas estratégias de migração (`StopAndCopy`, `PreCopy`, ...)
  sem alterar o orquestrador.

## Arquitetura em camadas

```
API (FastAPI)
   │
   ▼
Application (MigrationManager, estratégias, executor, state machine)
   │
   ▼
Domain Contracts (interfaces abstratas + modelos Pydantic)
   │
   ▼
Infrastructure (implementações concretas: AWS/Floci, futuramente Azure/GCP...)
```

**Regra fundamental:** `domain/` nunca importa nada de `infrastructure/`.
A dependência flui sempre "para dentro" (Dependency Inversion).

## Estrutura do projeto

```
migration-manager/
├── app/
│   ├── api/controllers/         # Endpoints REST (FastAPI)
│   ├── application/             # Orquestração: MigrationManager, estratégias,
│   │                             # MigrationContext, MigrationStateMachine,
│   │                             # MigrationExecutor, DependencyManager, ValidationService
│   ├── domain/
│   │   ├── models/               # Modelos Pydantic (MigrationRequest, Deployment, ...)
│   │   ├── enums/                 # MigrationState, CloudProviderType, ...
│   │   └── contracts/             # Interfaces abstratas (ABC)
│   ├── infrastructure/
│   │   ├── providers/aws/         # Implementacao AWS/Floci (compute, data, traffic, validation)
│   │   ├── providers/azure/       # Implementacao Azure/Floci-az (mesmo padrao da AWS)
│   │   └── providers/factory.py   # ProviderFactory (registra AWS e Azure)
│   └── main.py                    # App FastAPI
├── configs/
│   ├── migration.json             # Exemplo original (AWS -> AWS)
│   ├── migration-aws-to-azure.json  # Exemplo: migracao AWS -> Azure
│   └── migration-azure-to-aws.json  # Exemplo: migracao Azure -> AWS
├── tests/                         # Testes unitários (domain/application/infrastructure)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Fluxo de migração (`ContinuousMigrationStrategy`)

A estratégia inicial prioriza **continuidade do serviço**: o microsserviço
de origem continua atendendo requisições até que o novo, no destino, esteja
implantado e validado.

```
PENDING → PREPARING → PREPARED → MIGRATING_DATA → DATA_MIGRATED
   → DEPLOYING_TARGET → TARGET_DEPLOYED → VALIDATING_TARGET → TARGET_VALID
   → REDIRECTING_TRAFFIC → TRAFFIC_REDIRECTED → VALIDATING_APPLICATION
   → MIGRATION_COMPLETED → SOURCE_CLEANUP → COMPLETED
```

Qualquer falha em um estado "em andamento" leva a `FAILED`, a partir do
qual o `MigrationManager.rollback()` pode ser chamado para restaurar o
tráfego para a origem, remover o deployment de destino incompleto e
desfazer a migração de dados.

As transições são controladas explicitamente pela `MigrationStateMachine`
— transições fora do fluxo definido levantam `InvalidStateTransitionError`.

## Suporte multi-cloud: AWS ↔ Azure

O `MigrationManager` e a `ContinuousMigrationStrategy` **nunca** decidem com
base no provedor: `source` e `target` são resolvidos de forma independente
pela `ProviderFactory`, a partir do campo `provider` de cada lado da
`MigrationRequest`. Isso significa que suportar a direção `azure -> aws`
além de `aws -> azure` não exigiu nenhuma mudança no orquestrador, na
máquina de estados ou na estratégia — apenas registrar uma implementação
Azure na factory, espelhando exatamente o padrão já usado para AWS:

| Camada              | AWS                              | Azure (novo)                          |
|----------------------|-----------------------------------|-----------------------------------------|
| Compute              | `DockerComputeProvider`           | `AzureContainerComputeProvider`         |
| Cloud (alto nível)   | `AWSProvider`                     | `AzureProvider`                         |
| Tráfego              | `AWSTrafficProvider`              | `AzureTrafficProvider`                  |
| Validação            | `AWSValidationProvider`           | `AzureValidationProvider`               |
| Dados                | `AWSRDSDataMigrationProvider`     | `AzureDatabaseDataMigrationProvider`    |
| Emulador (Floci)     | `floci` (`AWS_ENDPOINT_URL`)       | `floci-az` (`AZURE_ENDPOINT_URL`)       |

Para migrar `azure -> aws`, basta inverter os valores de `source.provider`
e `target.provider` na `MigrationRequest` (veja
`configs/migration-aws-to-azure.json` e `configs/migration-azure-to-aws.json`).
Nenhuma outra alteração é necessária — o mesmo `MigrationManager`, a mesma
`ContinuousMigrationStrategy` e o mesmo endpoint da API atendem as duas
direções.

> O `DataMigrationProvider` é resolvido pelo **tipo de engine de dados**
> (`postgresql`, `mysql`, `aws_rds`, `azure_database`), não pelo cloud
> provider de origem/destino — por isso o mesmo stub genérico (hoje
> registrado para `postgresql`/`mysql`) já funciona em qualquer combinação
> de nuvens. `AZURE_DATABASE` foi registrado à parte para quando a
> `MigrationRequest` especificar esse engine explicitamente.


## Executando localmente

### 0. Pré-requisito: origem e destino já no ar

O `MigrationManager` não cria infraestrutura de nenhum dos lados — nem a
origem, nem o destino. Ele só migra o que já existe. Antes de qualquer
chamada, é preciso ter no ar, cada um por fora (outro arquivo, projeto ou
pipeline):

- a **origem**: por exemplo o projeto **microservices-demo** (repositório
  separado), com seu Floci (emulador AWS), o Gateway e as tasks que você
  quer migrar;
- o **destino**: o emulador ou provedor real do provedor de saída (ex.:
  Floci-AZ para Azure), também subido à parte.

Exemplo com o microservices-demo como origem:

```bash
cd ../microservices-demo   # caminho do outro projeto
./start.sh                 # sobe floci + gateway + ms1 + ms2 + ms3
```

Confirme que a origem responde antes de seguir:

```bash
curl -X POST http://localhost:8080/ms2/api/process
```

Com isso no ar, o `MigrationManager` descobre a task ECS de origem por
`source_workload.resource_id` (o ARN retornado por `aws ecs list-tasks
--cluster microservices-demo`), fala com o Gateway pelo
`ingress.gateway_admin_url`, e fala com o provedor de destino pelo
`target.endpoint` do body — todos endereços de infraestrutura que já existe,
nunca algo que este projeto sobe ou conhece de antemão.

### 1. Suba o MigrationManager

```bash
pip install -r requirements.txt --break-system-packages   # ou em um venv
uvicorn app.main:app --reload
```

A API sobe em `http://localhost:8000` (docs interativas em `/docs`).

> **Antes do primeiro `curl`:** os exemplos em `configs/` usam
> `host.docker.internal` para o Floci AWS (`4566`), o Gateway (`8080`) e o
> Floci-AZ (`4577`), porque é assim que o `migration-manager` os alcança
> quando ele mesmo roda em Docker (seção "Docker" abaixo). Rodando via
> `uvicorn` direto no host, como este passo 1, `host.docker.internal` não
> resolve — troque por `localhost` nesses três lugares do JSON antes de
> enviar. E, para a direção `aws -> azure`, troque
> `REPLACE_WITH_ECS_TASK_ARN` pelo ARN real da task (`aws ecs list-tasks
> --cluster microservices-demo --endpoint-url http://localhost:4566`).
>
> **Limitação deste modo (uvicorn direto):** o Floci AWS publica o RDS no
> host (por isso `host.docker.internal:PORT` chega até o Postgres da
> origem), mas o Postgres criado pelo Floci-AZ só é alcançável pela rede
> Docker interna dele — o `migration-manager` rodando fora do Docker não
> consegue chegar lá de jeito nenhum. Para migrar com destino Azure, use o
> modo Docker abaixo (o `migration-manager` entra na rede do destino).
>
> **Provisionamento do Postgres no destino Azure:** o container do
> PostgreSQL leva alguns segundos para subir depois que o servidor é criado
> no Floci-AZ; o `migration-manager` espera até 90s por padrão antes de
> desistir. Para ajustar, defina `postgres_ready_timeout_seconds` em
> `target.runtime_options` no JSON da migração.

### API assíncrona por microsserviço (`/migrate`)

Cada requisição migra **um** microsserviço (independente dos demais) pela
estratégia `stop-and-migrate`: a origem é parada, os dados são copiados e o
serviço sobe no destino. Como a operação é demorada, o `POST` só aceita a
migração e responde na hora; o acompanhamento é feito por `GET`.

```bash
# 1. Inicia (responde 202 imediatamente)
curl -X POST http://localhost:8000/migrate/stop-and-migrate \
  -H "Content-Type: application/json" \
  -d @configs/migration-aws-to-azure.json
```

```json
{
  "migrationId": "migration-ms2-aws-to-azure-001",
  "microservice": "ms2",
  "source": "aws",
  "destination": "azure",
  "status": "PENDING"
}
```

```bash
# 2. Acompanha até COMPLETED ou FAILED
curl http://localhost:8000/migrate/migration-ms2-aws-to-azure-001/status
```

```json
{
  "migrationId": "migration-ms2-aws-to-azure-001",
  "microservice": "ms2",
  "source": "aws",
  "destination": "azure",
  "status": "IN_PROGRESS",
  "state": "migrating_data",
  "error": null
}
```

| Campo | Significado |
|-------|-------------|
| `status` | `PENDING` (aceita) → `IN_PROGRESS` → `COMPLETED` ou `FAILED` |
| `state` | Etapa detalhada da máquina de estados (`quiescing_source`, `migrating_data`, `redirecting_traffic`, ...) |
| `error` | Motivo da falha; `null` enquanto não falhou. Em `FAILED` a estratégia já religou a origem, restaurou a rota e removeu o destino incompleto |

**Body:** o mesmo JSON dos arquivos em `configs/` (`microservice`, `source`,
`data`, `source_workload`, `ingress`). O provedor de destino pode ser enviado
como `destination` ou `target`. `migration_id` é opcional: sem ele, o serviço
gera um UUID. Faltando qualquer campo obrigatório, a resposta é `422` e nada
é iniciado. `source` e `destination` na resposta são o nome do provedor; as
credenciais nunca são devolvidas.

**Códigos:** `202` aceita · `404` migração inexistente · `409`
`migration_id` já usado ou já existe migração em andamento para o mesmo
microsserviço · `422` body inválido ou provedor não suportado.

**Independência:** microsserviços diferentes migram em paralelo, cada um em
sua própria thread e com seu próprio banco e rota no Gateway. O mesmo
microsserviço não migra duas vezes ao mesmo tempo (`409`); terminada a
migração, ele pode ser migrado de novo (por exemplo, no sentido inverso).

Depois de `COMPLETED` a origem continua parada e retida. Rollback manual e
limpeza definitiva seguem em `POST /migrations/{id}/rollback` e
`POST /migrations/{id}/finalize`, com o mesmo `migrationId`.

> O estado das migrações fica em memória: reiniciar o processo durante uma
> migração perde o acompanhamento (a persistência está nos próximos passos).

### Fluxo via API

Para iniciar uma migração com indisponibilidade controlada em uma chamada,
envie o JSON completo para `POST /migrations/stop-and-migrate`. A resposta é
`202 Accepted`; acompanhe os eventos em `GET /migrations/{migration_id}`.

```bash
curl -X POST http://localhost:8000/migrations/stop-and-migrate \
  -H "Content-Type: application/json" \
  -d @configs/migration-aws-to-azure.json
```

O body exige `source.endpoint`, `source.credentials`, `target.endpoint`,
`target.credentials`, e `username`/`password` em `data.source` e
`data.target`. Não registre esse JSON em logs, histórico de shell ou controle
de versão quando ele contiver credenciais reais.

```bash
# 1. Cria e prepara a migração (recebe o MigrationRequest completo)
curl -X POST http://localhost:8000/migrations \
  -H "Content-Type: application/json" \
  -d @configs/migration.json

# 2. Executa a migração (delega para a MigrationStrategy configurada)
curl -X POST http://localhost:8000/migrations/migration-ms-b-001/execute

# 3. Consulta estado e eventos registrados
curl http://localhost:8000/migrations/migration-ms-b-001

# 4. Valida o estado atual (serviço + dados)
curl -X POST http://localhost:8000/migrations/migration-ms-b-001/validate

# 5. Em caso de falha, executa rollback
curl -X POST http://localhost:8000/migrations/migration-ms-b-001/rollback
```

### Migrando entre AWS e Azure (nos dois sentidos)

```bash
# AWS -> Azure
curl -X POST http://localhost:8000/migrations \
  -H "Content-Type: application/json" \
  -d @configs/migration-aws-to-azure.json
curl -X POST http://localhost:8000/migrations/migration-ms2-aws-to-azure-001/execute

# Azure -> AWS (mesmo endpoint, mesmo fluxo - só o JSON muda)
curl -X POST http://localhost:8000/migrations \
  -H "Content-Type: application/json" \
  -d @configs/migration-azure-to-aws.json
curl -X POST http://localhost:8000/migrations/migration-ms2-azure-to-aws-001/execute
```

### Docker

O `docker-compose.yml` deste projeto sobe **só** o `migration-manager`.
Nenhuma infraestrutura de origem ou de destino é criada aqui — nem Floci
AWS, nem Gateway, nem Floci-AZ ou qualquer outro emulador/provedor: tudo
isso é responsabilidade de outros arquivos, projetos ou pipelines, e já
precisa estar no ar antes da primeira chamada (passo 0 acima).

```bash
# 1. Suba a origem (por exemplo, o projeto microservices-demo)
cd ../microservices-demo && ./start.sh

# 2. Suba o destino ANTES do manager (ex.: o projeto floci-az)
cd ../floci-az && ./start.sh

# 3. Neste projeto, só o manager
cd ../cloud-flow && docker compose up --build
```

O `migration-manager` alcança essa infraestrutura pelos endpoints enviados
em cada requisição (`source.endpoint`, `target.endpoint`,
`ingress.gateway_admin_url`), nunca por configuração fixa no compose. Se a
origem publica portas no host (como faz o `start.sh` do microservices-demo
com o Floci AWS), `host.docker.internal` — já mapeado no serviço — é
suficiente para alcançá-la; sendo `source`/`target` genéricos, o mesmo
manager atende `aws -> azure`, `azure -> aws` ou qualquer outro par sem
alteração.

**Exceção — banco de dados do destino:** nem todo provedor publica a porta
do banco no host. O Floci AWS publica (por isso o RDS de origem funciona
por `host.docker.internal`); o Floci-AZ não — o Postgres que ele cria só é
alcançável pela própria rede Docker dele. Por isso o compose também conecta
o `migration-manager` a uma rede **externa** do destino:

```yaml
networks:
  destination-network:
    external: true
    name: ${DESTINATION_NETWORK:-floci-az}
```

`external: true` só faz o `migration-manager` **entrar** numa rede que já
existe — não cria nem gerencia essa infraestrutura, e o `docker compose up`
falha se a rede ainda não existir (daí a ordem dos passos 1-3 acima: o
destino precisa estar de pé primeiro). O padrão é a rede `floci-az`, criada
pelo gerador do mesmo nome; se o seu destino usa outra rede, aponte para
ela sem editar o compose:

```bash
DESTINATION_NETWORK=nome-da-outra-rede docker compose up --build
```

Os exemplos em `configs/` já usam `host.docker.internal` nesse formato — é
o que muda para `localhost` se você rodar o manager via `uvicorn` direto no
host, fora do Docker (veja os avisos no passo 1 acima; nesse modo, sem
Docker, não há como entrar na rede do destino).

## Testes

```bash
pytest -q
```

Cobertura atual (23 testes):

| Categoria                       | O que valida |
|----------------------------------|---------------|
| `test_migration_request.py`      | `MigrationRequest` válida é aceita; provider ausente, imagem ausente, porta inválida, target ausente e dados inválidos são rejeitados |
| `test_dependency_graph.py`       | `get_dependencies`, `get_dependents` e `validate_dependencies` do `DependencyGraph` |
| `test_dependency_manager.py`     | Construção do grafo a partir de um `MicroserviceConfig` |
| `tests/api/test_migrate_api.py`  | `POST /migrate/stop-and-migrate` responde 202 na hora e migra em segundo plano; status até `COMPLETED`/`FAILED`; falha desfeita; microsserviços diferentes em paralelo e o mesmo em série (409); body incompleto (422) |
| `test_migration_state_machine.py`| Transições válidas/inválidas de `MigrationState`, incluindo fluxo de falha e rollback |
| `test_migration_manager.py`      | Ordem de execução das etapas com providers mockados; rollback após falha simulada durante `REDIRECTING_TRAFFIC` |
| `test_provider_factory.py`       | `ProviderFactory` resolve as implementações corretas por tipo de provedor (AWS **e Azure**) |
| `test_cross_cloud_migration.py`  | Migração completa fim a fim com os providers **reais** (não mockados) nas duas direções: `aws -> azure` e `azure -> aws`; rollback também na direção `azure -> aws` |

## Estado atual da implementação AWS/Azure (Floci)

As implementações em `app/infrastructure/providers/aws/` e
`app/infrastructure/providers/azure/` são **iniciais e propositalmente
simplificadas** (stubs/mocks), conforme orientado no plano original:
priorizam contratos, modelos, orquestração, estados e estratégia antes da
integração real com infraestrutura. Nenhuma dessas classes é conhecida pelo
domínio — apenas pela `ProviderFactory`. A implementação Azure segue
exatamente a mesma estrutura da AWS (mesmo conjunto de 5 classes por
provedor), o que tornou o suporte bidirecional (`aws -> azure` e
`azure -> aws`) uma questão de registro na factory, sem tocar em
`MigrationManager`, `MigrationContext`, `MigrationStateMachine` ou
`ContinuousMigrationStrategy`.

Próximos passos sugeridos:

- Substituir `DockerComputeProvider`/`AzureContainerComputeProvider` por
  integrações reais com Docker/ECS e Azure Container Instances/AKS;
- Implementar `PostgreSQLDataMigrationProvider` com transferência de dados
  real (hoje qualquer engine relacional cai no mesmo stub genérico);
- Adicionar `GCPProvider`, registrando-o na `ProviderFactory` (mesmo padrão
  usado para Azure);
- Persistir `MigrationContext`/eventos (hoje em memória) em
  `app/infrastructure/persistence/`;
- Adicionar métricas de tempo total, downtime e duração por etapa a partir
  dos eventos já registrados por `MigrationExecutor`.
