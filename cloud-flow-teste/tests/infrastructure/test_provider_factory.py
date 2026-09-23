"""Teste 6 - ProviderFactory cria o provider correto para cada tipo."""
from app.domain.enums.provider_type import CloudProviderType, DataEngineType
from app.domain.models.provider import ProviderConfig
from app.infrastructure.providers.aws.aws_provider import AWSProvider
from app.infrastructure.providers.aws.aws_traffic import AWSTrafficProvider
from app.infrastructure.providers.aws.aws_validation import AWSValidationProvider
from app.infrastructure.providers.azure.azure_provider import AzureProvider
from app.infrastructure.providers.azure.azure_traffic import AzureTrafficProvider
from app.infrastructure.providers.azure.azure_validation import AzureValidationProvider
from app.infrastructure.providers.factory import ProviderFactory, UnsupportedProviderError
import pytest


def _aws_config() -> ProviderConfig:
    return ProviderConfig(provider=CloudProviderType.AWS, region="us-east-1", environment="source")


def _azure_config() -> ProviderConfig:
    return ProviderConfig(provider=CloudProviderType.AZURE, region="eastus", environment="target")


def test_factory_creates_aws_cloud_provider():
    factory = ProviderFactory()
    provider = factory.create_cloud_provider(_aws_config())
    assert isinstance(provider, AWSProvider)


def test_factory_creates_aws_traffic_provider():
    factory = ProviderFactory()
    provider = factory.create_traffic_provider(_aws_config())
    assert isinstance(provider, AWSTrafficProvider)


def test_factory_creates_aws_validation_provider():
    factory = ProviderFactory()
    provider = factory.create_validation_provider(_aws_config())
    assert isinstance(provider, AWSValidationProvider)


def test_factory_creates_postgresql_data_provider():
    factory = ProviderFactory()
    provider = factory.create_data_provider(DataEngineType.POSTGRESQL)
    assert provider is not None


def test_factory_creates_azure_cloud_provider():
    factory = ProviderFactory()
    provider = factory.create_cloud_provider(_azure_config())
    assert isinstance(provider, AzureProvider)


def test_factory_creates_azure_traffic_provider():
    factory = ProviderFactory()
    provider = factory.create_traffic_provider(_azure_config())
    assert isinstance(provider, AzureTrafficProvider)


def test_factory_creates_azure_validation_provider():
    factory = ProviderFactory()
    provider = factory.create_validation_provider(_azure_config())
    assert isinstance(provider, AzureValidationProvider)


def test_factory_creates_azure_database_data_provider():
    factory = ProviderFactory()
    provider = factory.create_data_provider(DataEngineType.AZURE_DATABASE)
    assert provider is not None


def test_factory_raises_for_unregistered_cloud_provider():
    factory = ProviderFactory()
    factory._cloud_provider_builders.clear()
    with pytest.raises(UnsupportedProviderError):
        factory.create_cloud_provider(_aws_config())
