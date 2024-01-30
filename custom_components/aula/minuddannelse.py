import logging
import time

from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)
from .const import (
    MIN_UDDANNELSE_API,
)


class MinUddannelse:
    def __init__(
        self,
        minUddannelseForloeb,
        minUddannelseOpgaveListe,
        minUddannelseUgeNote,
    ):
        self._minUddannelseForloeb = minUddannelseForloeb
        self._minUddannelseOpgaveListe = minUddannelseOpgaveListe
        self._minUddannelseugenote = minUddannelseUgeNote

    def forloeb(
        self, session, token, week, childuserids, institutionProfiles, username
    ):
        _LOGGER.debug("Getting Min Uddannelse Forløb")

        children = session.get(
            MIN_UDDANNELSE_API
            + "/forloeb?"
            + "assuranceLevel=2"
            + "&childFilter="
            + ",".join(childuserids)
            + "&currentWeekNumber="
            + week
            + "&institutionFilter="
            + ",".join(institutionProfiles)
            + "&isMobileApp=false"
            + "&placement=full"
            + "&sessionUUID="
            + username
            + "&userProfile=guardian",
            headers={"Authorization": token, "accept": "application/json"},
            verify=True,
        ).json()["personer"]

        header = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.aula.dk",
            "Referer": "https://www.aula.dk/",
            # "Sec-Fetch-Dest": "document",
            # "Sec-Fetch-Mode": "navigate",
            # "Sec-Fetch-Site": "cross-site",
            "Authorization": token,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
        }

        forloeb = {}

        for child in children:
            data = {
                "childFilter": ",".join(childuserids),
                "currentWeekNumber": week,
                "group": "",
                "assuranceLevel": "2",
                "institutionFilter": ",".join(institutionProfiles),
                "isMobileApp": "false",
                "placement": "narrow",
                "sessionUUID": username,
                "userProfile": "guardian",
            }

            # Only take the first normally there are only one
            for task in child["institutioner"][0]["forloeb"]:
                # Get description from MinUddannelse
                task_description_page = session.post(
                    task["url"], data=data, headers=header, allow_redirects=True
                )

                _html = BeautifulSoup(task_description_page.text, "lxml")
                task_description = _html.find(
                    "div", {"class": "text-user fr-view"}
                ).get_text()

                # Add entry
                entry = {
                    "Name": task["navn"],
                    "Url": task["url"],
                    "Description": task_description,
                }

                if child["navn"] not in forloeb:
                    forloeb[child["navn"]] = []

                forloeb[child["navn"]].append(entry)

        return forloeb

    def ugeBrev(
        self, session, token, week, childuserids, institutionProfiles, username
    ):
        _LOGGER.debug("Getting Min Uddannelse Uge Brev")

        result = session.get(
            MIN_UDDANNELSE_API
            + "/redirect?redirectUrl=https://www.minuddannelse.net?"
            + "assuranceLevel=2"
            + "&childFilter="
            + ",".join(childuserids)
            + "&currentWeekNumber="
            + week
            + "&institutionFilter="
            + ",".join(institutionProfiles)
            + "&isMobileApp=false"
            + "&placement=full"
            + "&sessionUUID="
            + username
            + "&userProfile=guardian",
            headers={"Authorization": token},
            verify=True,
        )

        _html = BeautifulSoup(result.content, "lxml")
        anchor_element = _html.find("div", {"class": "col-sm-8 col-xs-7"})
        anchor_element = anchor_element.find("a")
        elevid = anchor_element.get("href").replace("minuge/", "")

        result = session.get(
            # MIN_UDDANNELSE_API
            "https://www.minuddannelse.net/api/stamdata/ugeplan/getUgeBreve?"
            + "tidspunkt="
            + week
            + "&elevId="
            + elevid
            + "&_="
            + str(round(time.time())),
            headers={"accept": "application/json"},
        ).json()["ugebreve"][0]["indhold"]

        _html = BeautifulSoup(result, "lxml")
        return _html.getText()

    def opgaveListe(
        self, session, token, week, childuserids, institutionProfiles, username
    ):
        _LOGGER.debug("Getting Min Uddannelse opgave Liste")

        result = session.get(
            MIN_UDDANNELSE_API
            + "/redirect?redirectUrl=https://www.minuddannelse.net?"
            + "assuranceLevel=2"
            + "&childFilter="
            + ",".join(childuserids)
            + "&currentWeekNumber="
            + week
            + "&institutionFilter="
            + ",".join(institutionProfiles)
            + "&isMobileApp=false"
            + "&placement=full"
            + "&sessionUUID="
            + username
            + "&userProfile=guardian",
            headers={"Authorization": token},
            verify=True,
        )

        _html = BeautifulSoup(result.content, "lxml")
        anchor_element = _html.find("div", {"class": "col-sm-8 col-xs-7"})
        anchor_element = anchor_element.find("a")
        elevid = anchor_element.get("href").replace("minuge/", "")

        opgaver = session.get(
            # MIN_UDDANNELSE_API
            "https://www.minuddannelse.net/api/forloebsafvikling/opgaver/getOpgaveliste?"
            + "tidspunkt="
            + week
            + "&elevId="
            + elevid
            + "&_="
            + str(round(time.time())),
            headers={"accept": "application/json"},
        ).json()["opgaver"]

        return opgaver
