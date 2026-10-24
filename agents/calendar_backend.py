"""Calendar backends for conflict checks, event creation, and rescheduling."""
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(os.getenv(
    "DATA_DIR",
    str(Path(__file__).resolve().parent.parent / "data"),
)).expanduser()
TZ_NAME = "Asia/Kolkata"
TZ = ZoneInfo(TZ_NAME)
_GOOGLE_CALENDARS = {}
_GOOGLE_CALENDARS_LOCK = threading.Lock()
GOOGLE_CALENDAR_IDS = {
    "priya": "59a8d5d4bb1a7a8c1b7b82b17de4b0eec155be2e11c8a06330a802aebb52297d@group.calendar.google.com",
    "arjun": "fa3946c38c3f91849bf35a2d8f2e70cc3096958c47350b22fb10474e2ddca149@group.calendar.google.com",
    "meera": "c2d9b3afc12f2bc162d6ec8584d09ea317b37aa4015908799c501a9cab257914@group.calendar.google.com",
}
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def get_google_calendar_id(name: str) -> str | None:
    norm_name = str(name).strip().lower()
    if norm_name in GOOGLE_CALENDAR_IDS:
        return GOOGLE_CALENDAR_IDS[norm_name]
    env_key = f"GOOGLE_CALENDAR_ID_{norm_name.upper()}"
    val = os.getenv(env_key)
    if val:
        return val.strip()
    try:
        import agent_registry
        agent = agent_registry.get_agent(norm_name)
        if agent and agent.get("metadata", {}).get("google_calendar_id"):
            return agent["metadata"]["google_calendar_id"].strip()
    except Exception:
        pass
    return None


def _aware(iso: str) -> datetime:
    """Parse an ISO string; treat naive times as Asia/Kolkata."""
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def _validate_interval(start, end):
    start_dt = _aware(start)
    end_dt = _aware(end)
    if end_dt <= start_dt:
        raise ValueError("meeting intervals must have an end later than their start")
    return start_dt, end_dt


def _overlaps(request_start, request_end, event_start, event_end) -> bool:
    request_start_dt, request_end_dt = _validate_interval(
        request_start, request_end)
    event_start_dt, event_end_dt = _validate_interval(event_start, event_end)
    return request_start_dt < event_end_dt and event_start_dt < request_end_dt


