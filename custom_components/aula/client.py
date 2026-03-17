"""
Aula client with MitID authentication
Based on https://github.com/JBoye/HA-Aula

This is the main orchestrator that composes functionality from:
- aula_auth.py: Authentication, session management, browser automation
- widgets.py: Widget detection, token management, institution types
- presence.py: Presence templates, weekly presence, closed days
- posts.py: Posts/news fetching and parsing
- mail.py: Mail threads fetching and parsing
"""

import datetime
import json
import logging
import os
import re
import time

import requests
from homeassistant.exceptions import ConfigEntryNotReady

from .aula_auth import AuthMixin
from .const import (
    AUTH_METHOD_APP,
    CICERO_API,
    MEEBOOK_API,
    MIN_UDDANNELSE_API,
    SYSTEMATIC_API,
)
from .mail import MailMixin
from .minuddannelse import MinUddannelse
from .posts import PostsMixin
from .presence import PresenceMixin
from .widgets import WidgetsMixin

_LOGGER = logging.getLogger(__name__)


def _safe_json(response, context="API call"):
    """Parse JSON from response, raising on non-200 status or empty body."""
    if response.status_code != 200:
        raise Exception(f"{context} returned status {response.status_code}")
    return response.json()


class Client(AuthMixin, WidgetsMixin, PresenceMixin, PostsMixin, MailMixin):
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
        auth_method=None,
        mitid_username=None,
        mitid_password=None,
        mitid_token=None,
        stored_tokens=None,
        token_persist_callback=None,
        hass=None,
        config_entry=None,
    ):
        self._session = None
        self._last_data_update = 0

        # MitID token-based auth fields
        self._auth_method = auth_method or AUTH_METHOD_APP
        self._mitid_username = mitid_username
        self._mitid_password = mitid_password
        self._mitid_token = mitid_token
        self._stored_tokens = stored_tokens or {}
        self._token_persist_callback = token_persist_callback
        self._login_client = None
        self._access_token = None
        self._hass = hass
        self._config_entry = config_entry

        # Use Home Assistant config dir for data files (not CWD)
        self._data_dir = hass.config.path() if hass else os.getcwd()

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
        # Initialize attributes that update_data populates, so they exist even if auth fails
        self._children = []
        self._childnames = {}
        self._childids = []
        self._childuserids = []
        self._institutions = {}
        self._institution_types = {}
        self._institutionProfiles = []
        self._institutionProfileIdsList = []
        self._daily_overview = {}

    def _ensure_token_auth(self):
        """Ensure we have a valid access token (MitID auth method)."""
        if not self._session:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/113.0.0.0 Mobile Safari/537.36",
                    "Accept": "application/json",
                }
            )

        # Check if we have stored tokens
        access_token = self._stored_tokens.get("access_token")
        refresh_token = self._stored_tokens.get("refresh_token")
        expires_at = self._stored_tokens.get("expires_at", 0)

        if access_token and time.time() < expires_at - 300:
            # Token still valid (with 5 min buffer)
            self._access_token = access_token
            _LOGGER.debug("Using existing access token (valid)")
            return True

        # Try to refresh the token — simple HTTP POST, no MitID needed
        if refresh_token:
            _LOGGER.debug("Access token expired, attempting refresh...")
            try:
                r = self._session.post(
                    "https://login.aula.dk/simplesaml/module.php/oidc/token.php",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": "_99949a54b8b65423862aac1bf629599ed64231607a",
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    timeout=30,
                )
                if r.status_code == 200:
                    new = r.json()
                    self._stored_tokens["access_token"] = new["access_token"]
                    if "refresh_token" in new:
                        self._stored_tokens["refresh_token"] = new["refresh_token"]
                    if "expires_in" in new:
                        self._stored_tokens["expires_at"] = (
                            time.time() + new["expires_in"]
                        )
                    self._access_token = new["access_token"]
                    if self._token_persist_callback:
                        self._token_persist_callback(self._stored_tokens)
                    _LOGGER.info(
                        "Access token refreshed (expires in %ss)",
                        new.get("expires_in", "?"),
                    )
                    return True
                else:
                    _LOGGER.warning("Token refresh failed: HTTP %s", r.status_code)
            except Exception as e:
                _LOGGER.error("Token refresh error: %s", e)

        # Full re-authentication requires user interaction (MitID app approval)
        # and cannot be done automatically in the background.
        _LOGGER.error(
            "Token refresh failed — manual re-authentication required. "
            "Please re-configure the Aula integration to log in again."
        )
        return False

    def _make_api_url(self, method, **params):
        """Build API URL (without token — use _auth_headers instead)."""
        url = self.apiurl + f"?method={method}"
        for key, val in params.items():
            if isinstance(val, list):
                for v in val:
                    url += f"&{key}[]={v}"
            else:
                url += f"&{key}={val}"
        return url

    def _data_path(self, filename):
        """Return the full path for a data file under HA config dir."""
        return os.path.join(self._data_dir, filename)

    def _auth_headers(self):
        """Return headers with the access token as a Bearer token."""
        headers = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def update_data(self):
        # Rate limiting: prevent multiple rapid data fetches within 30 seconds
        current_time = time.time()
        if current_time - self._last_data_update < 30:  # 30 seconds
            _LOGGER.debug(
                f"Data update rate limited, using cached result (last update: {current_time - self._last_data_update:.1f}s ago)"
            )
            return

        self._last_data_update = current_time
        _LOGGER.debug("Starting data update cycle...")

        # Clear cached widget tokens so they are re-fetched fresh each cycle
        self.tokens = {}

        # Ensure we have a valid access token
        have_valid_session = self._ensure_token_auth()

        if not have_valid_session:
            raise ConfigEntryNotReady(
                "Authentication failed. Please reconfigure the integration."
            )

        # Ensure post-login setup is done
        if (
            not hasattr(self, "apiurl")
            or not hasattr(self, "_profiles")
            or not self._profiles
        ):
            self._setup_post_login()

        # Verify API access with profiles data
        is_logged_in = False
        if (
            self._session
            and hasattr(self, "apiurl")
            and hasattr(self, "_profiles")
            and self._profiles
        ):
            try:
                api_url = self._make_api_url("profiles.getProfilesByLogin")

                response = self._session.get(
                    api_url,
                    headers=self._auth_headers(),
                    verify=True,
                    timeout=10,
                )
                data = _safe_json(response, "profiles.getProfilesByLogin")
                is_logged_in = data.get("status", {}).get("message") == "OK"
                if is_logged_in:
                    # Keep profiles fresh
                    self._profiles = data.get("data", {}).get(
                        "profiles", self._profiles
                    )
            except Exception as e:
                _LOGGER.warning(f"Failed to test API access: {e}")
                is_logged_in = False

        _LOGGER.debug("is_logged_in? " + str(is_logged_in))

        if not is_logged_in:
            _LOGGER.error("API access test failed - authentication may have expired")
            raise ConfigEntryNotReady(
                "API access denied. Please reconfigure the integration."
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
            f"Initialized {len(self._children)} children for {len(self._institutionProfiles)} institutions"
        )

        self._daily_overview = {}
        for i, child in enumerate(self._children):
            response = self._session.get(
                self.apiurl
                + "?method=presence.getDailyOverview&childIds[]="
                + str(child["id"]),
                headers=self._auth_headers(),
                verify=True,
            )
            data = _safe_json(
                response, f"presence.getDailyOverview child={child['id']}"
            )
            if len(data["data"]) > 0:
                self.presence[str(child["id"])] = 1
                self._daily_overview[str(child["id"])] = data["data"][0]
            else:
                _LOGGER.warn(
                    "Unable to retrieve presence data from Aula from child with id "
                    + str(child["id"])
                    + ". Some data will be missing from sensor entities."
                )
                self.presence[str(child["id"])] = 0
        _LOGGER.debug("Child ids and presence data status: " + str(self.presence))

        # Initialize widgets early so feature flags are set before widget sections
        if len(self.widgets) == 0:
            self.get_widgets()

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
                headers=self._auth_headers(),
                verify=True,
            )
            _LOGGER.debug(f"OLD MESSAGES: Response status: {mesres.status_code}")
            self.unread_messages = 0
            unread = 0
            self.message = {}
            mesdata = _safe_json(mesres, "messaging.getThreads")
            for mes in mesdata["data"]["threads"]:
                if not mes["read"]:
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
            if unread == 1:
                threadres = self._session.get(
                    self.apiurl
                    + "?method=messaging.getMessagesForThread&threadId="
                    + str(threadid)
                    + "&page=0",
                    headers=self._auth_headers(),
                    verify=True,
                )
                threaddata = _safe_json(threadres, "messaging.getMessagesForThread")
                if threaddata["status"]["code"] == 403:
                    self.message["text"] = (
                        "Log ind på Aula med MitID for at læse denne besked."
                    )
                    self.message["sender"] = "Ukendt afsender"
                    self.message["subject"] = "Følsom besked"
                else:
                    for message in threaddata["data"]["messages"]:
                        if message["messageType"] == "Message":
                            try:
                                self.message["text"] = message["text"]["html"]
                            except (KeyError, TypeError):
                                try:
                                    self.message["text"] = message["text"]
                                except (KeyError, TypeError):
                                    self.message["text"] = "intet indhold..."
                                    _LOGGER.warning(
                                        "There is an unread message, but we cannot get the text."
                                    )
                            try:
                                self.message["sender"] = message["sender"].get(
                                    "shortName",
                                    message["sender"].get(
                                        "fullName", "Ukendt afsender"
                                    ),
                                )
                            except (KeyError, TypeError):
                                self.message["sender"] = "Ukendt afsender"
                            try:
                                self.message["subject"] = threaddata["data"]["subject"]
                            except (KeyError, TypeError):
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
            csrf_token = self._session.cookies.get_dict().get("Csrfp-Token", "")
            headers = {
                **self._auth_headers(),
                "csrfp-token": csrf_token,
                "content-type": "application/json",
            }
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
            res = self._session.post(
                self.apiurl + "?method=calendar.getEventsByProfileIdsAndResourceIds",
                data=post_data,
                headers=headers,
                verify=True,
            )
            try:
                _path = self._data_path("skoleskema.json")
                fd = os.open(_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as skoleskema_json:
                    json.dump(res.text, skoleskema_json)
            except (OSError, ValueError) as e:
                _LOGGER.warning("Failed to write skoleskema.json: %s", e)
        # End of calendar

        # Bibliotek:
        # Fetch library data if any child is at a school-type institution
        has_school_children = any(
            itype != "kindergarten" for itype in self._institution_types.values()
        )
        if has_school_children:
            if len(self.widgets) == 0:
                self.get_widgets()

            _LOGGER.debug(
                f"Fetching library data (bibliotek flag: {self._bibliotek}, "
                f"widget 0019 available: {'0019' in self.widgets}, "
                f"institution types: {self._institution_types})"
            )

            try:
                token = self.get_token("0019")

                response = self._session.get(
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
                )

                if response.status_code != 200:
                    _LOGGER.warning(
                        f"Library API returned status {response.status_code}"
                    )
                    self.loaned_books = {}
                else:
                    books = response.json()

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

                    _LOGGER.debug(
                        f"Library books loaded. {len(self.loaned_books)} patron(s), "
                        f"{len(self._childnames)} child(ren) registered"
                    )
            except Exception as e:
                _LOGGER.warning(f"Failed to fetch library data: {e}")
                self.loaned_books = {}

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
                        _path = self._data_path("uddannelseopgaveliste.json")
                        fd = os.open(
                            _path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
                        )
                        with os.fdopen(fd, "w") as uddannelseopgaveliste_json:
                            json.dump(opgaver, uddannelseopgaveliste_json)
                    except (OSError, ValueError) as e:
                        _LOGGER.warning(
                            "Failed to write uddannelseopgaveliste.json: %s", e
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
                    except Exception:
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
                    except Exception:
                        self.ugenotenextweek = {}

        # End of Min Uddannelse Uge Note

        # Ugeplaner:
        if self._ugeplan is True:
            guardian = _safe_json(
                self._session.get(
                    self.apiurl
                    + "?method=profiles.getProfileContext&portalrole=guardian",
                    headers=self._auth_headers(),
                    verify=True,
                ),
                "profiles.getProfileContext",
            )["data"]["userId"]
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
                    ugedata = _safe_json(ugeplaner, "MinUddannelse ugebrev")
                    for person in ugedata["personer"]:
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
                        except (json.JSONDecodeError, ValueError) as e:
                            _LOGGER.error(
                                "Could not parse the response from Huskelisten as json: %s",
                                e,
                            )

                    for person in data:
                        name = person["userName"].split()[0]
                        _LOGGER.debug(
                            "Huskelisten for child ID %s",
                            person.get("userId", "unknown"),
                        )
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
                        mock_meebook = '[{"id":490000,"name":"Test Barn 1","unilogin":"test001","weekPlan":[{"date":"mandag 28. nov.","tasks":[{"id":3069630,"type":"comment","author":"Lærer A","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Eksempel indhold til test.","editUrl":"https://example.com/test/202248"}]},{"date":"tirsdag 29. nov.","tasks":[{"id":3069631,"type":"comment","author":"Lærer A","group":"3.a - ugeplan","pill":"Ingen fag tilknyttet","content":"Eksempel indhold til test.","editUrl":"https://example.com/test/202248"}]}]},{"id":630000,"name":"Test Barn 2","unilogin":"test002","weekPlan":[{"date":"mandag 28. nov.","tasks":[{"id":3090189,"type":"comment","author":"Lærer B","group":"0C (22/23)","pill":"Dansk, Matematik","content":"Eksempel indhold til test.","editUrl":"https://example.com/test/202248"}]}]}]'
                        data = json.loads(mock_meebook, strict=False)
                    else:
                        response = requests.get(
                            MEEBOOK_API + get_payload, headers=headers, verify=True
                        )
                        data = json.loads(response.text, strict=False)

                    for person in data:
                        _LOGGER.debug(
                            "Meebook ugeplan for child ID %s",
                            person.get("id", "unknown"),
                        )
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
                        except (KeyError, IndexError, AttributeError):
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
        # End of Ugeplaner

        # Posts (Indlæg):
        try:
            self._get_posts()
            _LOGGER.debug("Posts data retrieved successfully")
        except Exception as e:
            _LOGGER.error(f"Failed to retrieve posts: {e}", exc_info=True)
            # Initialize empty posts data on error
            self.posts = {}
            self.posts_by_child = {}

        # Mail Threads:
        try:
            self._get_mail()
            mail_count = len(getattr(self, "mail_threads", {}))
            child_count = len(getattr(self, "mail_by_child", {}))
            _LOGGER.debug(
                f"Retrieved {mail_count} mail threads for {child_count} children"
            )
        except Exception as e:
            _LOGGER.error(f"Failed to retrieve mail: {e}", exc_info=True)
            # Initialize empty mail data on error
            self.mail_threads = {}
            self.mail_by_child = {}
