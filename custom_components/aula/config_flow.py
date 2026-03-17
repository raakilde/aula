import asyncio
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.http import HomeAssistantView

from .const import (
    DOMAIN,
    CONF_AUTH_METHOD,
    CONF_MITID_USERNAME,
    CONF_MITID_PASSWORD,
    CONF_MITID_TOKEN,
    CONF_MITID_USE_TOKEN,
    CONF_MITID_IDENTITY,
    CONF_SCHOOLSCHEDULE,
    CONF_UGEPLAN,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    AUTH_METHOD_APP,
    AUTH_METHOD_TOKEN,
)

_LOGGER = logging.getLogger(__name__)

# Schema for MitID authentication
AUTH_METHOD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MITID_USERNAME): str,
        vol.Optional(CONF_MITID_USE_TOKEN, default=False): bool,
        vol.Optional(CONF_SCHOOLSCHEDULE, default=True): bool,
        vol.Optional(CONF_UGEPLAN, default=True): bool,
    }
)

# Schema for TOKEN auth credentials
TOKEN_AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MITID_PASSWORD): str,
        vol.Required(CONF_MITID_TOKEN): str,
    }
)


class AulaCustomConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Aula config flow with MitID authentication.

    Supports APP (push to phone) and TOKEN (password + hardware token) methods.
    """

    VERSION = 3

    def __init__(self):
        """Initialize config flow."""
        super().__init__()
        self._mitid_username = None
        self._mitid_password = None
        self._mitid_token = None
        self._use_token = False
        self._schoolschedule = True
        self._ugeplan = True
        self._auth_client = None
        self._auth_task = None
        self._auth_result = None
        self._auth_error = None
        self._available_identities = None
        self._selected_identity = None

    async def async_step_user(self, user_input=None):
        """MitID authentication - enter username and options."""
        errors = {}

        if user_input is not None:
            self._mitid_username = user_input[CONF_MITID_USERNAME]
            self._use_token = user_input.get(CONF_MITID_USE_TOKEN, False)
            self._schoolschedule = user_input.get(CONF_SCHOOLSCHEDULE, True)
            self._ugeplan = user_input.get(CONF_UGEPLAN, True)

            if self._use_token:
                return await self.async_step_token_credentials()
            else:
                return await self.async_step_authenticate()

        return self.async_show_form(
            step_id="user",
            data_schema=AUTH_METHOD_SCHEMA,
            errors=errors,
        )

    async def async_step_token_credentials(self, user_input=None):
        """Collect TOKEN auth credentials (password + token code)."""
        errors = {}

        if user_input is not None:
            self._mitid_password = user_input[CONF_MITID_PASSWORD]
            self._mitid_token = user_input[CONF_MITID_TOKEN]
            return await self.async_step_authenticate()

        return self.async_show_form(
            step_id="token_credentials",
            data_schema=TOKEN_AUTH_SCHEMA,
            errors=errors,
        )

    async def async_step_authenticate(self, user_input=None):
        """Start MitID authentication.

        For APP auth: sends push notification to user's phone.
        For TOKEN auth: uses password + token code.
        """
        auth_method = AUTH_METHOD_TOKEN if self._use_token else AUTH_METHOD_APP

        # Register the auth status view if not already registered
        try:
            self.hass.http.register_view(
                AulaAuthStatusView(self.hass)
            )
        except Exception:
            pass  # View might already be registered

        # Store flow_id for external access
        self.hass.data.setdefault(DOMAIN, {})
        self.hass.data[DOMAIN][f"auth_flow_{self.flow_id}"] = self

        # Start background authentication
        self._auth_task = self.hass.async_create_task(
            self._authenticate_async(auth_method)
        )

        return self.async_external_step(
            step_id="authenticate",
            url=f"/api/aula/auth/{self.flow_id}",
        )

    async def _authenticate_async(self, auth_method: str):
        """Run MitID authentication in background."""
        try:
            from .aula_auth import AulaAuthenticator

            self._auth_client = AulaAuthenticator(
                username=self._mitid_username,
                password=self._mitid_password,
                token_code=self._mitid_token,
                method=auth_method,
            )

            # Set up identity selector callback
            identity_future = asyncio.get_event_loop().create_future()
            self._identity_future = identity_future

            def identity_selector(identities):
                """Called when multiple identities are available."""
                self._available_identities = identities
                # Wait for user selection
                loop = asyncio.get_event_loop()
                future = asyncio.run_coroutine_threadsafe(
                    self._wait_for_identity_selection(),
                    loop,
                )
                return future.result(timeout=120)

            self._auth_client._identity_chooser = identity_selector

            # Run authentication in executor (blocking I/O)
            result = await self.hass.async_add_executor_job(
                self._auth_client.run
            )

            self._auth_result = result
            _LOGGER.info("MitID authentication completed successfully")

        except Exception as e:
            _LOGGER.error(f"MitID authentication failed: {e}")
            self._auth_error = str(e)

        # Signal external step completion
        self.hass.async_create_task(
            self.hass.config_entries.flow.async_configure(
                flow_id=self.flow_id
            )
        )

    async def _wait_for_identity_selection(self):
        """Wait for user to select an identity (called from auth thread)."""
        while self._selected_identity is None:
            await asyncio.sleep(0.5)
        return self._selected_identity

    async def async_step_authenticate_complete(self, user_input=None):
        """Called when external step completes (redirect from auth view)."""
        # Clean up flow data
        self.hass.data.get(DOMAIN, {}).pop(f"auth_flow_{self.flow_id}", None)

        if self._auth_error:
            return self.async_abort(reason="auth_failed")

        if not self._auth_result or not self._auth_result.get("access_token"):
            return self.async_abort(reason="auth_failed")

        tokens = self._auth_result

        data = {
            CONF_AUTH_METHOD: AUTH_METHOD_TOKEN if self._use_token else AUTH_METHOD_APP,
            CONF_MITID_USERNAME: self._mitid_username,
            CONF_SCHOOLSCHEDULE: self._schoolschedule,
            CONF_UGEPLAN: self._ugeplan,
            CONF_ACCESS_TOKEN: tokens.get("access_token"),
            CONF_REFRESH_TOKEN: tokens.get("refresh_token"),
            CONF_TOKEN_EXPIRES_AT: tokens.get("expires_at", 0),
        }

        if self._use_token:
            data[CONF_MITID_PASSWORD] = self._mitid_password
            data[CONF_MITID_TOKEN] = self._mitid_token

        if self._selected_identity:
            data[CONF_MITID_IDENTITY] = self._selected_identity

        return self.async_create_entry(title="Aula", data=data)

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return options flow handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Aula options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        super().__init__()
        self._config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle options - re-authenticate."""
        return await self.async_step_reauth(user_input)

    async def async_step_reauth(self, user_input=None):
        """Re-authenticate with MitID."""
        if user_input is not None:
            # Trigger re-authentication via config flow
            return self.async_create_entry(title="", data={"reauth": True})

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema({}),
            description_placeholders={
                "username": self._config_entry.data.get(CONF_MITID_USERNAME, ""),
            },
        )