class FakeCalendar:
    """JSON-backed calendar seeded from data/calendars/<name>.json."""

    def __init__(self, name: str):
        self.path = DATA / "calendars" / f"{name}.json"
        self.events = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def find_conflicts(self, start: str, end: str,
                       exclude_event_id: str | None = None,
                       exclude_event: dict | None = None,
                       exclude_meeting_id: str | None = None) -> list[dict]:
        _validate_interval(start, end)
        return [e for e in self.events
                if e is not exclude_event
                and (exclude_event_id is None or e.get("id") != exclude_event_id)
                and (exclude_meeting_id is None or e.get("meeting_id") != exclude_meeting_id)
                and _overlaps(start, end, e["start"], e["end"])]

    def find_event(self, meeting_id: str, title: str, start: str, end: str) -> dict | None:
        matches = [event for event in self.events if (
            event.get("meeting_id") == meeting_id
            or (event.get("meeting_id") is None
                and event.get("title") == title
                and _aware(event["start"]) == _aware(start)
                and _aware(event["end"]) == _aware(end))
        )]
        if len(matches) > 1:
            raise ValueError(f"multiple calendar events match meeting '{meeting_id}'")
        return matches[0] if matches else None

    def create_event(self, title: str, start: str, end: str, location: str = "",
                     meeting_id: str | None = None) -> dict:
        if meeting_id:
            existing = [
                event for event in self.events
                if event.get("meeting_id") == meeting_id
            ]
            if len(existing) > 1:
                raise ValueError(
                    f"multiple calendar events match meeting '{meeting_id}'"
                )
            if existing:
                return existing[0]

        ev = {"title": title, "start": start, "end": end, "location": location,
              "id": self._new_event_id()}
        if meeting_id:
            ev["meeting_id"] = meeting_id
        updated_events = [*self.events, ev]
        self.path.write_text(
            json.dumps(updated_events, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.events = updated_events
        return ev

    def move_event(self, meeting_id: str, old_title: str, old_start: str, old_end: str,
                   title: str, start: str, end: str, location: str = "",
                   allow_conflicts: bool = False) -> dict:
        old_event = self.find_event(meeting_id, old_title, old_start, old_end)
        if old_event is None:
            raise ValueError(f"no calendar event found for meeting '{meeting_id}'")
        conflicts = self.find_conflicts(
            start, end, old_event.get("id"), exclude_event=old_event)
        if conflicts and not allow_conflicts:
            raise ValueError(f"new time conflicts with calendar events: {json.dumps(conflicts)}")

        event_id = old_event.get("id") or self._new_event_id()
        updated_event = {
            "title": title,
            "start": start,
            "end": end,
            "location": location,
            "id": event_id,
            "meeting_id": meeting_id,
        }
        old_index = next(index for index, event in enumerate(self.events) if event is old_event)
        updated_events = list(self.events)
        updated_events[old_index] = updated_event
        self.path.write_text(
            json.dumps(updated_events, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.events = updated_events
        return updated_event

    def _new_event_id(self) -> str:
        existing_ids = {event.get("id") for event in self.events}
        candidate_number = len(self.events) + 1
        candidate = f"evt-{candidate_number}"
        while candidate in existing_ids:
            candidate_number += 1
            candidate = f"evt-{candidate_number}"
        return candidate

    def remove_event(self, meeting_id: str, title: str, start: str, end: str) -> dict:
        event = self.find_event(meeting_id, title, start, end)
        if event is None:
            return {"meeting_id": meeting_id, "removed": False}
        updated_events = [item for item in self.events if item is not event]
        self.path.write_text(
            json.dumps(updated_events, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.events = updated_events
        return {"meeting_id": meeting_id, "removed": True, "event": event}


class GoogleCalendar:
    """Reads and writes one employee's configured shared Google Calendar."""

    def __init__(self, name: str, interactive: bool = False):
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        cal_id = get_google_calendar_id(name)
        if not cal_id:
            raise ValueError(f"No Google Calendar is configured for employee '{name}'")

        self.calendar_id = cal_id
        client_secret_path = Path(os.getenv(
            "GOOGLE_CLIENT_SECRET_FILE",
            str(DATA / "credentials.json"),
        )).expanduser()
        token_path = Path(os.getenv(
            "GOOGLE_CALENDAR_TOKEN_FILE",
            str(DATA / "google_calendar_token.json"),
        )).expanduser()
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(token_path), GOOGLE_SCOPES)
        if creds and creds.valid and not creds.has_scopes(GOOGLE_SCOPES):
            creds = None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError as error:
                    if not interactive:
                        raise RuntimeError(
                            "Google token missing or expired, start an employee agent "
                            "to re-authorize"
                        ) from error
                    creds = None
            if not creds or not creds.valid:
                if not interactive:
                    raise RuntimeError(
                        "Google token missing or expired, start an employee agent "
                        "to re-authorize"
                    )
                if not client_secret_path.is_file():
                    raise FileNotFoundError(
                        "Google OAuth client secret file not found. Set "
                        "GOOGLE_CLIENT_SECRET_FILE to the downloaded JSON file."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(client_secret_path), GOOGLE_SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            _write_text_atomically(token_path, creds.to_json())
        self.svc = build("calendar", "v3", credentials=creds)

    def find_conflicts(self, start: str, end: str,
                       exclude_event_id: str | None = None,
                       exclude_event: dict | None = None,
                       exclude_meeting_id: str | None = None) -> list[dict]:
        _validate_interval(start, end)
        res = self.svc.events().list(
            calendarId=self.calendar_id,
            timeMin=_aware(start).isoformat(),
            timeMax=_aware(end).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        conflicts = []
        for event in res.get("items", []):
            if event.get("id") == exclude_event_id:
                continue
            normalized = self._normalize_event(event)
            if exclude_meeting_id and normalized.get("meeting_id") == exclude_meeting_id:
                continue
            if _overlaps(start, end, normalized["start"], normalized["end"]):
                conflicts.append(normalized)
        return conflicts

    def list_events(self) -> list[dict]:
        events = []
        page_token = None
        while True:
            response = self.svc.events().list(
                calendarId=self.calendar_id,
                timeMin=datetime.now(TZ).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
                pageToken=page_token,
            ).execute()
            events.extend(
                self._normalize_event(event)
                for event in response.get("items", [])
            )
            page_token = response.get("nextPageToken")
            if not page_token:
                return events

    @staticmethod
    def _normalize_event(event: dict) -> dict:
        private = event.get("extendedProperties", {}).get("private", {})
        return {"id": event["id"], "title": event.get("summary", "(no title)"),
                "start": event["start"].get("dateTime", event["start"].get("date")),
                "end": event["end"].get("dateTime", event["end"].get("date")),
                "location": event.get("location", ""),
                **({"meeting_id": private["meeting_id"]} if private.get("meeting_id") else {})}

    def find_event(self, meeting_id: str, title: str, start: str, end: str) -> dict | None:
        res = self.svc.events().list(
            calendarId=self.calendar_id,
            timeMin=(_aware(start) - timedelta(days=1)).isoformat(),
            timeMax=(_aware(end) + timedelta(days=1)).isoformat(),
            singleEvents=True,
        ).execute()
        matches = []
        for event in res.get("items", []):
            normalized = self._normalize_event(event)
            if (normalized.get("meeting_id") == meeting_id
                    or (not normalized.get("meeting_id")
                        and normalized["title"] == title
                        and _aware(normalized["start"]) == _aware(start)
                        and _aware(normalized["end"]) == _aware(end))):
                matches.append((event, normalized))
        if len(matches) > 1:
            raise ValueError(f"multiple calendar events match meeting '{meeting_id}'")
        return matches[0][1] if matches else None

    def create_event(self, title: str, start: str, end: str, location: str = "",
                     meeting_id: str | None = None) -> dict:
        if meeting_id:
            response = self.svc.events().list(
                calendarId=self.calendar_id,
                privateExtendedProperty=f"meeting_id={meeting_id}",
                maxResults=2,
            ).execute()
            existing = response.get("items", [])
            if len(existing) > 1:
                raise ValueError(
                    f"multiple calendar events match meeting '{meeting_id}'"
                )
            if existing:
                event = existing[0]
                return {
                    **self._normalize_event(event),
                    "link": event.get("htmlLink"),
                }

        body = {"summary": title, "location": location,
                "start": {"dateTime": _aware(start).isoformat(), "timeZone": TZ_NAME},
                "end": {"dateTime": _aware(end).isoformat(), "timeZone": TZ_NAME}}
        if meeting_id:
            body["extendedProperties"] = {"private": {"meeting_id": meeting_id}}
        ev = self.svc.events().insert(
            calendarId=self.calendar_id, body=body).execute()
        return {"id": ev["id"], "title": title, "start": start, "end": end,
                "location": location, "link": ev.get("htmlLink")}

    def move_event(self, meeting_id: str, old_title: str, old_start: str, old_end: str,
                   title: str, start: str, end: str, location: str = "",
                   allow_conflicts: bool = False) -> dict:
        old_event = self.find_event(meeting_id, old_title, old_start, old_end)
        if old_event is None:
            raise ValueError(f"no calendar event found for meeting '{meeting_id}'")
        conflicts = self.find_conflicts(start, end, old_event["id"])
        if conflicts and not allow_conflicts:
            raise ValueError(f"new time conflicts with calendar events: {json.dumps(conflicts)}")
        body = {
            "summary": title,
            "location": location,
            "start": {"dateTime": _aware(start).isoformat(), "timeZone": TZ_NAME},
            "end": {"dateTime": _aware(end).isoformat(), "timeZone": TZ_NAME},
            "extendedProperties": {"private": {"meeting_id": meeting_id}},
        }
        event = self.svc.events().update(
            calendarId=self.calendar_id, eventId=old_event["id"], body=body).execute()
        return {**self._normalize_event(event), "link": event.get("htmlLink")}

    def remove_event(self, meeting_id: str, title: str, start: str, end: str) -> dict:
        event = self.find_event(meeting_id, title, start, end)
        if event is None:
            return {"meeting_id": meeting_id, "removed": False}
        self.svc.events().delete(
            calendarId=self.calendar_id, eventId=event["id"]).execute()
        return {"meeting_id": meeting_id, "removed": True, "event": event}


def _write_text_atomically(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def get_calendar(name: str, interactive: bool = False):
    backend = os.getenv("CALENDAR_BACKEND", "fake").strip().lower()
    if backend != "google":
        return FakeCalendar(name)
    with _GOOGLE_CALENDARS_LOCK:
        if name not in _GOOGLE_CALENDARS:
            _GOOGLE_CALENDARS[name] = GoogleCalendar(name, interactive=interactive)
        return _GOOGLE_CALENDARS[name]


def create_calendar_for_employee(agent_id: str, display_name: str | None = None) -> dict:
    """Ensure a calendar exists for an employee, automatically provisioning it if needed.

    Returns:
        dict with {"backend": str, "calendar_id": str, "created": bool}
    """
    backend = os.getenv("CALENDAR_BACKEND", "fake").strip().lower()
    norm_id = str(agent_id).strip().lower()
    title_name = (display_name or norm_id.title()).strip()

    if backend != "google":
        cal_path = DATA / "calendars" / f"{norm_id}.json"
        cal_path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        if not cal_path.exists():
            _write_text_atomically(cal_path, "[]\n")
            created = True
        return {"backend": "fake", "calendar_id": norm_id, "created": created}

    # Google Calendar backend
    existing_id = get_google_calendar_id(norm_id)
    if existing_id:
        return {"backend": "google", "calendar_id": existing_id, "created": False}

    # Try creating secondary calendar via Google Calendar API
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    token_path = Path(os.getenv(
        "GOOGLE_CALENDAR_TOKEN_FILE",
        str(DATA / "google_calendar_token.json"),
    )).expanduser()
    if not token_path.exists():
        raise FileNotFoundError(f"Google token file not found at {token_path}")

    creds = Credentials.from_authorized_user_file(str(token_path))
    svc = build("calendar", "v3", credentials=creds)

    try:
        cal_body = {
            "summary": f"{title_name} (A2A Calendar)",
            "timeZone": TZ_NAME,
        }
        res = svc.calendars().insert(body=cal_body).execute()
        new_cal_id = res["id"]
        with _GOOGLE_CALENDARS_LOCK:
            GOOGLE_CALENDAR_IDS[norm_id] = new_cal_id
        return {"backend": "google", "calendar_id": new_cal_id, "created": True}
    except HttpError as error:
        if "insufficientPermissions" in str(error):
            raise PermissionError(
                "Google Calendar API requires 'https://www.googleapis.com/auth/calendar' scope "
                "to create secondary calendars automatically. Grant calendar permissions or configure calendar ID."
            ) from error
        raise

