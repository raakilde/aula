"""
Authentication management for Aula integration.
Handles MitID login, session management, cookie persistence, and browser automation.
"""

import json
import logging
import os
import time

import chromedriver_autoinstaller
import requests
from homeassistant.exceptions import ConfigEntryNotReady
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .const import API, API_VERSION

_LOGGER = logging.getLogger(__name__)


class AuthMixin:
    """Mixin providing authentication-related methods for the Aula Client."""

    def _check_browser_environment(self):
        """Check if browser automation is possible in current environment"""
        import platform
        import shutil

        # Check if running in Docker without display
        in_docker = os.path.exists("/.dockerenv")
        has_display = "DISPLAY" in os.environ or "WAYLAND_DISPLAY" in os.environ

        # Check for available browsers
        browsers = ["google-chrome", "chromium-browser", "chromium", "chrome"]
        browser_found = None
        for browser in browsers:
            if shutil.which(browser):
                browser_found = browser
                break

        # Check architecture compatibility
        arch = platform.machine().lower()
        supported_archs = ["x86_64", "amd64", "aarch64", "arm64"]
        arch_supported = any(a in arch for a in supported_archs)

        if not browser_found:
            return False, "No compatible browser found. Install Chrome or Chromium."

        if in_docker and not has_display:
            _LOGGER.info("Running in Docker environment, using headless browser mode")
            # Allow headless operation in Docker

        if not arch_supported:
            return False, f"Unsupported architecture: {arch}. Requires x86_64 or ARM64."

        try:
            # Test ChromeDriver availability based on architecture
            import platform

            arch = platform.machine().lower()

            if arch in ["aarch64", "arm64"]:
                # ARM64: Check for system ChromeDriver
                import shutil

                chromedriver_path = shutil.which("chromedriver")
                if not chromedriver_path:
                    return (
                        False,
                        "System ChromeDriver not found. Install with: sudo apt install chromium-driver",
                    )
                _LOGGER.debug(f"Found system ChromeDriver at: {chromedriver_path}")
            else:
                # x64: Test if chromedriver-autoinstaller can work
                try:
                    pass
                    # Don't actually install here, just check if we can
                except Exception as e:
                    return False, f"ChromeDriver auto-installer not available: {e}"

        except Exception as e:
            return False, f"ChromeDriver not available: {e}"

        return True, f"Browser environment ready: {browser_found} on {arch}"

    def test_jwt_token(self, jwt_token):
        """Test if a JWT token is valid by making an API call"""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant-Aula)",
            }

            # Test the JWT token with a simple API call
            response = requests.get(
                "https://www.aula.dk/api/v19/me", headers=headers, timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                return "id" in data or "userId" in data
            elif response.status_code == 401:
                _LOGGER.debug(
                    "JWT token authentication failed - token expired or invalid"
                )
                return False
            else:
                _LOGGER.debug(f"JWT token test returned status {response.status_code}")
                return False

        except Exception as e:
            _LOGGER.error(f"JWT token test failed: {e}")
            return False

    def test_authentication(self):
        """Test if authentication cookies are valid without browser automation"""
        try:
            if not self._auth_cookies:
                _LOGGER.debug("No authentication cookies provided")
                return False

            # Check if this is a JWT token stored as a special cookie
            if isinstance(self._auth_cookies, list) and len(self._auth_cookies) == 1:
                cookie = self._auth_cookies[0]
                if cookie.get("name") == "_jwt_token":
                    jwt_token = cookie.get("value")
                    if jwt_token:
                        return self.test_jwt_token(jwt_token)

            # Handle different cookie formats for regular session cookies
            if isinstance(self._auth_cookies, dict):
                # Simple JSON format: {"PHPSESSID": "value", "aula_token": "value"}
                # Convert to array format expected by init_session_with_cookies
                converted_cookies = []
                for name, value in self._auth_cookies.items():
                    converted_cookies.append(
                        {
                            "name": name,
                            "value": value,
                            "domain": ".aula.dk",
                            "path": "/",
                            "secure": True,
                        }
                    )
                self._auth_cookies = converted_cookies

            if self.init_session_with_cookies():
                return self.test_session()
            else:
                _LOGGER.debug("Failed to initialize session with cookies")
                return False

        except Exception as e:
            _LOGGER.error(f"Authentication test failed: {e}")
            return False

    def login(self, show_browser=True):
        """Login via MitID with environment detection and fallback options"""
        _LOGGER.info("Starting MitID authentication...")

        # Try to reuse existing session cookies first
        if self._auth_cookies and self.init_session_with_cookies():
            if self.test_session():
                _LOGGER.info("Successfully reused existing session cookies")
                # Initialize API after successful cookie authentication
                self._setup_post_login()
                return
            else:
                _LOGGER.info("Existing cookies invalid, need fresh authentication")

        # If we get here, browser automation would be needed
        # But since we're using user-redirect authentication, we should not reach this point
        _LOGGER.error(
            "No valid session cookies provided. Please use the Home Assistant configuration flow to authenticate."
        )
        raise ConfigEntryNotReady(
            "Authentication required. Please reconfigure the integration and provide valid session cookies from your browser after MitID login."
        )

    def _selenium_login(self, show_browser):
        """Perform Selenium-based MitID authentication"""
        # Setup Chrome options
        chrome_options = Options()
        if not show_browser:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--remote-debugging-port=9222")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--disable-features=VizDisplayCompositor")

        # Additional options for containerized environments
        if os.path.exists("/.dockerenv"):
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-software-rasterizer")
            chrome_options.add_argument("--disable-background-timer-throttling")
            chrome_options.add_argument("--single-process")
            chrome_options.add_argument("--disable-renderer-backgrounding")
            chrome_options.add_argument("--disable-backgrounding-occluded-windows")
            chrome_options.add_argument("--disable-client-side-phishing-detection")
            chrome_options.add_argument("--memory-pressure-off")

        driver = None
        try:
            # Use system ChromeDriver on ARM64, auto-install on x64
            import platform

            arch = platform.machine().lower()

            if arch in ["aarch64", "arm64"]:
                # Use system-installed Chromium and ChromeDriver on ARM64
                _LOGGER.info("ARM64 detected, using system Chromium and ChromeDriver")
                chrome_options.binary_location = "/usr/bin/chromium"

                # Use explicit service path for system ChromeDriver
                from selenium.webdriver.chrome.service import Service

                service = Service("/usr/bin/chromedriver")
                driver = webdriver.Chrome(service=service, options=chrome_options)
            else:
                # Use auto-installer on x64 systems
                _LOGGER.info("x64 detected, using ChromeDriver auto-installer")
                chromedriver_autoinstaller.install()
                driver = webdriver.Chrome(options=chrome_options)

            driver.implicitly_wait(10)

            # Navigate to Aula login
            _LOGGER.info("Navigating to Aula login page...")
            driver.get("https://login.aula.dk/auth/login.php?type=mitid")

            # Check if already logged in
            if self._check_already_logged_in(driver):
                _LOGGER.info("Already logged in, extracting session cookies")
                self._extract_session_cookies(driver)
                return

            # Handle MitID authentication flow
            self._handle_mitid_flow(driver)

            # Wait for successful authentication and extract cookies
            self._wait_for_auth_success(driver)
            self._extract_session_cookies(driver)

        except Exception as e:
            _LOGGER.error(f"Selenium browser automation failed: {e}")
            raise ConfigEntryNotReady(f"Browser automation error: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception as e:
                    _LOGGER.warning(f"Error closing browser: {e}")

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

    def test_session(self):
        """Test if current session is valid"""
        # Rate limiting: only test session once per 5 minutes minimum
        current_time = time.time()
        if current_time - self._last_session_test < 300:  # 5 minutes
            _LOGGER.debug("Session test rate limited, using cached result")
            return hasattr(self, "apiurl")  # Assume valid if we have API URL

        self._last_session_test = current_time

        try:
            # Test with the current API version endpoint using correct Aula API format
            test_urls = [
                "https://www.aula.dk/api/v22/?method=aulaToken.getAulaToken&widgetId=0018",  # Current version
                "https://www.aula.dk/api/v21/?method=aulaToken.getAulaToken&widgetId=0018",  # Fallback
                "https://www.aula.dk/api/v20/?method=aulaToken.getAulaToken&widgetId=0018",  # Fallback
                "https://www.aula.dk/api/v19/?method=aulaToken.getAulaToken&widgetId=0018",  # Fallback
            ]

            for url in test_urls:
                _LOGGER.debug(f"Testing session with: {url}")
                response = self._session.get(url, timeout=10)

                # Debug response details
                _LOGGER.debug(f"Response status: {response.status_code}")
                _LOGGER.debug(f"Response headers: {dict(response.headers)}")
                if response.status_code != 200:
                    _LOGGER.debug(f"Response content: {response.text[:500]}")

                if response.status_code == 200:
                    try:
                        data = response.json()
                        _LOGGER.debug(
                            f"Response data keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
                        )
                        # For aulaToken.getAulaToken, success means we got a "data" field with token
                        if "data" in data and data["data"]:
                            _LOGGER.debug(
                                f"Session valid, API version detected from {url}"
                            )
                            return True
                    except (ValueError, KeyError) as e:
                        _LOGGER.debug(f"JSON parsing error: {e}")
                        continue
                elif response.status_code == 410:
                    # API version not supported, try next
                    continue
                elif response.status_code in [401, 403]:
                    # Authentication failed
                    _LOGGER.debug(f"Authentication failed: {response.status_code}")
                    # Add brief delay to prevent rapid retry loops
                    time.sleep(1)
                    return False

            _LOGGER.debug("All session test URLs failed")

            # Try to auto-sync profile_change counter before giving up
            if self._try_profile_change_sync():
                _LOGGER.info(
                    "Successfully auto-synced profile_change counter, retrying authentication"
                )
                # Add delay to prevent rapid retries
                time.sleep(5)
                return self._retry_session_test()

            return False
        except Exception as e:
            _LOGGER.debug(f"Session test error: {e}")
            return False

    def _try_profile_change_sync(self):
        """Try to auto-detect and sync the current profile_change counter"""
        try:
            _LOGGER.debug("Attempting to auto-sync profile_change counter")
            # Add delay to prevent rapid polling
            time.sleep(2)

            # Try to access Aula portal page which might set fresh cookies
            portal_response = self._session.get(
                "https://www.aula.dk/portal/", timeout=10, allow_redirects=True
            )

            # Check if we got any updated cookies from the portal
            new_profile_change = None
            for cookie in self._session.cookies:
                if cookie.name == "profile_change":
                    new_profile_change = cookie.value
                    break

            if new_profile_change:
                old_value = self._session.cookies.get("profile_change")
                if new_profile_change != old_value:
                    _LOGGER.info(
                        f"Auto-detected profile_change update: {old_value} → {new_profile_change}"
                    )

                    # Update stored cookies for next time
                    if hasattr(self, "_auth_cookies") and isinstance(
                        self._auth_cookies, dict
                    ):
                        self._auth_cookies["profile_change"] = new_profile_change
                        _LOGGER.debug(
                            "Updated stored auth_cookies with new profile_change"
                        )

                        # Try to persist to Home Assistant config entry if available
                        self._persist_updated_cookies()

                    return True

            # Try alternative: make a simple API call and check response/cookies
            try:
                api_response = self._session.get(
                    "https://www.aula.dk/api/v22/?method=aulaToken.getAulaToken&widgetId=0001",
                    timeout=5,
                )

                # Check for any new profile_change value in response cookies
                for cookie in self._session.cookies:
                    if cookie.name == "profile_change":
                        current_stored = (
                            self._auth_cookies.get("profile_change")
                            if hasattr(self, "_auth_cookies")
                            else None
                        )
                        if cookie.value != current_stored:
                            _LOGGER.info(
                                f"Auto-detected profile_change from API call: {current_stored} → {cookie.value}"
                            )

                            if hasattr(self, "_auth_cookies") and isinstance(
                                self._auth_cookies, dict
                            ):
                                self._auth_cookies["profile_change"] = cookie.value
                                self._persist_updated_cookies()

                            return True

            except Exception as api_e:
                _LOGGER.debug(f"API-based profile_change detection failed: {api_e}")

            _LOGGER.debug("No profile_change update detected")
            return False

        except Exception as e:
            _LOGGER.debug(f"Profile change sync failed: {e}")
            return False

    def _retry_session_test(self):
        """Retry session test after profile_change sync"""
        try:
            # Quick single test with updated cookies
            test_url = "https://www.aula.dk/api/v22/?method=aulaToken.getAulaToken&widgetId=0018"
            response = self._session.get(test_url, timeout=10)

            if response.status_code == 200:
                try:
                    data = response.json()
                    if "data" in data and data["data"]:
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
        """Auto-increment profile_change counter based on session token lifecycle to maintain session"""
        try:
            current_time = time.time()

            # If session token time is not set, initialize it
            if self._session_token_time is None:
                self._session_token_time = current_time
                self._last_profile_change_increment = current_time

            # Check if 30 minutes have passed since last increment (based on session token timing)
            time_since_last_increment = (
                current_time - self._last_profile_change_increment
                if self._last_profile_change_increment is not None
                else current_time - self._session_token_time
            )

            if time_since_last_increment >= 1800:  # 30 minutes
                if (
                    isinstance(self._auth_cookies, dict)
                    and "profile_change" in self._auth_cookies
                ):
                    try:
                        # Get current profile_change value and increment it
                        current_value = int(self._auth_cookies["profile_change"])
                        new_value = current_value + 1

                        # Update stored cookies
                        self._auth_cookies["profile_change"] = str(new_value)

                        # Update session cookies if session exists
                        if self._session:
                            self._session.cookies.set("profile_change", str(new_value))

                        _LOGGER.info(
                            f"Auto-incremented profile_change (session-based): {current_value} → {new_value}"
                        )

                        # Update timestamp
                        self._last_profile_change_increment = current_time

                        # Persist updated cookies
                        self._persist_updated_cookies()

                    except (ValueError, TypeError) as e:
                        _LOGGER.warning(
                            f"Could not parse profile_change value for auto-increment: {e}"
                        )
                else:
                    # If this is the first time and we have profile_change in session cookies
                    if self._session and self._session.cookies.get("profile_change"):
                        session_value = self._session.cookies.get("profile_change")
                        try:
                            current_value = int(session_value)
                            new_value = current_value + 1

                            # Update both stored and session cookies
                            if isinstance(self._auth_cookies, dict):
                                self._auth_cookies["profile_change"] = str(new_value)

                            self._session.cookies.set("profile_change", str(new_value))

                            _LOGGER.info(
                                f"Auto-incremented profile_change (session-based): {current_value} → {new_value}"
                            )

                            # Update timestamp
                            self._last_profile_change_increment = current_time

                            # Persist updated cookies
                            self._persist_updated_cookies()

                        except (ValueError, TypeError) as e:
                            _LOGGER.warning(
                                f"Could not parse session profile_change value for auto-increment: {e}"
                            )

        except Exception as e:
            _LOGGER.error(f"Error in auto-increment profile_change: {e}")

    def get_session_cookies(self):
        """Get current session cookies for storage"""
        cookies = []
        for cookie in self._session.cookies:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                }
            )
        return cookies

    def import_cookies_from_file(self, cookie_file_path):
        """Import authentication cookies from a file (for headless setups)"""
        try:
            with open(cookie_file_path, "r") as f:
                cookies = json.load(f)

            self._auth_cookies = cookies
            _LOGGER.info(f"Imported {len(cookies)} cookies from {cookie_file_path}")

            # Initialize session token timing based on cookie import
            self._session_token_time = time.time()
            self._last_profile_change_increment = self._session_token_time

            # Test the imported session
            if self.init_session_with_cookies() and self.test_session():
                _LOGGER.info("Imported cookies are valid and working")
                return True
            else:
                _LOGGER.error("Imported cookies are invalid or expired")
                return False

        except Exception as e:
            _LOGGER.error(f"Failed to import cookies from {cookie_file_path}: {e}")
            return False

    def export_cookies_to_file(self, cookie_file_path):
        """Export current authentication cookies to a file"""
        try:
            if not self._auth_cookies:
                _LOGGER.error("No authentication cookies to export")
                return False

            with open(cookie_file_path, "w") as f:
                json.dump(self._auth_cookies, f, indent=2)

            _LOGGER.info(
                f"Exported {len(self._auth_cookies)} cookies to {cookie_file_path}"
            )
            return True

        except Exception as e:
            _LOGGER.error(f"Failed to export cookies to {cookie_file_path}: {e}")
            return False

    def _check_already_logged_in(self, driver):
        """Check if user is already logged in to Aula"""
        try:
            # Wait a moment for page to load
            time.sleep(2)

            # Check if we're redirected to the main portal
            if "aula.dk/portal" in driver.current_url:
                return True

            # Check for existing session indicators
            session_elements = [
                "//div[contains(@class, 'logged-in')]",
                "//a[contains(@href, 'logout')]",
                "//div[contains(text(), 'Logget ind')]",
            ]

            for xpath in session_elements:
                try:
                    driver.find_element(By.XPATH, xpath)
                    return True
                except NoSuchElementException:
                    continue

            return False

        except Exception as e:
            _LOGGER.debug(f"Error checking login status: {e}")
            return False

    def _handle_mitid_flow(self, driver):
        """Handle MitID authentication with QR code and manual options"""
        _LOGGER.info("Starting MitID authentication flow...")

        try:
            # Click MitID login button if present
            mitid_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(text(), 'MitID')]")
                )
            )
            mitid_button.click()
            _LOGGER.info("Clicked MitID login button")

        except TimeoutException:
            _LOGGER.info("No MitID button found, might already be on MitID page")

        # Wait for MitID interface to load
        time.sleep(3)

        # Try QR code authentication first (90 second timeout)
        if self._try_qr_authentication(driver, timeout=90):
            return

        # Fallback to manual authentication (120 second timeout)
        _LOGGER.info("QR code not available, trying manual authentication...")
        self._try_manual_authentication(driver, timeout=120)

    def _try_qr_authentication(self, driver, timeout=90):
        """Try QR code authentication"""
        try:
            # Look for QR code
            qr_code = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//img[contains(@alt, 'QR') or contains(@class, 'QR') or contains(@src, 'qr')]",
                    )
                )
            )

            _LOGGER.info(
                f"QR code found! Please scan with MitID app within {timeout} seconds..."
            )

            # Wait for QR code authentication to complete
            WebDriverWait(driver, timeout).until(
                lambda d: (
                    "aula.dk/portal" in d.current_url
                    or self._check_auth_success_elements(d)
                )
            )

            _LOGGER.info("QR code authentication successful!")
            return True

        except TimeoutException:
            _LOGGER.warning(f"QR code authentication timed out after {timeout} seconds")
            return False
        except NoSuchElementException:
            _LOGGER.info("No QR code found on page")
            return False

    def _try_manual_authentication(self, driver, timeout=120):
        """Try manual MitID authentication"""
        try:
            # Look for manual authentication options
            manual_options = [
                "//button[contains(text(), 'Chip')]",
                "//button[contains(text(), 'App')]",
                "//button[contains(text(), 'Fortsæt')]",
                "//input[@type='submit']",
            ]

            for xpath in manual_options:
                try:
                    element = driver.find_element(By.XPATH, xpath)
                    element.click()
                    _LOGGER.info(
                        f"Clicked manual authentication option: {element.text}"
                    )
                    break
                except NoSuchElementException:
                    continue

            _LOGGER.info(
                f"Please complete MitID authentication manually within {timeout} seconds..."
            )

            # Wait for manual authentication to complete
            WebDriverWait(driver, timeout).until(
                lambda d: (
                    "aula.dk/portal" in d.current_url
                    or self._check_auth_success_elements(d)
                )
            )

            _LOGGER.info("Manual MitID authentication successful!")

        except TimeoutException:
            _LOGGER.error(f"Manual authentication timed out after {timeout} seconds")
            raise ConfigEntryNotReady("MitID authentication timed out")

    def _check_auth_success_elements(self, driver):
        """Check for elements that indicate successful authentication"""
        success_indicators = [
            "//div[contains(@class, 'dashboard')]",
            "//div[contains(@class, 'portal')]",
            "//a[contains(@href, 'overblik')]",
            "//div[contains(text(), 'Velkommen')]",
        ]

        for xpath in success_indicators:
            try:
                driver.find_element(By.XPATH, xpath)
                return True
            except NoSuchElementException:
                continue

        return False

    def _wait_for_auth_success(self, driver):
        """Wait for authentication success and navigate to portal"""
        try:
            # Wait for successful redirect to Aula portal
            WebDriverWait(driver, 30).until(lambda d: "aula.dk/portal" in d.current_url)

            # Navigate to overview page to ensure full session
            _LOGGER.info("Navigating to overview page...")
            driver.get("https://www.aula.dk/portal/#/overblik")

            # Wait for overview page to load
            WebDriverWait(driver, 30).until(lambda d: "overblik" in d.current_url)

            _LOGGER.info("Successfully reached Aula overview page")

        except TimeoutException:
            _LOGGER.error("Failed to reach Aula portal after authentication")
            raise ConfigEntryNotReady(
                "Authentication appeared successful but failed to access Aula portal"
            )

    def _extract_session_cookies(self, driver):
        """Extract session cookies from browser"""
        cookies = driver.get_cookies()

        # Filter for important session cookies
        session_cookies = []
        for cookie in cookies:
            if any(
                key in cookie["name"].lower()
                for key in ["session", "auth", "login", "aula"]
            ):
                session_cookies.append(
                    {
                        "name": cookie["name"],
                        "value": cookie["value"],
                        "domain": cookie["domain"],
                        "path": cookie.get("path", "/"),
                        "secure": cookie.get("secure", False),
                    }
                )

        # Add all cookies to requests session
        self._session = requests.Session()
        for cookie in cookies:
            self._session.cookies.set(
                name=cookie["name"],
                value=cookie["value"],
                domain=cookie["domain"],
                path=cookie.get("path", "/"),
                secure=cookie.get("secure", False),
            )

        self._auth_cookies = session_cookies
        _LOGGER.info(f"Extracted {len(session_cookies)} session cookies")

    def _setup_post_login(self):
        """Setup API access after successful login"""
        try:
            # Find the API version and setup endpoints
            self.apiurl = API + API_VERSION
            apiver = int(API_VERSION)

            # Use API v22 directly - no version detection needed
            _LOGGER.debug(f"Using API v{apiver} at " + self.apiurl)
            try:
                ver = self._session.get(
                    self.apiurl + "?method=profiles.getProfilesByLogin",
                    verify=True,
                    timeout=10,
                )

                if ver.status_code == 403:
                    msg = "Access to Aula API was denied. Please check your authentication."
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
                            _LOGGER.info(f"Successfully connected to API {self.apiurl}")
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
                    _LOGGER.error(f"API returned unexpected status: {ver.status_code}")
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
                    self._profilecontext = profile_context["data"][
                        "institutionProfile"
                    ]["relations"]
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
