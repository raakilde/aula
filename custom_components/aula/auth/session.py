"""Session management for Aula authentication.

Handles API URL construction, session testing, and post-login setup
using OAuth access tokens obtained via MitID.
"""

import logging
import time

import requests
from homeassistant.exceptions import ConfigEntryNotReady

from ..const import API, API_VERSION

_LOGGER = logging.getLogger(__name__)


class SessionMixin:
    """Mixin providing session management methods for the Aula Client."""

    def _get_api_url(self, method_query):
        """Build API URL, appending access_token."""
        url = self.apiurl + method_query
        access_token = getattr(self, "_access_token", None)
        if access_token:
            separator = "&" if "?" in url else "?"
            url += f"{separator}access_token={access_token}"
        return url

    def test_session(self):
        """Test if current session is valid by checking actual API access."""
        try:
            api_versions = ["22", "21", "20", "19"]

            for ver in api_versions:
                url = f"https://www.aula.dk/api/v{ver}/?method=profiles.getProfilesByLogin"
                access_token = getattr(self, "_access_token", None)
                if access_token:
                    url += f"&access_token={access_token}"

                _LOGGER.debug(f"Testing session with: {url}")
                response = self._session.get(url, timeout=10)

                if response.status_code == 200:
                    try:
                        data = response.json()
                        if (
                            data.get("status", {}).get("message") == "OK"
                            and "data" in data
                            and "profiles" in data["data"]
                        ):
                            _LOGGER.debug(
                                f"Session valid, profiles loaded from API v{ver}"
                            )
                            self._profiles = data["data"]["profiles"]
                            return True
                    except (ValueError, KeyError) as e:
                        _LOGGER.debug(f"JSON parsing error: {e}")
                        continue
                elif response.status_code == 410:
                    continue
                elif response.status_code in [401, 403]:
                    _LOGGER.debug(f"Authentication failed: {response.status_code}")
                    break

            _LOGGER.debug("All session test URLs failed")
            return False
        except Exception as e:
            _LOGGER.debug(f"Session test error: {e}")
            return False

    def _setup_post_login(self):
        """Setup API access after successful login."""
        try:
            self.apiurl = API + API_VERSION + "/"
            _LOGGER.debug(f"Using API at {self.apiurl}")

            if not (hasattr(self, "_profiles") and self._profiles):
                ver = self._session.get(
                    self._get_api_url("?method=profiles.getProfilesByLogin"),
                    verify=True,
                    timeout=10,
                )

                if ver.status_code == 200:
                    try:
                        response_data = ver.json()
                        if (
                            "data" in response_data
                            and "profiles" in response_data["data"]
                        ):
                            self._profiles = response_data["data"]["profiles"]
                            _LOGGER.info(
                                f"Successfully connected to API {self.apiurl}"
                            )
                        else:
                            raise ConfigEntryNotReady(
                                "API response missing expected data structure"
                            )
                    except ValueError as e:
                        raise ConfigEntryNotReady(
                            f"Invalid JSON response from API: {e}"
                        )
                else:
                    raise ConfigEntryNotReady(
                        f"API returned unexpected status: {ver.status_code}"
                    )

            # Get profile context
            try:
                profile_context = self._session.get(
                    self._get_api_url(
                        "?method=profiles.getProfileContext&portalrole=guardian"
                    ),
                    verify=True,
                    timeout=10,
                ).json()

                if (
                    "data" in profile_context
                    and "institutionProfile" in profile_context["data"]
                ):
                    _LOGGER.info("Successfully retrieved profile context")
                else:
                    _LOGGER.warning("Profile context response missing expected data")

            except Exception as e:
                _LOGGER.error(f"Failed to get profile context: {e}")

        except ConfigEntryNotReady:
            raise
        except Exception as e:
            _LOGGER.error(f"Post-login setup failed: {e}")
            raise

        _LOGGER.debug(
            "Config - schoolschedule: "
            + str(self._schoolschedule)
            + ", config - ugeplaner: "
            + str(self._ugeplan)
        )
