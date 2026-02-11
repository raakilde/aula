"""
Session and cookie management for Aula authentication.
Handles cookie initialization, session testing, profile_change sync, and cookie persistence.
"""

import logging
import time

import requests
from homeassistant.exceptions import ConfigEntryNotReady

from ..const import API, API_VERSION

_LOGGER = logging.getLogger(__name__)


class SessionMixin:
    """Mixin providing session/cookie management methods for the Aula Client."""

    def init_session_with_cookies(self):
        """Initialize session with stored cookies"""
        if not self._auth_cookies:
            return False

        self._session = requests.Session()

        # Handle different cookie input formats
        cookies_to_set = []

        if isinstance(self._auth_cookies, dict):
            # JSON format: {"PHPSESSID": "value", "Csrfp-Token": "value"}
            for name, value in self._auth_cookies.items():
                cookies_to_set.append(
                    {
                        "name": name,
                        "value": value,
                        "domain": ".aula.dk",
                        "path": "/",
                        "secure": True,
                    }
                )
        elif isinstance(self._auth_cookies, list):
            # Array format: [{"name": "PHPSESSID", "value": "abc123"}, ...]
            cookies_to_set = self._auth_cookies

        # Add cookies to session
        for cookie in cookies_to_set:
            self._session.cookies.set(
                name=cookie["name"],
                value=cookie["value"],
                domain=cookie.get("domain", ".aula.dk"),
                path=cookie.get("path", "/"),
                secure=cookie.get("secure", True),
            )
            _LOGGER.debug(f"Added cookie: {cookie['name']}")

        # Set essential headers for Aula API
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.aula.dk/portal/",
            "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }

        # Add CSRF token header if available
        csrf_token = self._session.cookies.get("Csrfp-Token")
        if csrf_token:
            headers["csrfp-token"] = csrf_token
            _LOGGER.debug("Added CSRF token header")

        self._session.headers.update(headers)

        # Initialize session token timing when session is established
        self._session_token_time = time.time()
        # Initialize profile_change timing based on session token time
        self._last_profile_change_increment = self._session_token_time

        return True

    def _invalidate_session(self):
        """Reset session state so the next test_session performs a real check."""
        self._last_session_test = 0
        self._session_valid = False
        if hasattr(self, "apiurl"):
            del self.apiurl
        _LOGGER.debug("Session state invalidated, will re-test on next call")

    def test_session(self):
        """Test if current session is valid by checking actual API access."""
        # Rate limiting: only test session once per 5 minutes minimum
        current_time = time.time()
        if current_time - self._last_session_test < 300:  # 5 minutes
            _LOGGER.debug("Session test rate limited, using cached result")
            return getattr(self, "_session_valid", False)

        self._last_session_test = current_time

        try:
            # Test with the actual profiles endpoint - this is what we need to work
            # Using the same endpoint as _setup_post_login avoids false positives
            # where aulaToken passes but profiles returns 403
            api_versions = ["22", "21", "20", "19"]

            for ver in api_versions:
                url = f"https://www.aula.dk/api/v{ver}/?method=profiles.getProfilesByLogin"
                _LOGGER.debug(f"Testing session with: {url}")
                response = self._session.get(url, timeout=10)

                _LOGGER.debug(f"Response status: {response.status_code}")
                if response.status_code != 200:
                    _LOGGER.debug(f"Response content: {response.text[:500]}")

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
                            # Cache profiles so _setup_post_login can skip duplicate call
                            self._profiles = data["data"]["profiles"]
                            self._session_valid = True
                            return True
                    except (ValueError, KeyError) as e:
                        _LOGGER.debug(f"JSON parsing error: {e}")
                        continue
                elif response.status_code == 410:
                    # API version not supported, try next
                    continue
                elif response.status_code in [401, 403]:
                    _LOGGER.debug(f"Authentication failed: {response.status_code}")
                    break  # No point trying other versions

            _LOGGER.debug("All session test URLs failed")

            # Try to auto-sync profile_change counter before giving up
            if self._try_profile_change_sync():
                _LOGGER.info(
                    "Successfully auto-synced profile_change counter, retrying"
                )
                time.sleep(3)
                result = self._retry_session_test()
                self._session_valid = result
                return result

            self._session_valid = False
            return False
        except Exception as e:
            _LOGGER.debug(f"Session test error: {e}")
            self._session_valid = False
            return False

    def _try_profile_change_sync(self):
        """Try to sync the current profile_change counter from the server."""
        try:
            _LOGGER.debug("Attempting to sync profile_change from server")

            old_profile_change = (
                self._auth_cookies.get("profile_change")
                if isinstance(self._auth_cookies, dict)
                else None
            )

            # Hit the portal page — the server sets the current profile_change cookie
            try:
                self._session.get(
                    "https://www.aula.dk/portal/", timeout=10, allow_redirects=True
                )
            except requests.exceptions.RequestException as e:
                _LOGGER.debug(f"Portal request failed: {e}")

            # Pick up whatever the server sent back
            self._sync_cookies_from_session()

            new_profile_change = (
                self._auth_cookies.get("profile_change")
                if isinstance(self._auth_cookies, dict)
                else None
            )

            if new_profile_change and new_profile_change != old_profile_change:
                _LOGGER.info(
                    f"profile_change synced from portal: {old_profile_change} → {new_profile_change}"
                )
                return True

            _LOGGER.debug("No profile_change update from portal")
            return False

        except Exception as e:
            _LOGGER.debug(f"Profile change sync failed: {e}")
            return False

    def _retry_session_test(self):
        """Retry session test after profile_change sync"""
        try:
            test_url = f"{API}{API_VERSION}/?method=profiles.getProfilesByLogin"
            response = self._session.get(test_url, timeout=10)

            if response.status_code == 200:
                try:
                    data = response.json()
                    if (
                        data.get("status", {}).get("message") == "OK"
                        and "data" in data
                        and "profiles" in data["data"]
                    ):
                        self._profiles = data["data"]["profiles"]
                        _LOGGER.info(
                            "Session test successful after profile_change sync"
                        )
                        return True
                except (ValueError, KeyError):
                    pass

            _LOGGER.debug("Session test still failing after profile_change sync")
            return False

        except Exception as e:
            _LOGGER.debug(f"Retry session test error: {e}")
            return False

    def _persist_updated_cookies(self):
        """Try to persist updated cookies to Home Assistant config entry"""
        try:
            if self._cookie_persist_callback and hasattr(self, "_auth_cookies"):
                _LOGGER.debug(
                    "Calling cookie persistence callback with updated cookies"
                )
                self._cookie_persist_callback(dict(self._auth_cookies))
            else:
                _LOGGER.debug("No cookie persistence callback available")
        except Exception as e:
            _LOGGER.error(f"Cookie persistence callback failed: {e}")

    def _auto_increment_profile_change(self):
        """Sync profile_change counter from the server and keep session alive.

        Instead of blindly incrementing, we read the actual profile_change
        cookie that the server sends back after a lightweight API call.
        This keeps us in sync even when the user uses Aula in their browser
        simultaneously, and also serves as a session keep-alive.
        """
        try:
            current_time = time.time()

            if self._session_token_time is None:
                self._session_token_time = current_time
                self._last_profile_change_increment = current_time

            time_since_last = (
                current_time - self._last_profile_change_increment
                if self._last_profile_change_increment is not None
                else current_time - self._session_token_time
            )

            # Sync every 10 minutes (keeps PHP session alive & profile_change fresh)
            if time_since_last < 600:  # 10 minutes
                return

            _LOGGER.debug("Running session keep-alive and profile_change sync")

            old_profile_change = (
                self._auth_cookies.get("profile_change")
                if isinstance(self._auth_cookies, dict)
                else None
            )

            # Make a lightweight API call to keep the PHP session alive
            # and let the server send us the current profile_change cookie
            try:
                resp = self._session.get(
                    f"{API}{API_VERSION}/?method=profiles.getProfilesByLogin",
                    verify=True,
                    timeout=10,
                )

                if resp.status_code == 200:
                    _LOGGER.debug("Session keep-alive successful")
                else:
                    _LOGGER.debug(
                        f"Session keep-alive returned status {resp.status_code}"
                    )
            except requests.exceptions.RequestException as e:
                _LOGGER.debug(f"Session keep-alive request failed: {e}")

            # Read the actual profile_change value the server set in cookies
            self._sync_cookies_from_session()

            new_profile_change = (
                self._auth_cookies.get("profile_change")
                if isinstance(self._auth_cookies, dict)
                else None
            )

            if new_profile_change and new_profile_change != old_profile_change:
                _LOGGER.info(
                    f"profile_change synced from server: {old_profile_change} → {new_profile_change}"
                )

            self._last_profile_change_increment = current_time

        except Exception as e:
            _LOGGER.error(f"Error in profile_change sync / keep-alive: {e}")

    def _sync_cookies_from_session(self):
        """Read cookies from the requests session and persist any updates.

        After an API call, the server may update cookies (profile_change,
        PHPSESSID rotation, etc.). Capture those changes so they survive
        session re-initialization and HA restarts.
        """
        if not self._session or not isinstance(self._auth_cookies, dict):
            return

        changed = False
        for cookie in self._session.cookies:
            if cookie.domain and "aula.dk" not in cookie.domain:
                continue
            old = self._auth_cookies.get(cookie.name)
            if old != cookie.value:
                self._auth_cookies[cookie.name] = cookie.value
                changed = True
                _LOGGER.debug(
                    f"Cookie updated from server: {cookie.name} = {old} → {cookie.value}"
                )

        if changed:
            self._persist_updated_cookies()

    def login(self, show_browser=True):
        """Login via cookie-based authentication"""
        _LOGGER.info("Starting authentication...")

        # Reset rate limiter so test_session does a real check
        self._last_session_test = 0

        # Try to reuse existing session cookies
        if self._auth_cookies and self.init_session_with_cookies():
            if self.test_session():
                _LOGGER.info("Successfully reused existing session cookies")
                # Initialize API after successful cookie authentication
                self._setup_post_login()
                return
            else:
                _LOGGER.info("Existing cookies invalid, need fresh authentication")

        _LOGGER.error(
            "No valid session cookies provided. Please use the Home Assistant configuration flow to authenticate."
        )
        raise ConfigEntryNotReady(
            "Authentication required. Please reconfigure the integration and provide valid session cookies from your browser after MitID login."
        )

    def _setup_post_login(self):
        """Setup API access after successful login"""
        try:
            # Set API URL with trailing slash (matches Aula's expected URL format)
            self.apiurl = API + API_VERSION + "/"
            apiver = int(API_VERSION)

            _LOGGER.debug(f"Using API v{apiver} at {self.apiurl}")
            try:
                # If test_session already loaded profiles, skip the duplicate call
                if hasattr(self, "_profiles") and self._profiles:
                    _LOGGER.debug(
                        "Profiles already loaded from session test, skipping duplicate API call"
                    )
                else:
                    ver = self._session.get(
                        self.apiurl + "?method=profiles.getProfilesByLogin",
                        verify=True,
                        timeout=10,
                    )

                    if ver.status_code == 403:
                        _LOGGER.warning(
                            "Got 403 from profiles API, attempting profile_change sync"
                        )
                        if self._try_profile_change_sync():
                            time.sleep(3)
                            ver = self._session.get(
                                self.apiurl + "?method=profiles.getProfilesByLogin",
                                verify=True,
                                timeout=10,
                            )
                        if ver.status_code == 403:
                            self._invalidate_session()
                            msg = (
                                "Adgang til Aula API blev nægtet (HTTP 403). "
                                "Dine session-cookies er sandsynligvis udløbet. "
                                "Gå til Indstillinger → Integrationer → Aula → Konfigurer "
                                "og indsæt friske cookies fra din browser efter MitID-login."
                            )
                            _LOGGER.error(msg)
                            raise ConfigEntryNotReady(msg)
                    elif ver.status_code == 200:
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
                                _LOGGER.error(
                                    "API response missing expected data structure"
                                )
                                raise ConfigEntryNotReady(
                                    "API response missing expected data structure"
                                )
                        except ValueError as e:
                            _LOGGER.error(f"Invalid JSON response from API: {e}")
                            raise ConfigEntryNotReady(
                                f"Invalid JSON response from API: {e}"
                            )
                    else:
                        _LOGGER.error(
                            f"API returned unexpected status: {ver.status_code}"
                        )
                        raise ConfigEntryNotReady(
                            f"API returned unexpected status: {ver.status_code}"
                        )

            except requests.exceptions.RequestException as e:
                _LOGGER.error(f"API request failed: {e}")
                raise ConfigEntryNotReady(f"API request failed: {e}")

            # Get profile context
            try:
                profile_context = self._session.get(
                    self.apiurl
                    + "?method=profiles.getProfileContext&portalrole=guardian",
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

        except Exception as e:
            _LOGGER.error(f"Post-login setup failed: {e}")
            raise

        _LOGGER.debug(
            "Config - schoolschedule: "
            + str(self._schoolschedule)
            + ", config - ugeplaner: "
            + str(self._ugeplan)
        )
