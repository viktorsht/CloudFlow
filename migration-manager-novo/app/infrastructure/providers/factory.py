"""ProviderFactory: unica camada responsavel por resolver as implementacoes
concretas de CloudProvider/DataMigrationProvider/TrafficProvider/ValidationProvider
a partir de uma configuracao de provedor.

Esta e a UNICA classe que deve conhecer o mapeamento entre o tipo de
provedor/tecnologia e sua implementacao concreta - evitando `if provider ==`
espalhado pelo restante do sistema.
"""
from __future__ import annotations

from app.domain.contracts.cloud_provider import CloudProvider
from app.domain.contracts.data_migration_provider import DataMigrationProvider
from app.domain.contracts.traffic_provider import TrafficProvider
from app.domain.contracts.validation_provider import ValidationProvider
from app.domain.enums.provider_type import CloudProviderType, DataEngineType
from app.domain.models.provider import ProviderConfig


class UnsupportedProviderError(Exception):
    """Levantada quando nao existe implementacao registrada para o provedor."""


class ProviderFactory:
    """Fabrica responsavel por criar as implementacoes concretas dos
    contratos de dominio a partir de configuracao externa.

    Novos provedores (Azure, GCP, ...) sao adicionados registrando novas
    entradas nos mapas internos, sem exigir alteracoes no MigrationManager
    ou nas estrategias de migracao.
    """

    def __init__(self) -> None:
        self._cloud_provider_builders: dict[CloudProviderType, type[CloudProvider]] = {}
        self._traffic_provider_builders: dict[CloudProviderType, type[TrafficProvider]] = {}
        self._validation_provider_builders: dict[CloudProviderType, type[ValidationProvider]] = {}
        self._data_provider_builders: dict[DataEngineType, type[DataMigrationProvider]] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        # Import local para evitar import ciclico entre infrastructure e a
        # propria factory, e para manter o dominio livre de dependencias
        # concretas em tempo de import do modulo.
        from app.infrastructure.providers.aws.aws_compute import DockerComputeProvider
        from app.infrastructure.providers.aws.aws_data import AWSRDSDataMigrationProvider
        from app.infrastructure.providers.aws.aws_provider import AWSProvider
        from app.infrastructure.providers.aws.aws_traffic import AWSTrafficProvider
        from app.infrastructure.providers.aws.aws_validation import AWSValidationProvider
        from app.infrastructure.providers.azure.azure_compute import (
            AzureContainerComputeProvider,
        )
        from app.infrastructure.providers.azure.azure_data import (
            AzureDatabaseDataMigrationProvider,
        )
        from app.infrastructure.providers.azure.azure_provider import AzureProvider
        from app.infrastructure.providers.azure.azure_traffic import AzureTrafficProvider
        from app.infrastructure.providers.azure.azure_validation import AzureValidationProvider

        def build_aws_cloud_provider(config: ProviderConfig) -> CloudProvider:
            return AWSProvider(config=config, compute_provider=DockerComputeProvider(config=config))

        def build_azure_cloud_provider(config: ProviderConfig) -> CloudProvider:
            return AzureProvider(
                config=config, compute_provider=AzureContainerComputeProvider(config=config)
            )

        self.register_cloud_provider(CloudProviderType.AWS, build_aws_cloud_provider)
        self.register_traffic_provider(CloudProviderType.AWS, lambda config: AWSTrafficProvider(config=config))
        self.register_validation_provider(
            CloudProviderType.AWS, lambda config: AWSValidationProvider(config=config)
        )
        self.register_data_provider(
            DataEngineType.AWS_RDS, lambda: AWSRDSDataMigrationProvider()
        )

        self.register_cloud_provider(CloudProviderType.AZURE, build_azure_cloud_provider)
        self.register_traffic_provider(
            CloudProviderType.AZURE, lambda config: AzureTrafficProvider(config=config)
        )
        self.register_validation_provider(
            CloudProviderType.AZURE, lambda config: AzureValidationProvider(config=config)
        )
        self.register_data_provider(
            DataEngineType.AZURE_DATABASE, lambda: AzureDatabaseDataMigrationProvider()
        )

        # Motores relacionais genericos tambem resolvidos, por ora, pela
        # implementacao AWS RDS de exemplo (stub), ate que provedores
        # dedicados (PostgreSQLDataMigrationProvider, etc.) sejam adicionados.
        # Nota: DataMigrationProvider e resolvido pelo tipo de ENGINE de
        # dados (postgresql/mysql), nao pelo cloud provider de origem/destino
        # - por isso o mesmo stub generico serve para qualquer combinacao de
        # nuvens (AWS -> Azure, Azure -> AWS, etc). Use DataEngineType.AWS_RDS
        # ou AZURE_DATABASE explicitamente se quiser um stub nomeado por nuvem.
        self.register_data_provider(
            DataEngineType.POSTGRESQL, lambda: AWSRDSDataMigrationProvider()
        )
        self.register_data_provider(
            DataEngineType.MYSQL, lambda: AWSRDSDataMigrationProvider()
        )

    # -- registro (permite extensao/testes sem modificar esta classe) -----

    def register_cloud_provider(self, provider_type: CloudProviderType, builder) -> None:
        self._cloud_provider_builders[provider_type] = builder

    def register_traffic_provider(self, provider_type: CloudProviderType, builder) -> None:
        self._traffic_provider_builders[provider_type] = builder

    def register_validation_provider(self, provider_type: CloudProviderType, builder) -> None:
        self._validation_provider_builders[provider_type] = builder

    def register_data_provider(self, engine_type: DataEngineType, builder) -> None:
        self._data_provider_builders[engine_type] = builder

    # -- resolucao ----------------------------------------------------------

    def create_cloud_provider(self, config: ProviderConfig) -> CloudProvider:
        builder = self._cloud_provider_builders.get(config.provider)
        if builder is None:
            raise UnsupportedProviderError(f"Cloud provider nao suportado: {config.provider}")
        return builder(config)

    def create_traffic_provider(self, config: ProviderConfig) -> TrafficProvider:
        builder = self._traffic_provider_builders.get(config.provider)
        if builder is None:
            raise UnsupportedProviderError(f"Traffic provider nao suportado: {config.provider}")
        return builder(config)

    def create_validation_provider(self, config: ProviderConfig) -> ValidationProvider:
        builder = self._validation_provider_builders.get(config.provider)
        if builder is None:
            raise UnsupportedProviderError(f"Validation provider nao suportado: {config.provider}")
        return builder(config)

    def create_data_provider(self, engine_type: DataEngineType) -> DataMigrationProvider:
        builder = self._data_provider_builders.get(engine_type)
        if builder is None:
            raise UnsupportedProviderError(f"Data engine nao suportado: {engine_type}")
        return builder()
