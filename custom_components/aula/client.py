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
    ugep_attr = {}
    ugepnext_attr = {}
    widgets = {}
    tokens = {}
    loaned_books = {}
    forloebthisweek = {}
    forloebnext = {}
    ugenotethisweek = {}
    ugenotenextweek = {}

    def __init__(
        self,
        schoolschedule,
        ugeplan,
        bibliotek,
        minUddannelseForloeb,
        minUddannelseOpgaveListe,
        minUddannelseUgeNote,
        auth_cookies=None,
        cookie_persist_callback=None,
    ):
        self._session = None
        self._auth_cookies = auth_cookies or {}
        self._cookie_persist_callback = cookie_persist_callback
        self._last_session_test = 0  # Rate limiting for session tests
        self._schoolschedule = schoolschedule
        self._ugeplan = ugeplan
        self._bibliotek = bibliotek
        self._minUddannelseForloeb = minUddannelseForloeb
        self._minUddannelseOpgaveListe = minUddannelseOpgaveListe
        self._minUddannelseUgeNote = minUddannelseUgeNote
        self._minUddannelse = MinUddannelse(
            minUddannelseForloeb, minUddannelseOpgaveListe, minUddannelseUgeNote
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
            api_success = False

            while not api_success:
                _LOGGER.debug("Trying API at " + self.apiurl)
                try:
                    ver = self._session.get(
                        self.apiurl + "?method=profiles.getProfilesByLogin",
                        verify=True,
                        timeout=10,
                    )

                    if ver.status_code == 410:
                        _LOGGER.debug(
                            f"API version {apiver} not supported, trying newer version"
                        )
                        apiver += 1
                        self.apiurl = API + str(apiver)
                        if apiver > 25:  # Safety limit
                            break
                    elif ver.status_code == 403:
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
                                api_success = True
                                _LOGGER.info(
                                    f"Successfully connected to API {self.apiurl}"
                                )
                            else:
                                _LOGGER.error(
                                    "API response missing expected data structure"
                                )
                                break
                        except ValueError as e:
                            _LOGGER.error(f"Invalid JSON response from API: {e}")
                            break
                    else:
                        _LOGGER.error(
                            f"API returned unexpected status: {ver.status_code}"
                        )
                        break

                except requests.exceptions.RequestException as e:
                    _LOGGER.error(f"API request failed: {e}")
                    break

            if not api_success:
                raise ConfigEntryNotReady("Failed to connect to Aula API")

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
        _LOGGER.debug("LOGIN: " + str(success))
        _LOGGER.debug(
            "Config - schoolschedule: "
            + str(self._schoolschedule)
            + ", config - ugeplaner: "
            + str(self._ugeplan)
        )

    def get_widgets(self):
        detected_widgets = self._session.get(
            self.apiurl + "?method=profiles.getProfileContext", verify=True
        )
        for widget in detected_widgets.json()["data"]["pageConfiguration"][
            "widgetConfigurations"
        ]:
            widgetid = str(widget["widget"]["widgetId"])
            widgetname = widget["widget"]["name"]
            self.widgets[widgetid] = widgetname
        _LOGGER.debug("Widgets found: " + str(self.widgets))

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
        self._childuserids = []
        self._childids = []
        self._children = []
        self._institutionProfiles = []
        for profile in self._profiles:
            for child in profile["children"]:
                self._childnames[child["id"]] = child["name"]
                self._institutions[child["id"]] = child["institutionProfile"][
                    "institutionName"
                ]
                self._children.append(child)
                self._childids.append(str(child["id"]))
                self._childuserids.append(str(child["userId"]))
            for institutioncode in profile["institutionProfiles"]:
                if (
                    str(institutioncode["institutionCode"])
                    not in self._institutionProfiles
                ):
                    self._institutionProfiles.append(
                        str(institutioncode["institutionCode"])
                    )
        _LOGGER.debug("Child ids and names: " + str(self._childnames))
        _LOGGER.debug("Child ids and institution names: " + str(self._institutions))
        _LOGGER.debug("Institution codes: " + str(self._institutionProfiles))

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

        # Messages:
        mesres = self._session.get(
            self.apiurl
            + "?method=messaging.getThreads&sortOn=date&orderDirection=desc&page=0",
            verify=True,
        )
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
            guardian = self._session.get(
                self.apiurl + "?method=profiles.getProfileContext&portalrole=guardian",
                verify=True,
            ).json()["data"]["userId"]
            childUserIds = ",".join(self._childuserids)

            if len(self.widgets) == 0:
                self.get_widgets()

            # Check for support
            if "0019" not in self.widgets:
                _LOGGER.error(
                    "You have enabled bibliotek, but we cannot find any matching widgets (0019) in Aula."
                )

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
                _LOGGER.error(
                    "You have enabled min uddannelse forloeb, but we cannot find any matching widgets (0019) in Aula."
                )

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
                _LOGGER.error(
                    "You have enabled min uddannelse forloeb, but we cannot find any matching widgets (0019) in Aula."
                )

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
            with open("uddannelseopgaveliste.json", "w") as uddannelseopgaveliste_json:
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
                _LOGGER.error(
                    "You have enabled min uddannelse forloeb, but we cannot find any matching widgets (0019) in Aula."
                )

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
            if (
                "0029" not in self.widgets
                and "0004" not in self.widgets
                and "0062" not in self.widgets
            ):
                _LOGGER.error(
                    "You have enabled ugeplaner, but we cannot find any matching widgets (0029,0004) in Aula."
                )
            if "0029" in self.widgets and "0004" in self.widgets:
                _LOGGER.warning(
                    "Multiple sources for ugeplaner is untested and might cause problems."
                )

            def ugeplan(week, thisnext):
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
                        mock_huskelisten = '[{"userName":"Emilie efternavn","userId":164625,"courseReminders":[],"assignmentReminders":[],"teamReminders":[{"id":76169,"institutionName":"Holme Skole","institutionId":183,"dueDate":"2022-11-29T23:00:00Z","teamId":65240,"teamName":"2A","reminderText":"Onsdagslektie: Matematikfessor.dk: Sænk skibet med plus.","createdBy":"Peter ","lastEditBy":"Peter ","subjectName":"Matematik"},{"id":76598,"institutionName":"Holme Skole","institutionId":183,"dueDate":"2022-12-06T23:00:00Z","teamId":65240,"teamName":"2A","reminderText":"Julekalender på Skoledu.dk: I skal forsøge at løse dagens kalenderopgave. opgaven kan også godt løses dagen efter.","createdBy":"Peter ","lastEditBy":"Peter Riis","subjectName":"Matematik"},{"id":76599,"institutionName":"Holme Skole","institutionId":183,"dueDate":"2022-12-13T23:00:00Z","teamId":65240,"teamName":"2A","reminderText":"Julekalender på Skoledu.dk: I skal forsøge at løse dagens kalenderopgave. opgaven kan også godt løses dagen efter.","createdBy":"Peter ","lastEditBy":"Peter ","subjectName":"Matematik"},{"id":76600,"institutionName":"Holme Skole","institutionId":183,"dueDate":"2022-12-20T23:00:00Z","teamId":65240,"teamName":"2A","reminderText":"Julekalender på Skoledu.dk: I skal forsøge at løse dagens kalenderopgave. opgaven kan også godt løses dagen efter.","createdBy":"Peter Riis","lastEditBy":"Peter Riis","subjectName":"Matematik"}]},{"userName":"Karla","userId":77882,"courseReminders":[],"assignmentReminders":[{"id":0,"institutionName":"Holme Skole","institutionId":183,"dueDate":"2022-12-08T11:00:00Z","courseId":297469,"teamNames":["5A","5B"],"teamIds":[65271,65258],"courseSubjects":[],"assignmentId":5027904,"assignmentText":"Skriv en novelle"}],"teamReminders":[{"id":76367,"institutionName":"Holme Skole","institutionId":183,"dueDate":"2022-11-30T23:00:00Z","teamId":65258,"teamName":"5A","reminderText":"Læse resten af kap.1 fra Ternet Ninja ( kopiark) Læs det hele højt eller vælg et afsnit. ","createdBy":"Christina ","lastEditBy":"Christina ","subjectName":"Dansk"}]},{"userName":"Vega  ","userId":206597,"courseReminders":[],"assignmentReminders":[],"teamReminders":[]}]'
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
        return True
