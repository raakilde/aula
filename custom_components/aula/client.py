"""
Aula client with MitID authentication
Based on https://github.com/JBoye/HA-Aula
"""

import datetime
import json
import logging
import os
import re
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

from .const import (
    API,
    API_VERSION,
    CICERO_API,
    MEEBOOK_API,
    MIN_UDDANNELSE_API,
    SYSTEMATIC_API,
)
from .minuddannelse import MinUddannelse

_LOGGER = logging.getLogger(__name__)


class Client:
    huskeliste = {}
    presence = {}
    presence_templates = {}
    presence_templates_next = {}
    closed_days = {}
    weekly_presence_current = {}
    weekly_presence_next = {}
    ugep_attr = {}
    ugepnext_attr = {}
    widgets = {}
    tokens = {}
    loaned_books = {}
    forloebthisweek = {}
    forloebnext = {}
    ugenotethisweek = {}
    ugenotenextweek = {}
    posts = {}
    posts_by_child = {}

    def __init__(
        self,
        schoolschedule,
        ugeplan,
        auth_cookies=None,
        cookie_persist_callback=None,
    ):
        self._session = None
        self._auth_cookies = auth_cookies or {}
        self._cookie_persist_callback = cookie_persist_callback
        self._last_session_test = 0  # Rate limiting for session tests
        self._last_profile_change_increment = None  # Track when profile_change was last incremented based on session token
        self._session_token_time = None  # Track session token initialization time
        self._schoolschedule = schoolschedule
        self._ugeplan = ugeplan
        # Widget-based features now auto-detected from API
        self._bibliotek = None  # Auto-detected
        self._minUddannelseForloeb = None  # Auto-detected
        self._minUddannelseOpgaveListe = None  # Auto-detected
        self._minUddannelseUgeNote = None  # Auto-detected
        self._minUddannelse = MinUddannelse(
            None,
            None,
            None,  # Will be set dynamically based on widget availability
        )

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

    def get_widgets(self):
        try:
            # Use profiles.getProfileContext to get widget configurations
            response = self._session.get(
                self.apiurl + "?method=profiles.getProfileContext", verify=True
            )
            profile_context = response.json()

            if profile_context.get("status", {}).get("message") == "OK":
                widget_configs = (
                    profile_context.get("data", {})
                    .get("pageConfiguration", {})
                    .get("widgetConfigurations", [])
                )

                # Extract widget IDs and names from the configuration
                for widget_config in widget_configs:
                    widget = widget_config.get("widget", {})
                    widget_id = widget.get("widgetId", "")
                    widget_name = widget.get("name", "")
                    if widget_id and widget_name:
                        self.widgets[widget_id] = widget_name

                _LOGGER.debug(
                    "Widgets found from profile context: " + str(self.widgets)
                )

                # Dynamically set widget flags based on detected widgets and institution appropriateness
                self._bibliotek = self._should_enable_widget("0019")  # Library
                self._minUddannelseForloeb = self._should_enable_widget(
                    "0028"
                )  # Education/Learning
                self._minUddannelseOpgaveListe = self._should_enable_widget(
                    "0028"
                )  # Same widget for different features
                self._minUddannelseUgeNote = self._should_enable_widget(
                    "0028"
                )  # Same widget for different features

                # Update MinUddannelse instance with dynamic settings
                if hasattr(self, "_minUddannelse"):
                    self._minUddannelse._minUddannelseForloeb = (
                        self._minUddannelseForloeb
                    )
                    self._minUddannelse._minUddannelseOpgaveListe = (
                        self._minUddannelseOpgaveListe
                    )
                    self._minUddannelse._minUddannelseugenote = (
                        self._minUddannelseUgeNote
                    )
                else:
                    # Create MinUddannelse if not already exists
                    self._minUddannelse = MinUddannelse(
                        self._minUddannelseForloeb,
                        self._minUddannelseOpgaveListe,
                        self._minUddannelseUgeNote,
                    )

                _LOGGER.info(
                    f"Dynamic widget flags set: bibliotek={self._bibliotek}, minUddannelse_forloeb={self._minUddannelseForloeb}, minUddannelse_opgaveliste={self._minUddannelseOpgaveListe}, minUddannelse_ugenote={self._minUddannelseUgeNote}"
                )

            else:
                _LOGGER.warning("Failed to get profile context for widgets")
        except Exception as e:
            _LOGGER.error(f"Error getting widgets from profile context: {e}")

    def _should_enable_widget(self, widget_id):
        """Check if a widget should be enabled based on availability and institution appropriateness"""
        if widget_id not in self.widgets:
            return False

        # Check if any child has an institution type where this widget is appropriate
        for child_id, institution_type in self._institution_types.items():
            if self.is_widget_appropriate_for_institution(widget_id, institution_type):
                return True

        return False

    def is_widget_appropriate_for_institution(self, widget_id, institution_type):
        """Check if a widget is appropriate for a specific institution type"""
        # Widgets that are only appropriate for schools (not kindergartens)
        school_only_widgets = {
            "0019": "Library",  # Libraries typically not available in kindergartens
            "0028": "Education/Learning",  # Advanced learning features for schools
            "0062": "Reminders",  # Reminder system more relevant for older students
        }

        # If it's a kindergarten and the widget is school-only, return False
        if institution_type == "kindergarten" and widget_id in school_only_widgets:
            _LOGGER.debug(
                f"Widget {widget_id} ({school_only_widgets[widget_id]}) filtered out for kindergarten"
            )
            return False

        # All other widgets (attendance, schedules, etc.) are appropriate for all types
        return True

    def _detect_institution_type(self, institution_profile):
        """Detect institution type from profile data"""
        # First try to get institution type from the institution object
        if "institution" in institution_profile and institution_profile[
            "institution"
        ].get("type"):
            institution_type = institution_profile["institution"]["type"].lower()
            # Map API types to our standardized names
            if institution_type == "daycare":
                return "kindergarten"
            elif institution_type == "school":
                return "school"
            else:
                return institution_type

        # Fallback check for institutionType field
        if institution_profile.get("institutionType"):
            institution_type = institution_profile["institutionType"].lower()
            if institution_type == "daycare":
                return "kindergarten"
            elif institution_type == "school":
                return "school"
            else:
                return institution_type

        # Fallback detection based on name and metadata
        institution_name = institution_profile.get("institutionName", "").lower()
        metadata = institution_profile.get("metadata", "").lower()

        # Common kindergarten indicators
        kindergarten_keywords = [
            "børnehave",
            "vuggestue",
            "dagpleje",
            "kindergarten",
            "pionererne",
            "gardikjærgård",
            "gadkjærgård",
            "børnehus",
            "dagplejen",
        ]

        # Common school indicators
        school_keywords = [
            "skole",
            "school",
            "gymnasium",
            "erhvervsskole",
            "teknisk skole",
            "handelsskole",
            "hf",
            "ht",
        ]

        for keyword in kindergarten_keywords:
            if keyword in institution_name or keyword in metadata:
                return "kindergarten"

        for keyword in school_keywords:
            if keyword in institution_name or keyword in metadata:
                return "school"

        # Check if metadata contains class indicators (like "2.3" for 2nd grade class 3)
        import re

        if re.match(r"^\d+\.\d+$", metadata.strip()):
            return "school"

        # Default fallback based on institution name patterns
        if "skole" in institution_name:
            return "school"
        elif any(kw in institution_name for kw in ["gård", "hus", "have"]):
            return "kindergarten"

        return "unknown"

    def get_token(self, widgetid, mock=False):
        """Get Bearer token for specific widget API calls"""
        _LOGGER.debug("Requesting token for widget " + widgetid)
        if mock:
            return "MockToken"

        # Check if we already have a token for this widget
        if widgetid in self.tokens:
            _LOGGER.debug(f"Reusing existing token for widget {widgetid}")
            return self.tokens[widgetid]

        try:
            # Get token using current session
            response = self._session.get(
                self.apiurl + "?method=aulaToken.getAulaToken&widgetId=" + widgetid,
                verify=True,
                timeout=10,
            )

            if response.status_code != 200:
                _LOGGER.error(
                    f"Token request failed with status {response.status_code} for widget {widgetid}"
                )
                raise Exception(f"Token request failed: {response.status_code}")

            response_data = response.json()
            if "data" not in response_data:
                _LOGGER.error(
                    f"Token response missing data for widget {widgetid}: {response_data}"
                )
                raise Exception("Token response missing data")

            self._bearertoken = response_data["data"]
            token = "Bearer " + str(self._bearertoken)
            self.tokens[widgetid] = token

            _LOGGER.debug(f"Successfully obtained token for widget {widgetid}")
            return token

        except Exception as e:
            _LOGGER.error(f"Failed to get token for widget {widgetid}: {e}")
            raise ConfigEntryNotReady(
                f"Failed to obtain API token for widget {widgetid}: {e}"
            )

    def update_data(self):
        # Try to reuse existing session first
        if (
            self._auth_cookies
            and self.init_session_with_cookies()
            and self.test_session()
        ):
            _LOGGER.debug("Reusing existing session cookies")
            # Ensure API is set up
            if not hasattr(self, "apiurl"):
                self._setup_post_login()
        else:
            _LOGGER.debug("Need to authenticate - cookies invalid or missing")
            # Re-authenticate using the same cookies (they might work on retry)
            if self._auth_cookies:
                try:
                    self.login(show_browser=False)
                except Exception as e:
                    _LOGGER.error(f"Re-authentication failed: {e}")
                    raise ConfigEntryNotReady(
                        "Authentication session expired. Please reconfigure the integration."
                    )
            else:
                raise ConfigEntryNotReady(
                    "No authentication cookies available. Please reconfigure the integration."
                )

        # Test API access
        is_logged_in = False
        if self._session and hasattr(self, "apiurl"):
            # Auto-increment profile_change if 30 minutes have passed
            self._auto_increment_profile_change()

            try:
                response = self._session.get(
                    self.apiurl + "?method=profiles.getProfilesByLogin",
                    verify=True,
                    timeout=10,
                ).json()
                is_logged_in = response.get("status", {}).get("message") == "OK"
            except Exception as e:
                _LOGGER.warning(f"Failed to test API access: {e}")
                is_logged_in = False

        _LOGGER.debug("is_logged_in? " + str(is_logged_in))

        if not is_logged_in:
            _LOGGER.error("API access test failed - authentication may have expired")
            raise ConfigEntryNotReady(
                "API access denied. Please reconfigure the integration with fresh cookies."
            )

        self._childnames = {}
        self._institutions = {}
        self._institution_types = {}
        self._childuserids = []
        self._childids = []
        self._children = []
        self._institutionProfiles = []
        self._institutionProfileIdsList = []

        for profile in self._profiles:
            for child in profile["children"]:
                self._childnames[child["id"]] = child["name"]
                self._institutions[child["id"]] = child["institutionProfile"][
                    "institutionName"
                ]

                # Detect institution type
                institution_type = self._detect_institution_type(
                    child["institutionProfile"]
                )
                self._institution_types[child["id"]] = institution_type

                self._children.append(child)
                self._childids.append(str(child["id"]))
                self._childuserids.append(str(child["userId"]))

                # Add child IDs to institution profile IDs list for posts filtering
                child_id = str(child["id"])
                if child_id not in self._institutionProfileIdsList:
                    self._institutionProfileIdsList.append(child_id)

            for institutioncode in profile["institutionProfiles"]:
                if (
                    str(institutioncode["institutionCode"])
                    not in self._institutionProfiles
                ):
                    self._institutionProfiles.append(
                        str(institutioncode["institutionCode"])
                    )
                # Add institution profile IDs for posts filtering
                institution_profile_id = str(institutioncode["id"])
                if institution_profile_id not in self._institutionProfileIdsList:
                    self._institutionProfileIdsList.append(institution_profile_id)

        # Debug child mapping after initialization
        _LOGGER.debug(
            f"MAIL DEBUG: Initialized children with IDs: {[str(child['id']) for child in self._children]}"
        )
        _LOGGER.debug(f"MAIL DEBUG: Child names mapping: {self._childnames}")
        _LOGGER.debug("Child ids and names: " + str(self._childnames))
        _LOGGER.debug("Child ids and institution names: " + str(self._institutions))
        _LOGGER.debug(
            "Child ids and institution types: " + str(self._institution_types)
        )
        _LOGGER.debug("Institution codes: " + str(self._institutionProfiles))
        _LOGGER.debug(
            "Institution profile IDs for posts: " + str(self._institutionProfileIdsList)
        )

        self._daily_overview = {}
        for i, child in enumerate(self._children):
            response = self._session.get(
                self.apiurl
                + "?method=presence.getDailyOverview&childIds[]="
                + str(child["id"]),
                verify=True,
            ).json()
            if len(response["data"]) > 0:
                self.presence[str(child["id"])] = 1
                self._daily_overview[str(child["id"])] = response["data"][0]
            else:
                _LOGGER.warn(
                    "Unable to retrieve presence data from Aula from child with id "
                    + str(child["id"])
                    + ". Some data will be missing from sensor entities."
                )
                self.presence[str(child["id"])] = 0
        _LOGGER.debug("Child ids and presence data status: " + str(self.presence))

        # Weekly Presence (Komme og Gå):
        self._get_weekly_presence()

        # Presence Templates (Weekly Schedule):
        self._get_presence_templates()

        # Closed Days (Institution holidays):
        self._get_closed_days()

        # Messages:
        try:
            _LOGGER.debug(
                "OLD MESSAGES: About to call messaging.getThreads (page 0 only)..."
            )
            mesres = self._session.get(
                self.apiurl
                + "?method=messaging.getThreads&sortOn=date&orderDirection=desc&page=0",
                verify=True,
            )
            _LOGGER.debug(f"OLD MESSAGES: Response status: {mesres.status_code}")
            # _LOGGER.debug("mesres "+str(mesres.text))
            self.unread_messages = 0
            unread = 0
            self.message = {}
            for mes in mesres.json()["data"]["threads"]:
                if not mes["read"]:
                    # self.unread_messages = 1
                    unread = 1
                    threadid = mes["id"]
                    break
            _LOGGER.debug(f"OLD MESSAGES: Found unread messages: {unread}")
        except Exception as e:
            _LOGGER.error(f"OLD MESSAGES: Failed to fetch messages: {e}")
            self.unread_messages = 0
            unread = 0
            self.message = {}

        # Continue with unread message details (also within try-catch)
        try:
            # if self.unread_messages == 1:
            if unread == 1:
                # _LOGGER.debug("tid "+str(threadid))
                threadres = self._session.get(
                    self.apiurl
                    + "?method=messaging.getMessagesForThread&threadId="
                    + str(threadid)
                    + "&page=0",
                    verify=True,
                )
                # _LOGGER.debug("threadres "+str(threadres.text))
                if threadres.json()["status"]["code"] == 403:
                    self.message["text"] = (
                        "Log ind på Aula med MitID for at læse denne besked."
                    )
                    self.message["sender"] = "Ukendt afsender"
                    self.message["subject"] = "Følsom besked"
                else:
                    for message in threadres.json()["data"]["messages"]:
                        if message["messageType"] == "Message":
                            try:
                                self.message["text"] = message["text"]["html"]
                            except:
                                try:
                                    self.message["text"] = message["text"]
                                except:
                                    self.message["text"] = "intet indhold..."
                                    _LOGGER.warning(
                                        "There is an unread message, but we cannot get the text."
                                    )
                            try:
                                self.message["sender"] = message["sender"]["fullName"]
                            except:
                                self.message["sender"] = "Ukendt afsender"
                            try:
                                self.message["subject"] = threadres.json()["data"][
                                    "subject"
                                ]
                            except:
                                self.message["subject"] = ""
                            self.unread_messages = 1
                            break
        except Exception as e:
            _LOGGER.error(f"OLD MESSAGES: Failed to fetch unread message details: {e}")
            self.unread_messages = 0

        _LOGGER.debug("OLD MESSAGES: Section completed, continuing to Calendar...")

        # Calendar:
        if self._schoolschedule == True:
            instProfileIds = ",".join(self._childids)
            csrf_token = self._session.cookies.get_dict()["Csrfp-Token"]
            headers = {"csrfp-token": csrf_token, "content-type": "application/json"}
            start = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%d 00:00:00.0000%z"
            )
            _end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                days=14
            )
            end = _end.strftime("%Y-%m-%d 00:00:00.0000%z")
            post_data = (
                '{"instProfileIds":['
                + instProfileIds
                + '],"resourceIds":[],"start":"'
                + start
                + '","end":"'
                + end
                + '"}'
            )
            _LOGGER.debug("Fetching calendars...")
            # _LOGGER.debug("Calendar post-data: "+str(post_data))
            res = self._session.post(
                self.apiurl + "?method=calendar.getEventsByProfileIdsAndResourceIds",
                data=post_data,
                headers=headers,
                verify=True,
            )
            try:
                with open("skoleskema.json", "w") as skoleskema_json:
                    json.dump(res.text, skoleskema_json)
            except:
                _LOGGER.warn(
                    "Got the following reply when trying to fetch calendars: "
                    + str(res.text)
                )
        # End of calendar

        # Bibliotek:
        if self._bibliotek is True:
            if len(self.widgets) == 0:
                self.get_widgets()

            # Check for widget availability and appropriateness for each child
            if "0019" not in self.widgets:
                _LOGGER.info("Library widget (0019) not found in available widgets")
            else:
                # Check if any child has an institution type where library is appropriate
                applicable_children = []
                for child_id, institution_type in self._institution_types.items():
                    if self.is_widget_appropriate_for_institution(
                        "0019", institution_type
                    ):
                        applicable_children.append(child_id)

                if not applicable_children:
                    _LOGGER.info(
                        "Library widget available but not appropriate for any child's institution type"
                    )
                else:
                    guardian = self._session.get(
                        self.apiurl
                        + "?method=profiles.getProfileContext&portalrole=guardian",
                        verify=True,
                    ).json()["data"]["userId"]
                    childUserIds = ",".join(self._childuserids)

                    token = self.get_token("0019")

                    books = self._session.get(
                        CICERO_API
                        + "/portal-api/rest/aula/library/status/v3?"
                        + "institutions="
                        + "&institutions=".join(self._institutionProfiles)
                        + "&children="
                        + "&children=".join(self._childuserids)
                        + "&coverImageHeight=160&widgetVersion=1.6"
                        + "&userProfile=guardian"
                        + "&sessionUUID="
                        + "mitid_user",
                        headers={"Authorization": token, "accept": "application/json"},
                        verify=True,
                    ).json()

                    self.loaned_books = {}
                    for loaned_book in books["loans"]:
                        book = {
                            "Title": loaned_book["title"],
                            "Author": loaned_book["author"],
                            "DueDate": loaned_book["dueDate"],
                            "NumberOfLoans": loaned_book["numberOfLoans"],
                            "Cover": str(loaned_book["coverImageUrl"]).strip(),
                        }

                        if loaned_book["patronDisplayName"] not in self.loaned_books:
                            self.loaned_books[loaned_book["patronDisplayName"]] = []

                        self.loaned_books[loaned_book["patronDisplayName"]].append(book)

        # End of bibliotek

        # Min Uddannelse Forløb:
        if self._minUddannelseForloeb is True:
            if len(self.widgets) == 0:
                self.get_widgets()

            if "0028" not in self.widgets:
                _LOGGER.info("Education widget (0028) not found in available widgets")
            else:
                # Check if any child has an institution type where education widget is appropriate
                applicable_children = []
                for child_id, institution_type in self._institution_types.items():
                    if self.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    ):
                        applicable_children.append(child_id)

                if not applicable_children:
                    _LOGGER.info(
                        "Education widget available but not appropriate for any child's institution type"
                    )
                else:
                    token = self.get_token("0028")
                    now = datetime.datetime.now() + datetime.timedelta(weeks=1)
                    thisweek = datetime.datetime.now().strftime("%Y-W%W")
                    nextweek = now.strftime("%Y-W%W")
                    self.forloebthisweek = self._minUddannelse.forloeb(
                        self._session,
                        token,
                        thisweek,
                        self._childuserids,
                        self._institutionProfiles,
                        "mitid_user",
                    )
                    self.forloebnext = self._minUddannelse.forloeb(
                        self._session,
                        token,
                        nextweek,
                        self._childuserids,
                        self._institutionProfiles,
                        "mitid_user",
                    )
        # End of Min Uddannelse Forløb

        # Min Uddannelse Opgave Liste
        if self._minUddannelseOpgaveListe is True:
            if len(self.widgets) == 0:
                self.get_widgets()

            if "0028" not in self.widgets:
                _LOGGER.info("Education widget (0028) not found in available widgets")
            else:
                # Check if any child has an institution type where education widget is appropriate
                applicable_children = []
                for child_id, institution_type in self._institution_types.items():
                    if self.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    ):
                        applicable_children.append(child_id)

                if not applicable_children:
                    _LOGGER.info(
                        "Education widget available but not appropriate for any child's institution type"
                    )
                else:
                    token = self.get_token("0028")
                    week = datetime.datetime.now().strftime("%Y-W%W")
                    opgaver = self._minUddannelse.opgaveListe(
                        self._session,
                        token,
                        week,
                        self._childuserids,
                        self._institutionProfiles,
                        "mitid_user",
                    )

                    # Currently only one student supported
                    try:
                        with open(
                            "uddannelseopgaveliste.json", "w"
                        ) as uddannelseopgaveliste_json:
                            json.dump(opgaver, uddannelseopgaveliste_json)
                    except:
                        _LOGGER.warn(
                            "Got the following reply when trying to fetch calendars: "
                            + str(json.dumps(opgaver))
                        )
        # End of Min Uddannelse Opgave Liste

        # Min Uddannelse Uge Note
        if self._minUddannelseUgeNote is True:
            if len(self.widgets) == 0:
                self.get_widgets()
            if "0028" not in self.widgets:
                _LOGGER.info("Education widget (0028) not found in available widgets")
            else:
                # Check if any child has an institution type where education widget is appropriate
                applicable_children = []
                for child_id, institution_type in self._institution_types.items():
                    if self.is_widget_appropriate_for_institution(
                        "0028", institution_type
                    ):
                        applicable_children.append(child_id)

                if not applicable_children:
                    _LOGGER.info(
                        "Education widget available but not appropriate for any child's institution type"
                    )
                else:
                    token = self.get_token("0028")
                    now = datetime.datetime.now() + datetime.timedelta(weeks=1)
                    thisweek = datetime.datetime.now().strftime("%Y-W%W")
                    nextweek = now.strftime("%Y-W%W")

                    try:
                        self.ugenotethisweek = self._minUddannelse.ugeBrev(
                            self._session,
                            token,
                            thisweek,
                            self._childuserids,
                            self._institutionProfiles,
                            "mitid_user",
                        )
                    except:
                        self.ugenotethisweek = {}

                    try:
                        self.ugenotenextweek = self._minUddannelse.ugeBrev(
                            self._session,
                            token,
                            nextweek,
                            self._childuserids,
                            self._institutionProfiles,
                            "mitid_user",
                        )
                    except:
                        self.ugenotenextweek = {}

        # End of Min Uddannelse Uge Note

        # Ugeplaner:
        if self._ugeplan is True:
            guardian = self._session.get(
                self.apiurl + "?method=profiles.getProfileContext&portalrole=guardian",
                verify=True,
            ).json()["data"]["userId"]
            childUserIds = ",".join(self._childuserids)

            if len(self.widgets) == 0:
                self.get_widgets()

            # Check for widget availability with kindergarten awareness
            has_ugeplan_widgets = (
                "0029" in self.widgets
                or "0004" in self.widgets
                or "0062" in self.widgets
            )

            if not has_ugeplan_widgets:
                kindergarten_count = sum(
                    1
                    for child_id in self._institution_types
                    if self._institution_types[child_id] == "kindergarten"
                )
                if kindergarten_count > 0:
                    _LOGGER.info(
                        f"Week plan widgets (0029,0004,0062) not found - detected {kindergarten_count} kindergarten child(ren). Week plans may use different widgets or be unavailable for kindergartens."
                    )
                else:
                    _LOGGER.error(
                        "You have enabled ugeplaner, but we cannot find any matching widgets (0029,0004,0062) in Aula."
                    )

            if "0029" in self.widgets and "0004" in self.widgets:
                _LOGGER.warning(
                    "Multiple sources for ugeplaner is untested and might cause problems."
                )

            def ugeplan(week, thisnext):
                ugeplan_data_found = False

                if "0029" in self.widgets:
                    token = self.get_token("0029")
                    get_payload = (
                        "/ugebrev?assuranceLevel=2&childFilter="
                        + childUserIds
                        + "&currentWeekNumber="
                        + week
                        + "&isMobileApp=false&placement=narrow&sessionUUID="
                        + guardian
                        + "&userProfile=guardian"
                    )
                    ugeplaner = requests.get(
                        MIN_UDDANNELSE_API + get_payload,
                        headers={"Authorization": token, "accept": "application/json"},
                        verify=True,
                    )
                    # _LOGGER.debug("ugeplaner status_code "+str(ugeplaner.status_code))
                    # _LOGGER.debug("ugeplaner response "+str(ugeplaner.text))
                    for person in ugeplaner.json()["personer"]:
                        ugeplan = person["institutioner"][0]["ugebreve"][0]["indhold"]
                        if thisnext == "this":
                            self.ugep_attr[person["navn"].split()[0]] = ugeplan
                        elif thisnext == "next":
                            self.ugepnext_attr[person["navn"].split()[0]] = ugeplan
                        ugeplan_data_found = True

                if "0062" in self.widgets:
                    _LOGGER.debug("In the Huskelisten flow...")
                    token = self.get_token("0062", False)
                    huskelisten_headers = {
                        "Accept": "application/json, text/plain, */*",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
                        "Aula-Authorization": token,
                        "Origin": "https://www.aula.dk",
                        "Referer": "https://www.aula.dk/",
                        "Sec-Fetch-Dest": "empty",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "cross-site",
                        "User-Agent": "Mozilla/5.0 (X11; CrOS x86_64 15183.51.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
                        "zone": "Europe/Copenhagen",
                    }

                    children = "&children=".join(self._childuserids)
                    institutions = "&institutions=".join(self._institutionProfiles)
                    timedelta = datetime.datetime.now() + datetime.timedelta(days=180)
                    From = datetime.datetime.now().strftime("%Y-%m-%d")
                    dueNoLaterThan = timedelta.strftime("%Y-%m-%d")
                    get_payload = (
                        "/reminders/v1?children="
                        + children
                        + "&from="
                        + From
                        + "&dueNoLaterThan="
                        + dueNoLaterThan
                        + "&widgetVersion=1.10&userProfile=guardian&sessionId="
                        + "mitid_user"
                        + "&institutions="
                        + institutions
                    )
                    _LOGGER.debug(
                        "Huskelisten get_payload: " + SYSTEMATIC_API + get_payload
                    )
                    #
                    mock_huskelisten = 0
                    #
                    if mock_huskelisten == 1:
                        _LOGGER.warning("Using mock data for Huskelisten.")
                        mock_huskelisten = '[{"userName":"Test Student 1","userId":100001,"courseReminders":[],"assignmentReminders":[],"teamReminders":[{"id":70001,"institutionName":"Test School","institutionId":100,"dueDate":"2022-11-29T23:00:00Z","teamId":60001,"teamName":"2A","reminderText":"Matematik lektier: Løs opgaver.","createdBy":"Teacher 1","lastEditBy":"Teacher 1","subjectName":"Matematik"},{"id":70002,"institutionName":"Test School","institutionId":100,"dueDate":"2022-12-06T23:00:00Z","teamId":60001,"teamName":"2A","reminderText":"Matematik opgave: Løs dagens opgave.","createdBy":"Teacher 1","lastEditBy":"Teacher 2","subjectName":"Matematik"},{"id":70003,"institutionName":"Test School","institutionId":100,"dueDate":"2022-12-13T23:00:00Z","teamId":60001,"teamName":"2A","reminderText":"Matematik opgave: Løs dagens opgave.","createdBy":"Teacher 1","lastEditBy":"Teacher 1","subjectName":"Matematik"},{"id":70004,"institutionName":"Test School","institutionId":100,"dueDate":"2022-12-20T23:00:00Z","teamId":60001,"teamName":"2A","reminderText":"Matematik opgave: Løs dagens opgave.","createdBy":"Teacher 2","lastEditBy":"Teacher 2","subjectName":"Matematik"}]},{"userName":"Test Student 2","userId":100002,"courseReminders":[],"assignmentReminders":[{"id":0,"institutionName":"Test School","institutionId":100,"dueDate":"2022-12-08T11:00:00Z","courseId":200001,"teamNames":["5A","5B"],"teamIds":[60002,60003],"courseSubjects":[],"assignmentId":500001,"assignmentText":"Skriv en opgave"}],"teamReminders":[{"id":70005,"institutionName":"Test School","institutionId":100,"dueDate":"2022-11-30T23:00:00Z","teamId":60003,"teamName":"5A","reminderText":"Læse opgave fra bog.","createdBy":"Teacher 3","lastEditBy":"Teacher 3","subjectName":"Dansk"}]},{"userName":"Test Student 3","userId":100003,"courseReminders":[],"assignmentReminders":[],"teamReminders":[]}]'
                        data = json.loads(mock_huskelisten, strict=False)
                    else:
                        response = requests.get(
                            SYSTEMATIC_API + get_payload,
                            headers=huskelisten_headers,
                            verify=True,
                        )
                        try:
                            data = json.loads(response.text, strict=False)
                        except:
                            _LOGGER.error(
                                "Could not parse the response from Huskelisten as json."
                            )
                        # _LOGGER.debug("Huskelisten raw response: "+str(response.text))

                    for person in data:
                        name = person["userName"].split()[0]
                        _LOGGER.debug("Huskelisten for " + name)
                        huskel = ""
                        reminders = person["teamReminders"]
                        if len(reminders) > 0:
                            for reminder in reminders:
                                mytime = datetime.datetime.strptime(
                                    reminder["dueDate"], "%Y-%m-%dT%H:%M:%SZ"
                                )
                                ftime = mytime.strftime("%A %d. %B")
                                huskel = huskel + "<h3>" + ftime + "</h3>"
                                huskel = (
                                    huskel
                                    + "<b>"
                                    + reminder["subjectName"]
                                    + "</b><br>"
                                )
                                huskel = (
                                    huskel + "af " + reminder["createdBy"] + "<br><br>"
                                )
                                content = re.sub(
                                    r"([0-9]+)(\.)", r"\1\.", reminder["reminderText"]
                                )
                                huskel = huskel + content + "<br><br>"
                        else:
                            huskel = huskel + str(name) + " har ingen påmindelser."
                        self.huskeliste[name] = huskel

                # End Huskelisten
                if "0004" in self.widgets:
                    # Try Meebook:
                    _LOGGER.debug("In the Meebook flow...")
                    token = self.get_token("0004")
                    # _LOGGER.debug("Token "+token)
                    headers = {
                        "authority": "app.meebook.com",
                        "accept": "application/json",
                        "authorization": token,
                        "dnt": "1",
                        "origin": "https://www.aula.dk",
                        "referer": "https://www.aula.dk/",
                        "sessionuuid": "mitid_session",
                        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36",
                        "x-version": "1.0",
                    }
                    childFilter = "&childFilter[]=".join(self._childuserids)
                    institutionFilter = "&institutionFilter[]=".join(
                        self._institutionProfiles
                    )
                    get_payload = (
                        "/relatedweekplan/all?currentWeekNumber="
                        + week
                        + "&userProfile=guardian&childFilter[]="
                        + childFilter
                        + "&institutionFilter[]="
                        + institutionFilter
                    )

                    mock_meebook = 0
                    if mock_meebook == 1:
                        _LOGGER.warning("Using mock data for Meebook ugeplaner.")
                        mock_meebook = '[{"id":490000,"name":"Emilie efternavn","unilogin":"lud...","weekPlan":[{"date":"mandag 28. nov.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"I denne uge er der omlagt uge p\u00e5 hele skolen.\n\nMandag har vi \nKlippeklistredag:\n\nMan m\u00e5 gerne have nissehuer p\u00e5 :)\n\nMedbring gerne en god saks, limstift, skabeloner mm. \n\nB\u00f8rnene skal ogs\u00e5 medbringe et vasket syltet\u00f8jsglas eller lign., som vi skal male p\u00e5. S\u00f8rg gerne for at der ikke er m\u00e6rker p\u00e5:-)\n\n1. lektion: Morgenb\u00e5nd med l\u00e6sning/opgaver\n\n2. lektion: \nVi laver f\u00e6lles julenisser efter en bestemt skabelon.\n\n3. - 5. lektion: \nVi julehygger med musik og kreative projekter. Vi pynter vores f\u00e6lles juletr\u00e6, og synger julesange. \n\n6. lektion:\nAfslutning og oprydning.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]},{"date":"tirsdag 29. nov.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Omlagt uge:\n\n1. lektion\nMorgenb\u00e5nd med l\u00e6sning og opgaver.\n\n2. lektion\nVi starter p\u00e5 storylineforl\u00f8b om jul. Vi taler om nisser og danner nissefamilier i klassen.\n\n3.-5. lektion\nVi lave et juleprojekt med filt...\n\n6. lektion\nVi arbejder med en kreativ opgave om v\u00e5benskold.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]},{"date":"onsdag 30. nov.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Omlagt uge:\n\n1. -2. lektion\nVi skal til foredrag med SOS B\u00f8rnebyerne om omvendt julekalender.\n\n3-4. lektion\nVi skriver nissehistorier om nissefamilierne.\n\n5.-6. lektion\nVi laver jule-postel\u00f8b, hvor posterne skal l\u00e6ses med en kodel\u00e6ser.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]},{"date":"torsdag 1. dec.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Omlagt uge:\n\n1. lektion\nMorgenb\u00e5nd med l\u00e6sning og opgaver. \nVi arbejder med l\u00e6s og forst\u00e5 i en julehistorie.\n\n2.-5. lektion\nVi skal arbejde med et kreativt juleprojekt, hvor der laves huse til nisserne.\n\n6. lektion\nSe SOS b\u00f8rnebyernes julekalender og afrunding af dagen.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]},{"date":"fredag 2. dec.","tasks":[{"id":3069630,"type":"comment","author":"Met...","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"1. lektion\nMorgenb\u00e5nd med l\u00e6sning og opgaver samt julehygge, hvor vi l\u00e6ser julehistorie \n\n2. lektion:\nVi skal lave et julerim og skrive det ind p\u00e5 en flot julenisse samt tegne nissen. \n\n3.-4. lektion\nVi skal lave jule-postel\u00f8b p\u00e5 skolen. \n\n5.. lektion\nVi skal l\u00f8se et hemmeligt kodebrev ved hj\u00e6lp af en kodel\u00e6ser. \n\nVi evaluerer og afrunder ugen.","editUrl":"https://app.meebook.com//arsplaner/dlap//956783//202248"}]}]},{"id":630000,"name":"Ann...","unilogin":"ann...","weekPlan":[{"date":"mandag 28. nov.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"I dag skal vi h\u00f8re om jul i Norge og lave Norsk julepynt.\nEfter 12 pausen skal vi h\u00f8re om julen i Danmark f\u00f8r juletr\u00e6et og andestegen.\nVi skal farvel\u00e6gge g\u00e5rdnisserne der passede p\u00e5 g\u00e5rdene i gamle dage.","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]},{"date":"tirsdag 29. nov.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"I dag skal vi arbejde med julen i Gr\u00f8nland og lave gr\u00f8nlandske julehuse.\nEfter 12 pausen skal vi h\u00f8re om JUletr\u00e6et der flytter ind i de danske stuer. Vi skal tale om hvor det stammer fra og hvad der var p\u00e5 juletr\u00e6et i gamle dage . Blandt andet den spiselige pynt.\nVi taler om Peters jul og at der ikke altid har v\u00e6ret en stjerne i toppen. Vi klipper storke til juletr\u00e6stoppen","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]},{"date":"onsdag 30. nov.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"I dag st\u00e5r den p\u00e5 Jul i Finland og finske juletraditioner. Vi klipper finske julestjerner.\nEfter pausen skal vi arbejde videre med jul og julepynt gennem tiden i dk. \nVi skal tale om hvorfor der er flag, trompeter og trommer p\u00e5 tr\u00e6et (krigen i 1864) og vi skal lave gammeldags silkeroser og musetrapper til tr\u00e6et","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]},{"date":"torsdag 1. dec.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"I dag skal vi p\u00e5 en juletur med hygge og posl\u00f8b til trylleskoven \nBussen k\u00f8rer os derud kl 10 og vi er senest tilbage n\u00e5r skoledagen slutter .\nHusk at f\u00e5 varmt praktisk t\u00f8j p\u00e5 og en turtaske med en let tilg\u00e6ngelig madpakke der kan spises i det fri. Regnbukser eller overtr\u00e6ksbukser s\u00e5 man kan sidde p\u00e5 jorden.","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]},{"date":"fredag 2. dec.","tasks":[{"id":3090189,"type":"comment","author":"May...","group":"0C (22/23)","pill":"B\u00f8rnehaveklasse, B\u00f8rnehaveklassen, Dansk, Matematik","content":"Klippe/ klistre dag .\nHusk at tage lim, saks og kaffe m.m., kop og tallerkner med hjemmefra. Hvis i tager kage med er det til en buffet i klassen.","editUrl":"https://app.meebook.com//arsplaner/dlap//899210//202248"}]}]}]'
                        data = json.loads(mock_meebook, strict=False)
                    else:
                        response = requests.get(
                            MEEBOOK_API + get_payload, headers=headers, verify=True
                        )
                        data = json.loads(response.text, strict=False)
                        # _LOGGER.debug("Meebook ugeplan raw response from week "+week+": "+str(response.text))

                    for person in data:
                        _LOGGER.debug("Meebook ugeplan for " + person["name"])
                        ugep = ""
                        ugeplan = person["weekPlan"]
                        for day in ugeplan:
                            ugep = ugep + "<h3>" + day["date"] + "</h3>"
                            if len(day["tasks"]) > 0:
                                for task in day["tasks"]:
                                    if not task["pill"] == "Ingen fag tilknyttet":
                                        ugep = ugep + "<b>" + task["pill"] + "</b><br>"
                                    ugep = ugep + task["author"] + "<br><br>"
                                    content = re.sub(
                                        r"([0-9]+)(\.)", r"\1\.", task["content"]
                                    )
                                    ugep = ugep + content + "<br><br>"
                            else:
                                ugep = ugep + "-"
                        try:
                            name = person["name"].split()[0]
                        except:
                            name = person["name"]
                        if thisnext == "this":
                            self.ugep_attr[name] = ugep
                        elif thisnext == "next":
                            self.ugepnext_attr[name] = ugep

            now = datetime.datetime.now() + datetime.timedelta(weeks=1)
            thisweek = datetime.datetime.now().strftime("%Y-W%W")
            nextweek = now.strftime("%Y-W%W")
            ugeplan(thisweek, "this")
            ugeplan(nextweek, "next")
            # _LOGGER.debug("End result of ugeplan object: "+str(self.ugep_attr))
        # End of Ugeplaner

        # Posts (Indlæg):
        _LOGGER.info("POSTS: About to call _get_posts()...")
        try:
            self._get_posts()
            _LOGGER.info("POSTS: _get_posts() completed successfully")
        except Exception as e:
            _LOGGER.error(f"POSTS: _get_posts() failed with error: {e}")
            import traceback

            traceback.print_exc()
            # Initialize empty posts data on error
            self.posts = {}
            self.posts_by_child = {}

        # Debug checkpoint before mail section
        _LOGGER.info("DEBUG: About to start mail section...")

        # Test mail API first
        _LOGGER.info("MAIL TEST: Running simple mail API test...")
        self.test_mail_api_simple()

        # Mail Threads:
        _LOGGER.info("MAIL: About to call _get_mail()...")
        try:
            self._get_mail()
            _LOGGER.info("MAIL: _get_mail() completed successfully")
            _LOGGER.info(
                f"MAIL: Retrieved {len(getattr(self, 'mail_threads', {}))} total mail threads"
            )
            _LOGGER.info(
                f"MAIL: Mail distribution: {[(k, len(v)) for k, v in getattr(self, 'mail_by_child', {}).items()]}"
            )
        except Exception as e:
            _LOGGER.error(f"MAIL: _get_mail() failed with error: {e}")
            _LOGGER.error(f"MAIL: Exception type: {type(e).__name__}")
            import traceback

            traceback.print_exc()
            # Initialize empty mail data on error
            self.mail_threads = {}
            self.mail_by_child = {}

    def _get_presence_templates(self):
        """Get weekly schedule presence templates (current and next week)"""
        try:
            # Get current and next week in the required format
            current_week = datetime.datetime.now().strftime("%Y-W%W")
            next_week_date = datetime.datetime.now() + datetime.timedelta(weeks=1)
            next_week = next_week_date.strftime("%Y-W%W")

            _LOGGER.debug(
                f"Fetching presence templates for weeks: {current_week}, {next_week}"
            )

            # Get presence templates for current week
            try:
                response_current = self._session.get(
                    self.apiurl
                    + f"?method=presence.getPresenceTemplates&week={current_week}&childIds[]="
                    + "&childIds[]=".join(self._childids),
                    verify=True,
                    timeout=10,
                ).json()

                if (
                    response_current.get("status", {}).get("message") == "OK"
                    and "data" in response_current
                ):
                    self.presence_templates = response_current["data"]
                    _LOGGER.debug(
                        f"Retrieved presence templates for current week: {len(self.presence_templates)} entries"
                    )
                else:
                    _LOGGER.warning(
                        "No presence template data available for current week"
                    )
                    self.presence_templates = {}

            except Exception as e:
                _LOGGER.error(f"Error fetching current week presence templates: {e}")
                self.presence_templates = {}

            # Get presence templates for next week
            try:
                response_next = self._session.get(
                    self.apiurl
                    + f"?method=presence.getPresenceTemplates&week={next_week}&childIds[]="
                    + "&childIds[]=".join(self._childids),
                    verify=True,
                    timeout=10,
                ).json()

                if (
                    response_next.get("status", {}).get("message") == "OK"
                    and "data" in response_next
                ):
                    self.presence_templates_next = response_next["data"]
                    _LOGGER.debug(
                        f"Retrieved presence templates for next week: {len(self.presence_templates_next)} entries"
                    )
                else:
                    _LOGGER.warning("No presence template data available for next week")
                    self.presence_templates_next = {}

            except Exception as e:
                _LOGGER.error(f"Error fetching next week presence templates: {e}")
                self.presence_templates_next = {}

        except Exception as e:
            _LOGGER.error(f"Error in _get_presence_templates: {e}")
            self.presence_templates = {}
            self.presence_templates_next = {}

    def _get_closed_days(self):
        """Get closed days (lukkedage) for all institutions"""
        try:
            _LOGGER.debug("Fetching closed days for institutions...")

            if not self._institutionProfiles:
                _LOGGER.warning("No institution profiles available for closed days")
                self.closed_days = {}
                return

            # Build URL with institutionCodes[] parameters
            institution_params = "&".join(
                [f"institutionCodes[]={code}" for code in self._institutionProfiles]
            )

            url = f"{self.apiurl}?method=presence.getClosedDays&{institution_params}"
            _LOGGER.debug(f"Fetching closed days from: {url}")

            try:
                response = self._session.get(url, verify=True, timeout=10)

                if response.status_code == 200:
                    data = response.json()

                    if data.get("status", {}).get("message") == "OK" and "data" in data:
                        # Process institutionClosedDays array from response
                        institution_closed_days = data["data"].get(
                            "institutionClosedDays", []
                        )

                        # Clear existing data
                        self.closed_days = {}

                        # Process each institution's closed days
                        for institution_data in institution_closed_days:
                            institution_code = institution_data.get(
                                "institutionCode", ""
                            )
                            closed_days_overview = institution_data.get(
                                "closedDaysOverview", {}
                            )
                            closed_days_list = closed_days_overview.get(
                                "closedDays", []
                            )

                            if institution_code:
                                self.closed_days[institution_code] = closed_days_list
                                _LOGGER.debug(
                                    f"Retrieved {len(closed_days_list)} closed days for institution {institution_code}"
                                )

                        _LOGGER.debug(
                            f"Successfully fetched closed days for {len(self.closed_days)} institutions"
                        )
                    else:
                        _LOGGER.warning(
                            f"Failed to fetch closed days: {data.get('status', {})}"
                        )
                        self.closed_days = {}
                else:
                    _LOGGER.warning(
                        f"HTTP error fetching closed days: {response.status_code}"
                    )
                    self.closed_days = {}

            except Exception as e:
                _LOGGER.error(f"Error fetching closed days: {e}")
                self.closed_days = {}

        except Exception as e:
            _LOGGER.error(f"Error in _get_closed_days: {e}")
            self.closed_days = {}

    def _get_weekly_presence(self):
        """Get weekly presence data (komme og gå) dynamically for current and next week"""
        import datetime

        try:
            _LOGGER.info(
                "WEEKLY PRESENCE: Starting to fetch dynamic weekly presence data..."
            )

            # Initialize weekly presence data
            self.weekly_presence_current = {}
            self.weekly_presence_next = {}

            # Get current date
            today = datetime.date.today()

            # Calculate current week (Monday = 0, Sunday = 6)
            current_monday = today - datetime.timedelta(days=today.weekday())
            current_sunday = current_monday + datetime.timedelta(days=6)

            # Calculate next week
            next_monday = current_monday + datetime.timedelta(days=7)
            next_sunday = next_monday + datetime.timedelta(days=6)

            _LOGGER.info(
                f"WEEKLY PRESENCE: Current week: {current_monday} to {current_sunday}"
            )
            _LOGGER.info(f"WEEKLY PRESENCE: Next week: {next_monday} to {next_sunday}")

            # Get child profile IDs dynamically from profile context
            child_profile_ids = self._get_dynamic_child_profile_ids()

            if not child_profile_ids:
                _LOGGER.warning(
                    "WEEKLY PRESENCE: No child profile IDs found for presence data"
                )
                return

            _LOGGER.info(
                f"WEEKLY PRESENCE: Using dynamic child profile IDs: {child_profile_ids}"
            )

            # Fetch current week data
            _LOGGER.info("WEEKLY PRESENCE: Fetching current week data...")
            current_week_data = self._get_presence_templates_for_week(
                current_monday, current_sunday, child_profile_ids
            )
            if current_week_data:
                self.weekly_presence_current = current_week_data
                _LOGGER.info(
                    f"WEEKLY PRESENCE: Current week: Added data for {len(current_week_data)} children"
                )
                for child_id, days in current_week_data.items():
                    _LOGGER.info(
                        f"WEEKLY PRESENCE: Child {child_id} has {len(days)} days"
                    )
            else:
                _LOGGER.warning("WEEKLY PRESENCE: No current week data returned")

            # Fetch next week data
            _LOGGER.info("WEEKLY PRESENCE: Fetching next week data...")
            next_week_data = self._get_presence_templates_for_week(
                next_monday, next_sunday, child_profile_ids
            )
            if next_week_data:
                self.weekly_presence_next = next_week_data
                _LOGGER.info(
                    f"WEEKLY PRESENCE: Next week: Added data for {len(next_week_data)} children"
                )
                for child_id, days in next_week_data.items():
                    _LOGGER.info(
                        f"WEEKLY PRESENCE: Child {child_id} has {len(days)} days"
                    )
            else:
                _LOGGER.warning("WEEKLY PRESENCE: No next week data returned")

            _LOGGER.info(
                f"WEEKLY PRESENCE: Completed - Current week ({len(self.weekly_presence_current)} children), Next week ({len(self.weekly_presence_next)} children)"
            )

        except Exception as e:
            _LOGGER.error(
                f"WEEKLY PRESENCE: Error in _get_weekly_presence: {e}", exc_info=True
            )
            self.weekly_presence_current = {}
            self.weekly_presence_next = {}

    def _get_dynamic_child_profile_ids(self):
        """Get child profile IDs dynamically from available data sources"""
        child_profile_ids = []

        try:
            _LOGGER.info("CHILD IDS: Starting to get dynamic child profile IDs...")

            # First, try to get from _childnames (most reliable) - these are the 'id' fields
            if hasattr(self, "_childnames") and self._childnames:
                child_ids_from_names = list(self._childnames.keys())
                child_profile_ids.extend(child_ids_from_names)
                _LOGGER.info(
                    f"CHILD IDS: Found {len(child_ids_from_names)} child IDs from _childnames: {child_ids_from_names}"
                )
                for child_id in child_ids_from_names:
                    _LOGGER.info(
                        f"CHILD IDS: Child {child_id} -> {self._childnames[child_id]}"
                    )
            else:
                _LOGGER.warning("CHILD IDS: No _childnames found")

            # ONLY use _childnames - it has the correct 'id' fields we need for API
            # Remove duplicates and convert to strings
            child_profile_ids = list(
                set([str(pid) for pid in child_profile_ids if pid])
            )

            _LOGGER.info(
                f"CHILD IDS: Final child profile IDs for API (deduplicated): {child_profile_ids}"
            )

        except Exception as e:
            _LOGGER.error(
                f"CHILD IDS: Error getting dynamic child profile IDs: {e}",
                exc_info=True,
            )

        return child_profile_ids

    def _get_presence_templates_for_week(self, start_date, end_date, child_profile_ids):
        """Fetch presence templates for a week using getPresenceTemplates API"""
        try:
            # Format dates as YYYY-MM-DD
            from_date = start_date.strftime("%Y-%m-%d")
            to_date = end_date.strftime("%Y-%m-%d")

            # Build URL with child profile IDs - these are the institutionProfile 'id' fields
            url_params = f"method=presence.getPresenceTemplates&fromDate={from_date}&toDate={to_date}"
            for profile_id in child_profile_ids:
                url_params += f"&filterInstitutionProfileIds[]={profile_id}"

            full_url = f"{self.apiurl}?{url_params}"

            response = self._session.get(
                full_url,
                verify=True,
                timeout=15,
            )

            _LOGGER.info(f"PRESENCE API CALL: {full_url}")
            _LOGGER.info(f"Response status: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                _LOGGER.info(
                    f"API response keys: {list(result.keys()) if isinstance(result, dict) else type(result)}"
                )
                _LOGGER.info(f"API status: {result.get('status', {})}")

                if result.get("status", {}).get("message") == "OK":
                    data = result.get("data", {})
                    _LOGGER.info(
                        f"Data keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
                    )

                    if "presenceWeekTemplates" in data:
                        templates = data["presenceWeekTemplates"]
                        _LOGGER.info(f"Found {len(templates)} presence week templates")
                        for i, template in enumerate(templates):
                            institution_profile = template.get("institutionProfile", {})
                            child_id = institution_profile.get("id")
                            child_name = institution_profile.get("name", "Unknown")
                            day_templates = template.get("dayTemplates", [])
                            _LOGGER.info(
                                f"Template {i}: Child {child_name} (ID: {child_id}) - {len(day_templates)} days"
                            )
                    else:
                        _LOGGER.warning("No presenceWeekTemplates in API response")

                    return self._parse_presence_templates_data(
                        data, start_date, end_date
                    )
                else:
                    _LOGGER.warning(
                        f"API returned non-OK status for presence templates: {result.get('status', {})}"
                    )
            else:
                _LOGGER.warning(
                    f"API request failed {response.status_code} for presence templates"
                )
                _LOGGER.warning(f"Response text: {response.text[:500]}")

        except Exception as e:
            _LOGGER.error(
                f"Error fetching presence templates for week {start_date} to {end_date}: {e}"
            )

        return {}

    def _parse_presence_templates_data(self, data, start_date, end_date):
        """Parse presence templates data from getPresenceTemplates API response"""
        parsed_data = {}

        try:
            _LOGGER.info("PARSING: Start parsing presence templates data")

            # Check if we have presenceWeekTemplates in the data
            if "presenceWeekTemplates" in data:
                presence_templates = data["presenceWeekTemplates"]
                _LOGGER.info(
                    f"PARSING: Found {len(presence_templates)} presence week templates"
                )

                # Process each child's template
                for template in presence_templates:
                    # Extract child information
                    institution_profile = template.get("institutionProfile", {})
                    child_id = str(institution_profile.get("id", ""))
                    profile_id = str(institution_profile.get("profileId", ""))
                    child_name = institution_profile.get("name", "")
                    short_name = institution_profile.get("shortName", "")
                    institution_name = institution_profile.get("institutionName", "")
                    institution_code = institution_profile.get("institutionCode", "")

                    _LOGGER.info(
                        f"PARSING: Processing child {child_name} (ID: {child_id}, Profile: {profile_id})"
                    )

                    if not child_id:
                        _LOGGER.warning(f"PARSING: No child_id found for {child_name}")
                        continue

                    # Initialize child data
                    parsed_data[child_id] = {}
                    daily_count = 0

                    # Process day templates
                    day_templates = template.get("dayTemplates", [])
                    _LOGGER.info(
                        f"PARSING: Processing {len(day_templates)} day templates for {child_name}"
                    )

                    for day_template in day_templates:
                        by_date = day_template.get("byDate")
                        if not by_date:
                            continue

                        # Parse the date
                        try:
                            from datetime import datetime

                            date_obj = datetime.strptime(by_date, "%Y-%m-%d").date()
                            date_str = date_obj.isoformat()
                            day_name = date_obj.strftime("%A")
                        except Exception as date_error:
                            _LOGGER.warning(
                                f"PARSING: Could not parse date {by_date}: {date_error}"
                            )
                            continue

                        # Check if this date is in our target range
                        if not (start_date <= date_obj <= end_date):
                            _LOGGER.debug(
                                f"PARSING: Date {date_str} not in range {start_date} to {end_date}"
                            )
                            continue

                        # Extract presence information
                        is_on_vacation = day_template.get("isOnVacation", False)
                        vacation_info = day_template.get("vacation", {})
                        entry_time = day_template.get("entryTime")
                        exit_time = day_template.get("exitTime")
                        exit_with = day_template.get("exitWith")

                        presence_info = {
                            "date": date_str,
                            "day_name": day_name,
                            "check_in_time": None,  # Not available in templates
                            "check_out_time": None,  # Not available in templates
                            "planned_entry_time": entry_time,
                            "planned_exit_time": exit_time,
                            "exit_with": exit_with,
                            "status": "vacation" if is_on_vacation else "scheduled",
                            "comment": day_template.get("comment", ""),
                            "location": "",
                            "activity_type": day_template.get("activityType"),
                            "is_default_entry": day_template.get(
                                "isDefaultEntryTime", False
                            ),
                            "is_default_exit": day_template.get(
                                "isDefaultExitTime", False
                            ),
                            "is_on_vacation": is_on_vacation,
                            "vacation_title": vacation_info.get("title", "")
                            if vacation_info
                            else "",
                            "vacation_description": vacation_info.get(
                                "description", {}
                            ).get("html", "")
                            if vacation_info
                            else "",
                            "institution_name": institution_name,
                            "institution_code": institution_code,
                            "main_group": institution_profile.get("mainGroup", {}).get(
                                "name", ""
                            )
                            if institution_profile.get("mainGroup")
                            else "",
                            "child_name": child_name,
                            "short_name": short_name,
                            "profile_id": profile_id,
                        }

                        parsed_data[child_id][date_str] = presence_info
                        daily_count += 1

                        _LOGGER.info(
                            f"PARSING: {child_name} {date_str} ({day_name}): entry={entry_time}, exit={exit_time}, vacation={is_on_vacation}"
                        )

                _LOGGER.info(
                    f"PARSING: Successfully parsed presence data for {len(parsed_data)} children"
                )
                for child_id, child_data in parsed_data.items():
                    _LOGGER.info(
                        f"PARSING: Child {child_id} has {len(child_data)} days of data"
                    )

            else:
                _LOGGER.warning(
                    "PARSING: No presenceWeekTemplates found in API response"
                )

        except Exception as e:
            _LOGGER.error(
                f"PARSING: Error parsing presence templates data: {e}", exc_info=True
            )

        return parsed_data

    def _get_posts(self):
        """Fetch posts/news (indlæg) from Aula with pagination"""
        try:
            _LOGGER.info("POSTS: Starting to fetch posts data...")

            # Initialize posts data
            self.posts = {}
            self.posts_by_child = {}

            # Initialize mail data
            self.mail_threads = {}
            self.mail_by_child = {}

            # Build filter parameters for institution profiles
            filter_params = ""
            if self._institutionProfileIdsList:
                filter_params = "&" + "&".join(
                    [
                        f"institutionProfileIds[]={profile_id}"
                        for profile_id in self._institutionProfileIdsList
                    ]
                )
                _LOGGER.info(
                    f"POSTS: Using institution profile IDs: {self._institutionProfileIdsList}"
                )
            else:
                _LOGGER.warning(
                    "POSTS: No institution profile IDs available, fetching all posts"
                )

            all_posts = []

            # Fetch multiple pages to get the last 50 posts
            for index in [
                0,
                10,
                20,
                30,
                40,
            ]:  # Fetch 5 pages of 10 posts each = 50 posts
                try:
                    posts_url = f"{self.apiurl}?method=posts.getAllPosts&parent=profile&index={index}&limit=10{filter_params}"
                    _LOGGER.debug(
                        f"POSTS: Fetching page {index // 10 + 1} from: {posts_url}"
                    )

                    response = self._session.get(posts_url, verify=True, timeout=15)
                    _LOGGER.debug(
                        f"POSTS: Page {index // 10 + 1} response status: {response.status_code}"
                    )

                    if response.status_code == 200:
                        result = response.json()
                        if result.get("status", {}).get("message") == "OK":
                            posts_data = result.get("data", {}).get("posts", [])
                            if posts_data:
                                all_posts.extend(posts_data)
                                _LOGGER.info(
                                    f"POSTS: Retrieved {len(posts_data)} posts from page {index // 10 + 1}"
                                )
                            else:
                                _LOGGER.info(
                                    f"POSTS: No more posts at index {index}, stopping pagination"
                                )
                                break
                        else:
                            _LOGGER.warning(
                                f"POSTS: API returned non-OK status at index {index}: {result.get('status', {})}"
                            )
                            break
                    else:
                        _LOGGER.warning(
                            f"POSTS: API request failed at index {index} with status {response.status_code}"
                        )
                        break

                except Exception as page_error:
                    _LOGGER.error(
                        f"POSTS: Error fetching page at index {index}: {page_error}"
                    )
                    break

            _LOGGER.info(
                f"POSTS: Retrieved total of {len(all_posts)} posts across all pages"
            )

            if all_posts:
                self._parse_posts_data(all_posts)
                _LOGGER.info(f"POSTS: Successfully processed {len(self.posts)} posts")
                _LOGGER.info(
                    f"POSTS: Posts distribution by child: {[(child_id, len(posts)) for child_id, posts in self.posts_by_child.items()]}"
                )
            else:
                _LOGGER.warning("POSTS: No posts data found across all pages")

        except Exception as e:
            _LOGGER.error(f"POSTS: Error in _get_posts: {e}", exc_info=True)
            self.posts = {}
            self.posts_by_child = {}

    def _parse_posts_data(self, posts_data):
        """Parse posts data from getAllPosts API response"""
        try:
            _LOGGER.info("POSTS: Starting to parse posts data")

            self.posts = {}
            self.posts_by_child = {}

            for post in posts_data:
                post_id = str(post.get("id", ""))
                if not post_id:
                    continue

                # Extract basic post information
                parsed_post = {
                    "id": post_id,
                    "title": post.get("title", ""),
                    "content": self._extract_post_content(post.get("content", {})),
                    "timestamp": post.get("timestamp", ""),
                    "publish_at": post.get("publishAt", ""),
                    "expire_at": post.get("expireAt", ""),
                    "is_important": post.get("isImportant", False),
                    "important_from": post.get("importantFrom"),
                    "important_to": post.get("importantTo"),
                    "owner": self._extract_post_owner(post.get("ownerProfile", {})),
                    "attachments": self._extract_post_attachments(
                        post.get("attachments", [])
                    ),
                    "shared_with_groups": self._extract_shared_groups(
                        post.get("sharedWithGroups", [])
                    ),
                    "related_profiles": self._extract_related_profiles(
                        post.get("relatedProfiles", [])
                    ),
                    "comment_count": post.get("commentCount", 0),
                    "allow_comments": post.get("allowComments", False),
                    "edited_at": post.get("editedAt"),
                }

                # Store post in main posts dictionary
                self.posts[post_id] = parsed_post

                # Group posts by related child profiles
                related_profiles = post.get("relatedProfiles", [])
                for profile in related_profiles:
                    child_id = str(profile.get("id", ""))
                    if child_id:
                        if child_id not in self.posts_by_child:
                            self.posts_by_child[child_id] = []
                        self.posts_by_child[child_id].append(parsed_post)

                _LOGGER.debug(
                    f"POSTS: Parsed post {post_id}: '{parsed_post['title']}' from {parsed_post['owner'].get('name', 'Unknown')}"
                )

            _LOGGER.info(
                f"POSTS: Successfully parsed {len(self.posts)} posts for {len(self.posts_by_child)} children"
            )

        except Exception as e:
            _LOGGER.error(f"POSTS: Error parsing posts data: {e}", exc_info=True)
            self.posts = {}
            self.posts_by_child = {}

    def _extract_post_content(self, content):
        """Extract and clean post content"""
        if isinstance(content, dict):
            html_content = content.get("html", "")
            # Strip HTML tags for clean text
            import re

            clean_content = re.sub(r"<[^>]+>", "", html_content)
            clean_content = re.sub(r"\s+", " ", clean_content).strip()
            return {
                "html": html_content,
                "text": clean_content,
            }
        return {"html": "", "text": ""}

    def _extract_post_owner(self, owner_profile):
        """Extract post owner information"""
        if not owner_profile:
            return {}

        return {
            "id": str(owner_profile.get("id", "")),
            "name": owner_profile.get("fullName", ""),
            "short_name": owner_profile.get("shortName", ""),
            "role": owner_profile.get("role", ""),
            "institution": owner_profile.get("institution", {}).get(
                "institutionName", ""
            ),
            "institution_code": owner_profile.get("institution", {}).get(
                "institutionCode", ""
            ),
            "metadata": owner_profile.get("metadata", ""),
        }

    def _extract_post_attachments(self, attachments):
        """Extract post attachments (files, media, documents)"""
        extracted_attachments = []

        for attachment in attachments:
            attachment_info = {
                "id": str(attachment.get("id", "")),
                "name": attachment.get("name", ""),
                "status": attachment.get("status", ""),
                "type": "unknown",
            }

            # Handle file attachments
            if "file" in attachment and attachment["file"]:
                file_info = attachment["file"]
                attachment_info.update(
                    {
                        "type": "file",
                        "url": file_info.get("url", ""),
                        "created": file_info.get("created", ""),
                        "bucket": file_info.get("bucket", ""),
                        "key": file_info.get("key", ""),
                    }
                )

            # Handle media attachments (images, videos)
            elif "media" in attachment and attachment["media"]:
                media_info = attachment["media"]
                attachment_info.update(
                    {
                        "type": "media",
                        "media_type": media_info.get("mediaType", ""),
                        "url": media_info.get("file", {}).get("url", ""),
                        "thumbnail_url": media_info.get("thumbnailUrl", ""),
                        "duration": media_info.get("duration"),
                        "tags": media_info.get("tags", []),
                    }
                )

            # Handle document attachments
            elif "document" in attachment and attachment["document"]:
                attachment_info.update(
                    {
                        "type": "document",
                        "url": attachment["document"].get("url", ""),
                    }
                )

            # Handle link attachments
            elif "link" in attachment and attachment["link"]:
                attachment_info.update(
                    {
                        "type": "link",
                        "url": attachment["link"].get("url", ""),
                    }
                )

            extracted_attachments.append(attachment_info)

        return extracted_attachments

    def _extract_shared_groups(self, shared_groups):
        """Extract groups the post is shared with"""
        groups = []
        for group in shared_groups:
            groups.append(
                {
                    "id": str(group.get("id", "")),
                    "name": group.get("name", ""),
                    "short_name": group.get("shortName", ""),
                    "institution_code": group.get("institutionCode", ""),
                    "institution_name": group.get("institutionName", ""),
                    "is_main_group": group.get("mainGroup", False),
                    "portal_roles": group.get("portalRoles", []),
                }
            )
        return groups

    def _extract_related_profiles(self, related_profiles):
        """Extract profiles related to the post (usually children)"""
        profiles = []
        for profile in related_profiles:
            profiles.append(
                {
                    "id": str(profile.get("id", "")),
                    "name": profile.get("fullName", ""),
                    "short_name": profile.get("shortName", ""),
                    "role": profile.get("role", ""),
                    "institution_code": profile.get("institution", {}).get(
                        "institutionCode", ""
                    ),
                    "institution_name": profile.get("institution", {}).get(
                        "institutionName", ""
                    ),
                    "main_group": profile.get("mainGroupName", ""),
                }
            )
        return profiles

    def test_mail_api_simple(self):
        """Simple test of mail API for debugging"""
        try:
            _LOGGER.info("MAIL TEST: Testing basic mail API call...")
            if not hasattr(self, "_session") or not self._session:
                _LOGGER.error("MAIL TEST: No session available")
                return False

            url = f"{self.apiurl}?method=messaging.getThreads&sortOn=date&orderDirection=desc&page=0"
            _LOGGER.info(f"MAIL TEST: Calling URL: {url}")

            response = self._session.get(url, verify=True, timeout=10)
            _LOGGER.info(f"MAIL TEST: Response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                _LOGGER.info(
                    f"MAIL TEST: Response status message: {data.get('status', {}).get('message', 'UNKNOWN')}"
                )
                threads = data.get("data", {}).get("threads", [])
                _LOGGER.info(f"MAIL TEST: Found {len(threads)} threads")

                for i, thread in enumerate(threads[:3]):  # Log first 3 threads
                    regarding_children = thread.get("regardingChildren", [])
                    profile_ids = [
                        str(child.get("profileId", "")) for child in regarding_children
                    ]
                    _LOGGER.info(
                        f"MAIL TEST: Thread {i + 1}: '{thread.get('subject', 'NO_SUBJECT')}' - Profile IDs: {profile_ids}"
                    )

                return True
            else:
                _LOGGER.error(
                    f"MAIL TEST: API call failed with status {response.status_code}"
                )
                return False

        except Exception as e:
            _LOGGER.error(f"MAIL TEST: Exception occurred: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _get_mail(self):
        """Fetch mail threads from Aula messaging system"""
        try:
            _LOGGER.info("MAIL: Starting to fetch mail threads...")

            # Debug current session cookies
            current_cookies = self._session.cookies.get_dict() if self._session else {}
            _LOGGER.info(f"MAIL: Current session cookies: {current_cookies}")
            _LOGGER.info(
                f"MAIL: Current profile_change: {current_cookies.get('profile_change', 'NOT_SET')}"
            )

            # Initialize mail data
            self.mail_threads = {}
            self.mail_by_child = {}
            self.mail_child_profile_mapping = {}

            # Fetch multiple pages of mail threads (50 threads total)
            all_threads = []
            for page in range(5):  # Fetch 5 pages of 10 threads each = 50 threads
                try:
                    mail_url = f"{self.apiurl}?method=messaging.getThreads&sortOn=date&orderDirection=desc&page={page}"
                    _LOGGER.debug(f"MAIL: Fetching page {page + 1} from: {mail_url}")

                    response = self._session.get(mail_url, verify=True, timeout=15)
                    _LOGGER.debug(
                        f"MAIL: Page {page + 1} response status: {response.status_code}"
                    )

                    if response.status_code == 200:
                        result = response.json()
                        if result.get("status", {}).get("message") == "OK":
                            threads_data = result.get("data", {}).get("threads", [])
                            if threads_data:
                                all_threads.extend(threads_data)
                                _LOGGER.info(
                                    f"MAIL: Retrieved {len(threads_data)} threads from page {page + 1}"
                                )

                                # Stop if no more messages exist
                                if not result.get("data", {}).get(
                                    "moreMessagesExist", False
                                ):
                                    _LOGGER.info(
                                        f"MAIL: No more threads available after page {page + 1}"
                                    )
                                    break
                            else:
                                _LOGGER.info(
                                    f"MAIL: No threads found on page {page + 1}, stopping pagination"
                                )
                                break
                        else:
                            _LOGGER.warning(
                                f"MAIL: API returned non-OK status on page {page}: {result.get('status', {})}"
                            )
                            break
                    else:
                        _LOGGER.warning(
                            f"MAIL: API request failed on page {page} with status {response.status_code}"
                        )
                        break

                except Exception as page_error:
                    _LOGGER.error(f"MAIL: Error fetching page {page}: {page_error}")
                    break

            _LOGGER.info(
                f"MAIL: Retrieved total of {len(all_threads)} mail threads across all pages"
            )

            if all_threads:
                self._parse_mail_data(all_threads)
            _LOGGER.info(
                f"MAIL: Successfully processed {len(self.mail_threads)} mail threads"
            )
            _LOGGER.info(
                f"MAIL: Mail distribution by child: {[(child_id, len(threads)) for child_id, threads in self.mail_by_child.items()]}"
            )

            # Debug child ID mapping
            _LOGGER.debug(
                f"MAIL: Available child IDs from _children: {[str(child['id']) for child in getattr(self, '_children', [])]}"
            )
            _LOGGER.debug(
                f"MAIL: Mail threads stored for child IDs: {list(self.mail_by_child.keys())}"
            )

        except Exception as e:
            _LOGGER.error(f"MAIL: Error in _get_mail: {e}", exc_info=True)
            self.mail_threads = {}
            self.mail_by_child = {}

    def _parse_mail_data(self, threads_data):
        """Parse mail threads data from getThreads API response"""
        try:
            _LOGGER.info("MAIL: Starting to parse mail threads data")

            self.mail_threads = {}
            self.mail_by_child = {}
            # Create mapping between child IDs and regardingChildren profileIds
            self._create_mail_child_mapping(threads_data)

            for thread in threads_data:
                thread_id = str(thread.get("id", ""))
                if not thread_id:
                    continue

                # Extract thread information
                parsed_thread = {
                    "id": thread_id,
                    "subject": thread.get("subject", ""),
                    "read": thread.get("read", False),
                    "muted": thread.get("muted", False),
                    "marked": thread.get("marked", False),
                    "sensitive": thread.get("sensitive", False),
                    "started_time": thread.get("startedTime", ""),
                    "latest_message": self._extract_latest_message(
                        thread.get("latestMessage", {})
                    ),
                    "creator": self._extract_thread_creator(thread.get("creator", {})),
                    "regarding_children": self._extract_regarding_children(
                        thread.get("regardingChildren", [])
                    ),
                    "recipients_count": len(thread.get("recipients", []))
                    + (thread.get("extraRecipientsCount") or 0),
                    "institution_code": thread.get("institutionCode", ""),
                }

                # Store thread in main mail dictionary
                self.mail_threads[thread_id] = parsed_thread

                # Group threads by related child profiles using profile IDs
                regarding_children = thread.get("regardingChildren", [])
                regarding_child_ids = []
                for child in regarding_children:
                    profile_id = str(child.get("profileId", ""))
                    if profile_id:
                        regarding_child_ids.append(profile_id)
                        if profile_id not in self.mail_by_child:
                            self.mail_by_child[profile_id] = []
                        self.mail_by_child[profile_id].append(parsed_thread)

                        # Also map by sensor child ID for easier access
                        sensor_child_id = self._get_sensor_child_id_for_profile(
                            profile_id
                        )
                        if sensor_child_id:
                            if sensor_child_id not in self.mail_by_child:
                                self.mail_by_child[sensor_child_id] = []
                            self.mail_by_child[sensor_child_id].append(parsed_thread)

                _LOGGER.debug(
                    f"MAIL: Parsed thread {thread_id}: '{parsed_thread['subject']}' regarding children: {regarding_child_ids}"
                )

            _LOGGER.info(
                f"MAIL: Successfully parsed {len(self.mail_threads)} mail threads for {len(self.mail_by_child)} children"
            )
        except Exception as e:
            _LOGGER.error(f"MAIL: Error parsing mail data: {e}", exc_info=True)
            self.mail_threads = {}
            self.mail_by_child = {}

    def _create_mail_child_mapping(self, threads_data):
        """Create mapping between sensor child IDs and regardingChildren profile IDs"""
        try:
            # Extract profile IDs and display names from mail threads
            profile_name_map = {}
            for thread in threads_data:
                regarding_children = thread.get("regardingChildren", [])
                for child in regarding_children:
                    profile_id = str(child.get("profileId", ""))
                    display_name = child.get("displayName", "")
                    if profile_id and display_name:
                        profile_name_map[profile_id] = display_name

            # Match with existing child names to create mapping
            self.mail_child_profile_mapping = {}
            for child_id, child_name in self._childnames.items():
                child_id_str = str(child_id)
                # Find matching profile by name
                for profile_id, display_name in profile_name_map.items():
                    if child_name.strip() == display_name.strip():
                        self.mail_child_profile_mapping[child_id_str] = profile_id
                        _LOGGER.debug(
                            f"MAIL: Mapped sensor child ID {child_id_str} ({child_name}) to profile ID {profile_id} ({display_name})"
                        )
                        break

            _LOGGER.info(
                f"MAIL: Created child profile mapping: {self.mail_child_profile_mapping}"
            )

        except Exception as e:
            _LOGGER.error(f"MAIL: Error creating child mapping: {e}", exc_info=True)
            self.mail_child_profile_mapping = {}

    def _get_sensor_child_id_for_profile(self, profile_id):
        """Get sensor child ID for a regardingChildren profile ID"""
        for (
            sensor_child_id,
            mapped_profile_id,
        ) in self.mail_child_profile_mapping.items():
            if mapped_profile_id == profile_id:
                return sensor_child_id
        return None

    def _extract_latest_message(self, latest_message):
        """Extract latest message information from a thread"""
        if not latest_message:
            return {}

        # Clean HTML content for text preview
        html_content = latest_message.get("text", {}).get("html", "")
        import re

        clean_text = re.sub(r"<[^>]+>", "", html_content)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        return {
            "id": latest_message.get("id", ""),
            "send_date_time": latest_message.get("sendDateTime", ""),
            "text_html": html_content,
            "text_clean": clean_text,
        }

    def _extract_thread_creator(self, creator):
        """Extract thread creator information"""
        if not creator:
            return {}

        return {
            "full_name": creator.get("fullName", ""),
            "metadata": creator.get("metadata", ""),
            "answer_directly_name": creator.get("answerDirectlyName", ""),
        }

    def _extract_regarding_children(self, regarding_children):
        """Extract information about children the thread regards"""
        extracted_children = []
        for child in regarding_children:
            if child:
                extracted_children.append(
                    {
                        "profile_id": str(child.get("profileId", "")),
                        "display_name": child.get("displayName", ""),
                        "short_name": child.get("shortName", ""),
                    }
                )
        return extracted_children
