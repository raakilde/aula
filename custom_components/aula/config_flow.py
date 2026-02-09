import json
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries

from .const import (
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Schema for initial setup with cookies and feature selection
SETUP_SCHEMA = vol.Schema(
    {
        vol.Required(
            "session_cookies", description="Enter your session cookies from browser"
        ): str,
        vol.Optional("schoolschedule", default=True): bool,
        vol.Optional("ugeplan", default=True): bool,
    }
)

# Schema for updating session cookies
COOKIE_UPDATE_SCHEMA = vol.Schema(
    {
        vol.Required(
            "session_cookies", description="Enter your fresh session cookies as JSON"
        ): str,
    }
)


class AulaCustomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Aula Custom config flow."""

    VERSION = 1

    def __init__(self):
        """Initialize config flow."""
        print("AULA CONFIG FLOW __INIT__ CALLED")
        _LOGGER.critical("AULA CONFIG FLOW __INIT__ CALLED")
        super().__init__()

    async def async_step_user(self, user_input=None):
        """Handle initial setup - collect session cookies from user."""

        print("AULA CONFIG FLOW async_step_user CALLED")
        _LOGGER.critical("AULA CONFIG FLOW async_step_user CALLED")

        if user_input is not None:
            try:
                # Parse the session cookies (support both JSON and browser cookie string format)
                cookies_input = user_input["session_cookies"].strip()

                if cookies_input.startswith("{"):
                    # JSON format: {"PHPSESSID": "abc123", "Csrfp-Token": "def456"}
                    auth_cookies = json.loads(cookies_input)
                else:
                    # Browser cookie string format: PHPSESSID=abc123; Csrfp-Token=def456
                    auth_cookies = {}
                    for cookie_pair in cookies_input.split(";"):
                        if "=" in cookie_pair:
                            key, value = cookie_pair.strip().split("=", 1)
                            auth_cookies[key.strip()] = value.strip()

                # Validate we have essential cookies
                if not auth_cookies.get("PHPSESSID") or not auth_cookies.get(
                    "Csrfp-Token"
                ):
                    raise ValueError(
                        "Missing essential cookies (PHPSESSID or Csrfp-Token)"
                    )

                # Include configuration for sensors to work
                data = {
                    "auth_cookies": auth_cookies,
                    "schoolschedule": user_input.get("schoolschedule", True),
                    "ugeplan": user_input.get("ugeplan", True),
                    "bibliotek": user_input.get("bibliotek", True),
                    "minUddannelseForloeb": user_input.get(
                        "minUddannelseForloeb", True
                    ),
                    "minUddannelseOpgaveListe": user_input.get(
                        "minUddannelseOpgaveListe", True
                    ),
                    "minUddannelseUgeNote": user_input.get(
                        "minUddannelseUgeNote", True
                    ),
                }

                return self.async_create_entry(title="Aula", data=data)

            except json.JSONDecodeError:
                errors = {"session_cookies": "invalid_json"}
            except ValueError as e:
                if "Missing essential cookies" in str(e):
                    errors = {"session_cookies": "missing_cookies"}
                else:
                    errors = {"base": "unknown"}
            except Exception as e:
                _LOGGER.error(f"Error processing cookies: {e}")
                errors = {"base": "unknown"}

            return self.async_show_form(
                step_id="user",
                data_schema=SETUP_SCHEMA,
                errors=errors,
            )

        # Show initial form
        return self.async_show_form(
            step_id="user",
            data_schema=SETUP_SCHEMA,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return options flow handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Aula options flow - allows updating cookies."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        super().__init__()
        self._config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle options step."""
        if user_input is not None:
            try:
                # Parse the new session cookies (support both JSON and browser cookie string format)
                cookies_input = user_input["session_cookies"].strip()

                if cookies_input.startswith("{"):
                    # JSON format: {"PHPSESSID": "abc123", "Csrfp-Token": "def456"}
                    new_cookies = json.loads(cookies_input)
                else:
                    # Browser cookie string format: PHPSESSID=abc123; Csrfp-Token=def456
                    new_cookies = {}
                    for cookie_pair in cookies_input.split(";"):
                        if "=" in cookie_pair:
                            key, value = cookie_pair.strip().split("=", 1)
                            new_cookies[key.strip()] = value.strip()

                # Validate we have essential cookies
                if not new_cookies.get("PHPSESSID") or not new_cookies.get(
                    "Csrfp-Token"
                ):
                    raise ValueError(
                        "Missing essential cookies (PHPSESSID or Csrfp-Token)"
                    )

                # Update the config entry with fresh cookies
                new_data = dict(self._config_entry.data)
                new_data["auth_cookies"] = new_cookies

                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data=new_data,
                )

                # Trigger a reload of the integration
                await self.hass.config_entries.async_reload(self._config_entry.entry_id)

                return self.async_create_entry(title="", data={})

            except json.JSONDecodeError:
                errors = {"session_cookies": "Invalid JSON format"}
                return self.async_show_form(
                    step_id="init",
                    data_schema=COOKIE_UPDATE_SCHEMA,
                    errors=errors,
                    description_placeholders={
                        "current_cookies": str(
                            self._config_entry.data.get("auth_cookies", {})
                        ),
                        "instructions": """**How to get fresh cookies:**

1. Open https://www.aula.dk in your browser
2. Login with MitID (complete authentication)
3. Open Developer Tools (F12) → Application → Cookies → https://www.aula.dk
4. Copy cookies as JSON format:
   {"PHPSESSID": "abc123", "Csrfp-Token": "def456", "profile_change": "13", "initialLogin": "true"}

**Note:** Tokens hentes automatisk via dine cookies - du behøver ikke finde dem manuelt!""",
                    },
                )
            except Exception as e:
                _LOGGER.error(f"Error updating cookies: {e}")
                errors = {"base": "update_failed"}
                return self.async_show_form(
                    step_id="init", data_schema=COOKIE_UPDATE_SCHEMA, errors=errors
                )

        return self.async_show_form(
            step_id="init",
            data_schema=COOKIE_UPDATE_SCHEMA,
            description_placeholders={
                "current_cookies": str(self._config_entry.data.get("auth_cookies", {})),
                "instructions": """**Update your expired session cookies:**

1. Open https://www.aula.dk in your browser
2. Login with MitID (complete authentication)
3. Open Developer Tools (F12) → Application → Cookies → https://www.aula.dk
4. Copy ALL cookies as JSON format:
   {"PHPSESSID": "abc123", "Csrfp-Token": "def456", "profile_change": "13", "initialLogin": "true"}

**Note:** API tokens hentes automatisk via dine cookies - du skal kun opdatere cookies!

**Current cookies:** {current_cookies}""",
            },
        )