class AulaAuthStatusView(HomeAssistantView):
    """View to check MitID authentication status and display QR codes."""

    url = "/api/aula/auth/{flow_id}"
    name = "api:aula:auth"
    requires_auth = False

    def __init__(self, hass):
        """Initialize the auth status view."""
        self._hass = hass

    async def get(self, request, flow_id):
        """Handle GET request - show auth status page."""
        from aiohttp import web

        flow = self._hass.data.get(DOMAIN, {}).get(f"auth_flow_{flow_id}")

        if not flow:
            return web.Response(
                text="<html><body><h2>Authentication session not found.</h2></body></html>",
                content_type="text/html",
            )

        # Check if auth completed
        if flow._auth_result or flow._auth_error:
            redirect_url = f"/api/aula/auth/{flow_id}/complete"
            return web.Response(
                text=f"""<html><head>
                    <meta http-equiv="refresh" content="0;url={redirect_url}">
                </head><body>
                    <h2>Authentication complete! Redirecting...</h2>
                </body></html>""",
                content_type="text/html",
            )

        # Check for identity selection needed
        if flow._available_identities:
            identities_html = ""
            for i, name in enumerate(flow._available_identities):
                identities_html += f'<button onclick="selectIdentity({i+1})">{name}</button><br>'

            return web.Response(
                text=f"""<html><body>
                    <h2>Select Identity</h2>
                    {identities_html}
                    <script>
                    function selectIdentity(id) {{
                        fetch('/api/aula/auth/{flow_id}/identity?id=' + id)
                            .then(() => location.reload());
                    }}
                    </script>
                </body></html>""",
                content_type="text/html",
            )

        # Show waiting page with QR codes if available
        qr_html = ""
        if flow._auth_client:
            try:
                qr_svgs = flow._auth_client.qr_svg_pair()
                if qr_svgs:
                    qr_html = f"""
                    <div style="display:flex; gap:20px; justify-content:center;">
                        <div>{qr_svgs[0]}</div>
                        <div>{qr_svgs[1]}</div>
                    </div>
                    <p>Scan a QR code with MitID app, or approve the push notification on your phone.</p>
                    """
            except Exception:
                pass

        return web.Response(
            text=f"""<html><head>
                <meta http-equiv="refresh" content="3">
                <style>
                    body {{ font-family: sans-serif; text-align: center; padding: 40px; }}
                    .spinner {{ animation: spin 1s linear infinite; display: inline-block; }}
                    @keyframes spin {{ from {{ transform: rotate(0deg) }} to {{ transform: rotate(360deg) }} }}
                </style>
            </head><body>
                <h2>Waiting for MitID authentication...</h2>
                <div class="spinner">⏳</div>
                <p>Please approve the login request in your MitID app.</p>
                {qr_html}
            </body></html>""",
            content_type="text/html",
        )
