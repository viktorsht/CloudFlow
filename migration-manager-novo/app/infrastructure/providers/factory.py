"""Fabrica das integracoes reais usadas pelo orquestrador."""
from __future__ import annotations

from app.domain.contracts.cloud_provider import CloudProvider
from app.domain.contracts.data_migration_provider import DataMigrationProvider
from app.domain.contracts.database_provider import DatabaseProvider
from app.domain.contracts.traffic_provider import TrafficProvider
from app.domain.contracts.validation_provider import ValidationProvider
from app.domain.enums.provider_type import CloudProviderType, DataEngineType
from app.domain.models.migration import IngressConfig
from app.domain.models.provider import ProviderConfig


class UnsupportedProviderError(Exception):
    pass


class ProviderFactory:
    def create_cloud_provider(self, config: ProviderConfig) -> CloudProvider:
        if config.provider is CloudProviderType.AWS:
            from app.infrastructure.providers.aws.aws_compute import ECSComputeProvider
            from app.infrastructure.providers.aws.aws_provider import AWSProvider
            return AWSProvider(config, ECSComputeProvider(config))
        if config.provider is CloudProviderType.AZURE:
            from app.infrastructure.providers.azure.azure_compute import AzureContainerAppsComputeProvider
            from app.infrastructure.providers.azure.azure_provider import AzureProvider
            return AzureProvider(config, AzureContainerAppsComputeProvider(config))
        raise UnsupportedProviderError(f"Cloud provider nao suportado: {config.provider}")

    def create_database_provider(self, config: ProviderConfig) -> DatabaseProvider:
        if config.provider is CloudProviderType.AWS:
            from app.infrastructure.providers.aws.aws_database import AWSRDSDatabaseProvider
            return AWSRDSDatabaseProvider(config)
        if config.provider is CloudProviderType.AZURE:
            from app.infrastructure.providers.azure.azure_database import AzurePostgreSQLDatabaseProvider
            return AzurePostgreSQLDatabaseProvider(config)
        raise UnsupportedProviderError(f"Database provider nao suportado: {config.provider}")

    def create_traffic_provider(self, ingress: IngressConfig) -> TrafficProvider:
        from app.infrastructure.providers.gateway_traffic import GatewayTrafficProvider
        return GatewayTrafficProvider(ingress)

    def create_validation_provider(self, config: ProviderConfig) -> ValidationProvider:
        if config.provider is CloudProviderType.AWS:
            from app.infrastructure.providers.aws.aws_validation import AWSValidationProvider
            return AWSValidationProvider(config)
        if config.provider is CloudProviderType.AZURE:
            from app.infrastructure.providers.azure.azure_validation import AzureValidationProvider
            return AzureValidationProvider(config)
        raise UnsupportedProviderError(f"Validation provider nao suportado: {config.provider}")

    def create_data_provider(self, engine_type: DataEngineType) -> DataMigrationProvider:
        if engine_type is not DataEngineType.POSTGRESQL:
            raise UnsupportedProviderError("Esta entrega suporta apenas PostgreSQL")
        from app.infrastructure.providers.postgres_data import PostgreSQLDataMigrationProvider
        return PostgreSQLDataMigrationProvider()
