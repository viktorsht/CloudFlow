# microservices-demo

Sistema de exemplo com **API Gateway + 3 microsserviços** em Spring Boot,
executado localmente como uma task ECS e três instâncias RDS emuladas pelo
[Floci](https://github.com/floci-io/floci).

## Arquitetura

```
                        ┌─────────────┐
   cliente  ───────────▶│ API Gateway │◀──────────────────┐  (porta 8080)
                        └──────┬──────┘                    │
              ┌────────────────┼────────────────┐          │ /ms3/**
              ▼                ▼                 ▼          │
        /ms1/**           /ms2/**            /ms3/**        │
              │                │                 │          │
        ┌─────▼─────┐   ┌──────▼──────┐   ┌──────▼─────┐    │
        │    MS1    │   │     MS2     │   │    MS3     │    │
        │  simples  │   │  composto   │────────────────┼────┘
        │  :8081    │   │   :8082     │  (via gateway)  │
        └─────┬─────┘   └──────┬──────┘   └──────┬─────┘
              │                │                  │
        ┌─────▼─────┐   ┌──────▼──────┐   ┌──────▼─────┐
        │ RDS/Floci │   │ RDS/Floci  │   │ RDS/Floci  │
        │  ms1_db   │   │   ms2_db   │   │   ms3_db   │
        │  :7001+   │   │   :7001+   │   │   :7001+   │
        └───────────┘   └─────────────┘   └────────────┘
```

- **API Gateway** (Spring Cloud Gateway): expõe `/ms1/**`, `/ms2/**`,
  `/ms3/**` e roteia (com `StripPrefix=1`) para o serviço correspondente.
- **MS1 e MS3** (tipo *simples*): recebem a requisição, calculam um hash
  SHA-256 e salvam/retornam o registro.
- **MS2** (tipo *composto*): recebe a requisição, calcula seu próprio hash,
  chama o **MS3 através do API Gateway** (rota `/ms3/**`, não direto no
  container do MS3), concatena o hash próprio com o hash recebido de MS3,
  salva e retorna o registro.
- **Database per service**: cada microsserviço tem sua **própria** instância
  PostgreSQL emulada pelo RDS do Floci, com database, usuário e senha
  exclusivos — nenhum MS acessa o banco de outro.

> **Por que via gateway em vez de host:porta fixos?** MS2 não precisa saber
> onde/como o MS3 está hospedado. Se o MS3 for trocado de provedor de nuvem,
> reescrito em outra stack, escalado horizontalmente, ou se você adicionar
> um MS4/MS5 no lugar dele, basta ajustar a rota no gateway — nenhum código
> ou config do MS2 muda. Isso também centraliza no gateway pontos futuros
> como retry, circuit breaker, rate limiting e logging de todas as chamadas
> entre serviços.

### Registro salvo por requisição

Cada microsserviço que processa uma requisição grava uma linha na tabela
`request_log`:

| Campo         | Descrição                                          |
|---------------|-----------------------------------------------------|
| `ip_request`  | IP de quem enviou a requisição (via `X-Forwarded-For`) |
| `provider`    | Provedor de nuvem onde o serviço está rodando (`local`, `aws`, `azure`, `gcp`...) |
| `ms_name`     | Nome do microsserviço (`ms1`, `ms2`, `ms3`)         |
| `hash`        | Hash SHA-256 calculado (MS2 concatena o seu com o de MS3) |
| `created_at`  | Data/hora de criação                                |

> A variável `APP_PROVIDER` identifica o ambiente em que o serviço está
> rodando. No fluxo Floci ela recebe `aws`; no fluxo local alternativo, recebe
> `local`.

## Como rodar no Floci

Pré-requisitos: Docker com o daemon em execução, Docker Compose, AWS CLI e
`curl`. O primeiro build das imagens precisa acessar a internet para baixar
as dependências Maven.

```bash
cd microservices-demo
./init-aws.sh
```

O script inicia o Floci, cria o cluster ECS e os três bancos RDS, constrói as
imagens e inicia uma única task ECS com `gateway`, `ms1`, `ms2` e `ms3`.
No Docker Desktop, a task usa a rede ECS `bridge`: as portas `8080` a `8083`
são publicadas no host e os containers se comunicam por
`host.docker.internal`. Os três serviços também acessam os proxies RDS por
esse endereço.

As variáveis abaixo são opcionais caso o endpoint, a região ou as credenciais
locais do Floci precisem ser diferentes:

```bash
AWS_ENDPOINT_URL=http://localhost:4566 \
AWS_DEFAULT_REGION=us-east-1 \
AWS_ACCESS_KEY_ID=test \
AWS_SECRET_ACCESS_KEY=test \
./init-aws.sh
```

## Testando

Todas as chamadas abaixo passam pelo **gateway** (porta 8080):

```bash
# MS1 (simples)
curl -X POST http://localhost:8080/ms1/api/process

# MS3 (simples)
curl -X POST http://localhost:8080/ms3/api/process

# MS2 (composto -> chama MS3 internamente)
curl -X POST http://localhost:8080/ms2/api/process
```

Resposta esperada (exemplo, `ms2`):

```json
{
  "ip_request": "172.19.0.1",
  "provider": "aws",
  "ms_name": "ms2",
  "hash": "a1b2c3...<hash proprio><hash do ms3>...f9e8d7",
  "created_at": "2026-09-07T14:32:10.123"
}
```

Repare que o `hash` do `ms2` é mais longo que o de `ms1`/`ms3`: é a
concatenação do hash calculado pelo próprio MS2 com o hash retornado pelo
MS3.

### Diagnóstico

```bash
# Recursos ECS e RDS criados no Floci
aws ecs list-tasks --cluster microservices-demo --endpoint-url http://localhost:4566
aws rds describe-db-instances --endpoint-url http://localhost:4566

# Containers da task e dos RDS emulados
docker ps --filter label=floci=true
docker logs <container>
```

O script consulta as portas atribuídas aos proxies RDS e as injeta na task;
elas não precisam ser configuradas manualmente nos microsserviços.

## Execução local alternativa, sem ECS/RDS emulado

Para executar diretamente os containers da aplicação e bancos PostgreSQL
convencionais, use o compose alternativo:

```bash
docker compose -f docker-compose-containers.yml up --build
```

Nesse modo, o gateway continua na porta `8080`; MS1, MS2 e MS3 também ficam
disponíveis diretamente nas portas `8081`, `8082` e `8083`, e os bancos nas
portas `5432`, `5433` e `5434`.

## Estrutura do projeto

```
microservices-demo/
├── pom.xml                  # POM agregador (reactor build)
├── docker-compose.yml        # Floci, ECS e RDS
├── init-aws.sh               # provisiona e inicia a task ECS no Floci
├── docker-compose-containers.yml # alternativa local sem Floci
├── ms1-simple/               # MS simples
├── ms3-simple/               # MS simples (chamado por ms2)
├── ms2-composite/             # MS composto (chama ms3 pelo gateway)
└── api-gateway/               # Spring Cloud Gateway
```

Cada microsserviço é um módulo Maven **independente** (parent =
`spring-boot-starter-parent`, sem lib compartilhada entre eles), propositalmente,
para simular serviços que podem inclusive evoluir/ser reescritos de forma
desacoplada — o que também facilita migrá-los individualmente entre
provedores de nuvem mais adiante.

## Build local sem Docker (opcional)

```bash
# a partir da raiz, compila todos os módulos
mvn clean package

# roda cada um em terminais separados
java -jar ms1-simple/target/ms1-simple-1.0.0.jar
java -jar ms3-simple/target/ms3-simple-1.0.0.jar
java -jar ms2-composite/target/ms2-composite-1.0.0.jar
java -jar api-gateway/target/api-gateway-1.0.0.jar
```

Nesse caso, é preciso ter um Postgres local com **três bancos separados**
(ou ajustar as env vars `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD` de cada serviço para apontar aonde quiser):

| Serviço | Banco     | Usuário    | Senha     | Porta padrão |
|---------|-----------|------------|-----------|--------------|
| ms1     | `ms1_db`  | `ms1_user` | `ms1_pass`| 5432         |
| ms2     | `ms2_db`  | `ms2_user` | `ms2_pass`| 5432         |
| ms3     | `ms3_db`  | `ms3_user` | `ms3_pass`| 5432         |
