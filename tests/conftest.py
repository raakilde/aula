"""Pytest fixtures for Aula tests."""

import pytest
from unittest.mock import patch, MagicMock

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


@pytest.fixture
def mock_aula_client():
    """Create a mock Aula client."""
    with patch("custom_components.aula.client.Client") as mock_client:
        client_instance = MagicMock()
        mock_client.return_value = client_instance
        
        # Mock login method
        client_instance.login = MagicMock()
        
        # Mock update_data method
        client_instance.update_data = MagicMock()
        
        # Mock properties
        client_instance._children = []
        client_instance._childnames = {}
        client_instance._institutions = {}
        client_instance.unread_messages = 0
        client_instance.message = {}
        
        yield client_instance


