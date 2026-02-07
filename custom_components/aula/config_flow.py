import logging
from typing import Any, Dict, Optional

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.entity_registry import (
    async_entries_for_config_entry,
    async_get,
)

from .const import (
    CONF_AUTH_COOKIES,
    CONF_BIBLIOTEK,
    CONF_MINUDANNELSEFORLOEB,
    CONF_MINUDANNELSEOPGAVELISTE,
    CONF_MINUDANNELSEUGENOTE,
    CONF_SCHOOLSCHEDULE,
    CONF_UGEPLAN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

AUTH_SCHEMA = vol.Schema(
    {
        vol.Optional("schoolschedule"): cv.boolean,
        vol.Optional("ugeplan"): cv.boolean,
        vol.Optional("bibliotek"): cv.boolean,
        vol.Optional("minuddannelseforloeb"): cv.boolean,
        vol.Optional("minuddannelseopgaveliste"): cv.boolean,
        vol.Optional("minuddannelseugenote"): cv.boolean,
    }
)


class AulaCustomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Aula Custom config flow."""

    data: Optional[Dict[str, Any]]

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Invoked when a user initiates a flow via the user interface."""
        errors: Dict[str, str] = {}
        if user_input is not None:
            self.data = user_input

            # No URL needed - MitID will handle domain detection automatically

            _LOGGER.debug(user_input.get("schoolschedule"))
            if user_input.get("schoolschedule") == None:
                self.data[CONF_SCHOOLSCHEDULE] = False
            else:
                self.data[CONF_SCHOOLSCHEDULE] = user_input.get("schoolschedule")
            _LOGGER.debug(user_input.get("ugeplan"))
            if user_input.get("ugeplan") == None:
                self.data[CONF_UGEPLAN] = False
            else:
                self.data[CONF_UGEPLAN] = user_input.get("ugeplan")
            _LOGGER.debug(user_input.get("bibliotek"))
            if user_input.get("bibliotek") == None:
                self.data[CONF_BIBLIOTEK] = False
            else:
                self.data[CONF_BIBLIOTEK] = user_input.get("bibliotek")

            _LOGGER.debug(user_input.get("minuddannelseforloeb"))
            if user_input.get("minuddannelseforloeb") == None:
                self.data[CONF_MINUDANNELSEFORLOEB] = False
            else:
                self.data[CONF_MINUDANNELSEFORLOEB] = user_input.get(
                    "minuddannelseforloeb"
                )

            _LOGGER.debug(user_input.get("minuddannelseopgaveliste"))
            if user_input.get("minuddannelseopgaveliste") == None:
                self.data[CONF_MINUDANNELSEOPGAVELISTE] = False
            else:
                self.data[CONF_MINUDANNELSEOPGAVELISTE] = user_input.get(
                    "minuddannelseopgaveliste"
                )

            _LOGGER.debug(user_input.get("minuddannelseugenote"))
            if user_input.get("minuddannelseugenote") == None:
                self.data[CONF_MINUDANNELSEUGENOTE] = False
            else:
                self.data[CONF_MINUDANNELSEUGENOTE] = user_input.get(
                    "minuddannelseugenote"
                )

            # Perform initial MitID authentication to get session cookies
            try:
                from .client import Client

                _LOGGER.info("Performing initial MitID authentication...")
                client = Client(
                    self.data[CONF_SCHOOLSCHEDULE],
                    self.data[CONF_UGEPLAN],
                    self.data[CONF_BIBLIOTEK],
                    self.data[CONF_MINUDANNELSEFORLOEB],
                    self.data[CONF_MINUDANNELSEOPGAVELISTE],
                    self.data[CONF_MINUDANNELSEUGENOTE],
                )

                # Show browser during initial setup for MitID authentication
                await self.hass.async_add_executor_job(client.login, True)

                # Store the authentication cookies
                self.data[CONF_AUTH_COOKIES] = client.get_session_cookies()
                _LOGGER.info(
                    f"Stored {len(self.data[CONF_AUTH_COOKIES])} authentication cookies"
                )

            except Exception as e:
                _LOGGER.error(f"MitID authentication failed: {e}")
                # Provide helpful error message based on the exception
                if "Browser required" in str(e):
                    errors["base"] = "browser_required"
                elif "ChromeDriver" in str(e):
                    errors["base"] = "chromedriver_failed"
                elif "display" in str(e).lower():
                    errors["base"] = "no_display"
                else:
                    errors["base"] = "auth_failed"
                return self.async_show_form(
                    step_id="user", data_schema=AUTH_SCHEMA, errors=errors
                )

            # Create entry title
            return self.async_create_entry(title="Aula MitID", data=self.data)

        return self.async_show_form(
            step_id="user", data_schema=AUTH_SCHEMA, errors=errors
        )


# reconfiguration (options flow), to be implemented
#    @staticmethod
#    @callback
#    def async_get_options_flow(config_entry):
#        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Blueprint config flow options handler."""

    def __init__(self, config_entry):
        """Initialize HACS options flow."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        _LOGGER.debug("Options......")
        _LOGGER.debug(self.config_entry)
        entity_registry = await async_get(self.hass)
        entries = async_entries_for_config_entry(
            entity_registry, self.config_entry.entry_id
        )
        repo_map = {e.entity_id: e for e in entries}
        for entity_id in repo_map.keys():
            # Unregister from HA
            _LOGGER.debug(entity_id)
            # entity_registry.async_remove(entity_id)
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self.options.update(user_input)
            return await self._update_options()

        return self.async_show_form(
            step_id="user",
            data_schema=AUTH_SCHEMA,
        )

    async def _update_options(self):
        """Update config entry options."""
        return self.async_create_entry(title="Aula", data=self.options)
