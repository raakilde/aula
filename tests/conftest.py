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


@pytest.fixture
def mock_selenium():
    """Mock Selenium webdriver for testing without actual browser."""
    with patch("custom_components.aula.client.webdriver") as mock_webdriver:
        mock_driver = MagicMock()
        mock_webdriver.Chrome.return_value = mock_driver
        
        # Mock driver methods
        mock_driver.get = MagicMock()
        mock_driver.find_element = MagicMock()
        mock_driver.get_cookies = MagicMock(return_value=[
            {"name": "session", "value": "test_session", "domain": "aula.dk"}
        ])
        mock_driver.quit = MagicMock()
        mock_driver.current_url = "https://www.aula.dk:443/portal/"
        
        yield mock_driver
