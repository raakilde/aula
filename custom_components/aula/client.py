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
import re
import time

import requests
from homeassistant.exceptions import ConfigEntryNotReady

from .aula_auth import AuthMixin
from .const import (
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
        auth_cookies=None,
        cookie_persist_callback=None,
    ):
        self._session = None
        self._auth_cookies = auth_cookies or {}
        self._cookie_persist_callback = cookie_persist_callback
        self._last_session_test = 0  # Rate limiting for session tests
        self._last_data_update = 0  # Rate limiting for data updates
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
            f"Initialized {len(self._children)} children for {len(self._institutionProfiles)} institutions"
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
                verify=True,
            )
            _LOGGER.debug(f"OLD MESSAGES: Response status: {mesres.status_code}")
            self.unread_messages = 0
            unread = 0
            self.message = {}
            for mes in mesres.json()["data"]["threads"]:
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
                    verify=True,
                )
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

                _LOGGER.debug(
                    f"Library books loaded. Keys (patronDisplayNames): {list(self.loaned_books.keys())}, "
                    f"Child names: {list(self._childnames.values())}"
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
