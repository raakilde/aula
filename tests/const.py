"""Constants for Aula tests."""

from homeassistant.const import CONF_USERNAME

MOCK_USERNAME = "test_user"

MOCK_CONFIG_DATA = {
    CONF_USERNAME: MOCK_USERNAME,
    "schoolschedule": True,
    "ugeplan": True,
    "bibliotek": False,
    "minuddannelseforloeb": False,
    "minuddannelseopgaveliste": False,
    "minuddannelseugenote": False,
}

MOCK_CHILD = {
    "id": 12345,
    "userId": 67890,
    "name": "Test Child",
    "institutionProfile": {"institutionName": "Test School"},
}

MOCK_PROFILE = {
    "children": [MOCK_CHILD],
    "institutionProfiles": [{"institutionCode": "123456"}],
}
