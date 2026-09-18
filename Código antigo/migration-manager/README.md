# MigrationManager

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
│   │   ├── providers/aws/         # Implementação inicial (stub) AWS/Floci
│   │   └── providers/factory.py   # ProviderFactory
│   └── main.py                    # App FastAPI
├── configs/migration.json         # Exemplo de MigrationRequest
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

## Executando localmente

```bash
pip install -r requirements.txt --break-system-packages   # ou em um venv
uvicorn app.main:app --reload
```

A API sobe em `http://localhost:8000` (docs interativas em `/docs`).

### Fluxo via API

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

### Docker

```bash
docker compose up --build
```

O `docker-compose.yml` inclui um serviço `floci` (usando a imagem do
LocalStack como emulador local de serviços AWS — substitua pela imagem real
do Floci utilizada no seu ambiente). O endpoint do emulador é configurado
via variável de ambiente `AWS_ENDPOINT_URL`, nunca hardcoded no domínio.

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
| `test_migration_state_machine.py`| Transições válidas/inválidas de `MigrationState`, incluindo fluxo de falha e rollback |
| `test_migration_manager.py`      | Ordem de execução das etapas com providers mockados; rollback após falha simulada durante `REDIRECTING_TRAFFIC` |
| `test_provider_factory.py`       | `ProviderFactory` resolve as implementações corretas por tipo de provedor |

## Estado atual da implementação AWS/Floci

A implementação em `app/infrastructure/providers/aws/` é **inicial e
propositalmente simplificada** (stubs/mocks), conforme orientado no plano:
prioriza contratos, modelos, orquestração, estados e estratégia antes da
integração real com infraestrutura. Nenhuma dessas classes é conhecida pelo
domínio — apenas pela `ProviderFactory`.

Próximos passos sugeridos:

- Substituir `DockerComputeProvider` por uma integração real com Docker/ECS;
- Implementar `PostgreSQLDataMigrationProvider` / `AWSRDSDataMigrationProvider`
  com transferência de dados real;
- Adicionar `AzureProvider` / `GCPProvider` registrando-os na `ProviderFactory`;
- Persistir `MigrationContext`/eventos (hoje em memória) em
  `app/infrastructure/persistence/`;
- Adicionar métricas de tempo total, downtime e duração por etapa a partir
  dos eventos já registrados por `MigrationExecutor`.
