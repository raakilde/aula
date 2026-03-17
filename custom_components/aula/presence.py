"""
Presence management for Aula integration.
Handles weekly presence, presence templates, and closed days.
"""

import datetime
import logging

_LOGGER = logging.getLogger(__name__)


class PresenceMixin:
    """Mixin providing presence-related methods for the Aula Client."""

    def _get_presence_templates(self):
        """Get weekly schedule presence templates (current and next week)"""
        try:
            today = datetime.date.today()

            # Calculate current week (Monday to Sunday)
            current_monday = today - datetime.timedelta(days=today.weekday())
            current_sunday = current_monday + datetime.timedelta(days=6)

            # Calculate next week
            next_monday = current_monday + datetime.timedelta(days=7)
            next_sunday = next_monday + datetime.timedelta(days=6)

            # Get child profile IDs (same IDs used by _get_weekly_presence)
            child_profile_ids = self._get_dynamic_child_profile_ids()
            if not child_profile_ids:
                _LOGGER.warning("No child profile IDs available for presence templates")
                self.presence_templates = {}
                self.presence_templates_next = {}
                return

            _LOGGER.debug(
                f"Fetching presence templates: {current_monday}–{current_sunday}, {next_monday}–{next_sunday}"
            )

            # Fetch current week
            self.presence_templates = self._fetch_and_format_schedule_templates(
                current_monday, current_sunday, child_profile_ids, "current"
            )

            # Fetch next week
            self.presence_templates_next = self._fetch_and_format_schedule_templates(
                next_monday, next_sunday, child_profile_ids, "next"
            )

        except Exception as e:
            _LOGGER.error(f"Error in _get_presence_templates: {e}")
            self.presence_templates = {}
            self.presence_templates_next = {}

    def _fetch_and_format_schedule_templates(
        self, start_date, end_date, child_profile_ids, week_label
    ):
        """Fetch presence templates using fromDate/toDate API and format for schedule sensors."""
        try:
            from_date = start_date.strftime("%Y-%m-%d")
            to_date = end_date.strftime("%Y-%m-%d")

            url_params = f"method=presence.getPresenceTemplates&fromDate={from_date}&toDate={to_date}"
            for pid in child_profile_ids:
                url_params += f"&filterInstitutionProfileIds[]={pid}"

            resp = self._session.get(
                f"{self.apiurl}?{url_params}",
                headers=self._auth_headers(),
                verify=True,
                timeout=15,
            )

            if resp.status_code != 200:
                _LOGGER.error(
                    f"presence.getPresenceTemplates ({week_label} week) returned status {resp.status_code}"
                )
                return {}

            result = resp.json()
            if result.get("status", {}).get("message") != "OK":
                _LOGGER.warning(
                    f"Non-OK status for {week_label} week presence templates"
                )
                return {}

            data = result.get("data", {})
            templates = data.get("presenceWeekTemplates", [])

            # Format keyed by child first name (what schedule sensors expect)
            formatted = {}
            for template in templates:
                inst_profile = template.get("institutionProfile", {})
                child_name = inst_profile.get("name", "")
                first_name = child_name.split()[0] if child_name else ""
                institution_name = inst_profile.get("institutionName", "")

                if not first_name:
                    continue

                formatted[first_name] = {
                    "child_name": child_name,
                    "institution": institution_name,
                    "day_templates": template.get("dayTemplates", []),
                }

            _LOGGER.debug(
                f"Retrieved presence templates for {week_label} week: {len(formatted)} children"
            )
            return formatted

        except Exception as e:
            _LOGGER.error(f"Error fetching {week_label} week presence templates: {e}")
            return {}

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
                response = self._session.get(url, headers=self._auth_headers(), verify=True, timeout=10)

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
        try:
            _LOGGER.debug("Starting to fetch dynamic weekly presence data...")

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

            _LOGGER.debug(f"Current week: {current_monday} to {current_sunday}")
            _LOGGER.debug(f"Next week: {next_monday} to {next_sunday}")

            # Get child profile IDs dynamically from profile context
            child_profile_ids = self._get_dynamic_child_profile_ids()

            if not child_profile_ids:
                _LOGGER.warning(
                    "WEEKLY PRESENCE: No child profile IDs found for presence data"
                )
                return

            _LOGGER.debug(
                f"Using {len(child_profile_ids)} child profile IDs for presence data"
            )

            # Fetch current week data
            _LOGGER.debug("Fetching current week data...")
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
            _LOGGER.debug("Fetching next week data...")
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

            _LOGGER.debug(
                f"Weekly presence completed - Current: {len(self.weekly_presence_current)} children, Next: {len(self.weekly_presence_next)} children"
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
            _LOGGER.debug("Getting dynamic child profile IDs...")

            # First, try to get from _childnames (most reliable) - these are the 'id' fields
            if hasattr(self, "_childnames") and self._childnames:
                child_ids_from_names = list(self._childnames.keys())
                child_profile_ids.extend(child_ids_from_names)
                _LOGGER.debug(
                    f"Found {len(child_ids_from_names)} child IDs from profile data"
                )
            else:
                _LOGGER.debug("No child names found in profile data")

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
                headers=self._auth_headers(),
                verify=True,
                timeout=15,
            )

            _LOGGER.debug("PRESENCE API CALL: presence.getPresenceTemplates")
            _LOGGER.debug(f"Response status: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                _LOGGER.debug(
                    f"API response keys: {list(result.keys()) if isinstance(result, dict) else type(result)}"
                )
                _LOGGER.debug(f"API status: {result.get('status', {})}")

                if result.get("status", {}).get("message") == "OK":
                    data = result.get("data", {})
                    _LOGGER.debug(
                        f"Data keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
                    )

                    if "presenceWeekTemplates" in data:
                        templates = data["presenceWeekTemplates"]
                        _LOGGER.debug(f"Found {len(templates)} presence week templates")
                        for i, template in enumerate(templates):
                            institution_profile = template.get("institutionProfile", {})
                            child_id = institution_profile.get("id")
                            child_name = institution_profile.get("name", "Unknown")
                            day_templates = template.get("dayTemplates", [])
                            _LOGGER.debug(
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

                    _LOGGER.debug(
                        f"PARSING: Processing child {child_name} (ID: {child_id})"
                    )

                    if not child_id:
                        _LOGGER.warning(f"PARSING: No child_id found for {child_name}")
                        continue

                    # Initialize child data
                    parsed_data[child_id] = {}
                    daily_count = 0

                    # Process day templates
                    day_templates = template.get("dayTemplates", [])
                    _LOGGER.debug(
                        f"PARSING: Processing {len(day_templates)} day templates for {child_name}"
                    )

                    for day_template in day_templates:
                        by_date = day_template.get("byDate")
                        if not by_date:
                            continue

                        # Parse the date
                        try:
                            date_obj = datetime.datetime.strptime(
                                by_date, "%Y-%m-%d"
                            ).date()
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
                            "check_in_time": None,
                            "check_out_time": None,
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

                _LOGGER.debug(
                    f"PARSING: Successfully parsed presence data for {len(parsed_data)} children"
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
