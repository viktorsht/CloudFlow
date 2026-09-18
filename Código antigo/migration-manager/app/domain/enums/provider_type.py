"""Enums relacionados a tipos de provedores e tecnologias suportadas."""
from enum import Enum


class CloudProviderType(str, Enum):
    """Provedores de nuvem suportados pela ProviderFactory."""

    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"


class ComputeKind(str, Enum):
    """Mecanismos de execucao de workloads (compute)."""

    DOCKER = "docker"
    ECS = "ecs"
    KUBERNETES = "kubernetes"
    VM = "vm"


class DataEngineType(str, Enum):
    """Tecnologias de armazenamento de dados suportadas para migracao."""

    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    AWS_RDS = "aws_rds"
    AZURE_DATABASE = "azure_database"


class HealthStatus(str, Enum):
    """Resultado de uma verificacao de saude de um deployment."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class OperationStatus(str, Enum):
    """Resultado generico de uma operacao registrada em um evento."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
