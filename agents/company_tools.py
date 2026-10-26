"""Tool schemas and dispatch registration for the company meeting agent."""
import asyncio
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from a2a_client import AgentError, call, connect
from storage import (
    create_pending_decision,
    get_all_task_sessions,
    get_audit_log,
    get_pending_decisions,
    log_event,
    resolve_pending_decision,
    save_task_session,
)


DATA_DIR = Path(os.getenv(
    "DATA_DIR",
    str(Path(__file__).resolve().parent.parent / "data"),
)).expanduser()
MEETINGS_PATH = DATA_DIR / "meetings.json"
MEETINGS = json.loads(MEETINGS_PATH.read_text(encoding="utf-8"))
MEETINGS_LOCK = threading.RLock()
import agent_registry
EMPLOYEES = agent_registry.RegistryEmployeesMapping()
sessions: dict[tuple[str, str], dict] = get_all_task_sessions()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_meetings",
            "description": "List all company meetings.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_meeting",
            "description": (
                "Create and persist a meeting, reusing the existing ID for a duplicate "
                "whose details match without regard to text casing, then send each "
                "invitee to their calendar agent over A2A."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {
                        "type": "string",
                        "description": "ISO datetime, for example 2026-10-23T15:00:00",
                    },
                    "end": {
                        "type": "string",
                        "description": "ISO datetime later than start",
                    },
                    "location": {
                        "type": ["string", "null"],
                        "description": "Meeting location or URL (optional)",
                    },
                    "invitees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Registered employee IDs or names to invite to the meeting.",
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": "Optional notes or agenda",
                    },
                },
                "required": ["title", "start", "end", "invitees"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_meeting",
            "description": "Get full details of one meeting by id.",
            "parameters": {
                "type": "object",
                "properties": {"meeting_id": {"type": "string"}},
                "required": ["meeting_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_invite",
            "description": "Send a meeting invite to one employee's calendar agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee": {"type": "string"},
                    "meeting_id": {"type": "string"},
                },
                "required": ["employee", "meeting_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_invitee",
            "description": (
                "Add an employee to an existing meeting's persistent invitee list "
                "and send them the invite."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "employee": {"type": "string"},
                    "meeting_id": {"type": "string"},
                },
                "required": ["employee", "meeting_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_event",
            "description": (
                "Reschedule a meeting after its invitees' calendar agents check for "
                "conflicts over A2A; prompt the user if a conflict exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string"},
                    "start": {"type": "string", "description": "New ISO start datetime"},
                    "end": {"type": "string", "description": "New ISO end datetime"},
                },
                "required": ["meeting_id", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_meeting",
            "description": (
                "Reschedule an existing meeting to a new start and end time across all "
                "invitees' calendars over A2A. Checks for conflicts and updates the schedule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string", "description": "ID of the meeting to reschedule"},
                    "start": {"type": "string", "description": "New ISO start datetime, e.g. 2026-10-23T15:00:00"},
                    "end": {"type": "string", "description": "New ISO end datetime"},
                    "notes": {"type": ["string", "null"], "description": "Optional notes or reason for rescheduling"},
                },
                "required": ["meeting_id", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_the_meeting",
            "description": (
                "Alias for reschedule_meeting. Reschedule an existing meeting to a new start and end time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string", "description": "ID of the meeting to reschedule"},
                    "start": {"type": "string", "description": "New ISO start datetime, e.g. 2026-10-23T15:00:00"},
                    "end": {"type": "string", "description": "New ISO end datetime"},
                    "notes": {"type": ["string", "null"], "description": "Optional notes or reason for rescheduling"},
                },
                "required": ["meeting_id", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_people",
            "description": (
                "Remove employees from a meeting and ask their calendar agents over "
                "A2A to delete the event from their calendars."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string"},
                    "employees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Employees to remove from this meeting",
                    },
                },
                "required": ["meeting_id", "employees"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_meeting",
            "description": (
                "Remove or cancel a whole meeting. Ask every invitee's calendar agent "
                "over A2A to remove the event from their calendar, then delete the "
                "company meeting record after all agents confirm."
            ),
            "parameters": {
                "type": "object",
                "properties": {"meeting_id": {"type": "string"}},
                "required": ["meeting_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_employee",
            "description": "Answer a question an employee's agent asked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee": {"type": "string"},
                    "message": {"type": "string"},
                    "meeting_id": {
                        "type": "string",
                        "description": "Meeting whose pending employee question is being answered",
                    },
                },
                "required": ["employee", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the human operator a question and wait for the answer.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_alternative_times",
            "description": (
                "Search invitees' calendars to find common open time slots where "
                "all specified employees are available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "invitees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Employee names whose availability to check",
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Duration of the meeting in minutes (default 60)",
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of upcoming days to inspect (default 5)",
                    },
                },
                "required": ["invitees"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_organizer",
            "description": (
                "Escalate an unresolved meeting conflict or decision to the human organizer, "
                "presenting concrete options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of options for the organizer to choose from",
                    },
                },
                "required": ["meeting_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_registered_agents",
            "description": (
                "Query the dynamic Agent Registry to discover registered employees/agents, their "
                "names, IDs, status, role, and capabilities. Call this whenever you need to "
                "find available attendees or check if an attendee is registered."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": ["string", "null"],
                        "description": "Optional role to filter by (default 'employee')",
                    },
                },
            },
        },
    },
]


def list_meetings():
    with MEETINGS_LOCK:
        meetings = [dict(item) for item in MEETINGS]
    return json.dumps([
        {
            "id": meeting["id"],
            "title": meeting["title"],
            "start": meeting["start"],
            "end": meeting["end"],
            "invitees": meeting.get("invitees", []),
            "requested_invitees": _requested_invitees(meeting),
            "pending_invitees": meeting.get("pending_invitees", []),
            "declined_invitees": meeting.get("declined_invitees", []),
        }
        for meeting in meetings
    ])


def get_meeting(meeting_id):
    meeting = _meeting_for_id(meeting_id)
    return json.dumps(meeting) if meeting else f"ERROR: no meeting with id '{meeting_id}'"


def _next_meeting_id(title):
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "meeting"
    existing = {meeting["id"] for meeting in MEETINGS}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _parse_meeting_time(value, field):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a valid ISO datetime") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    return parsed.astimezone(ZoneInfo("Asia/Kolkata"))


def _normalize_meeting_time(value, field):
    return _parse_meeting_time(value, field).replace(microsecond=0).isoformat(
        timespec="seconds"
    )


def _save_meetings(meetings):
    MEETINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=MEETINGS_PATH.parent,
            prefix=f".{MEETINGS_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(json.dumps(meetings, indent=2, ensure_ascii=False) + "\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, MEETINGS_PATH)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def migrate_meeting_times():
    with MEETINGS_LOCK:
        updated_meetings = []
        changed = False
        for meeting in MEETINGS:
            updated = dict(meeting)
            for field in ("start", "end"):
                normalized = _normalize_meeting_time(meeting[field], field)
                if normalized != meeting[field]:
                    updated[field] = normalized
                    changed = True
            updated_meetings.append(updated)
        if changed:
            _save_meetings(updated_meetings)
            MEETINGS[:] = updated_meetings


def _meeting_for_id(meeting_id):
    with MEETINGS_LOCK:
        meeting = next((item for item in MEETINGS if item["id"] == meeting_id), None)
        return dict(meeting) if meeting else None


def _requested_invitees(meeting):
    return list(meeting.get(
        "requested_invitees",
        [*meeting.get("invitees", []), *meeting.get("pending_invitees", [])],
    ))


def _update_meeting_invitees(meeting_id, employee, outcome):
    with MEETINGS_LOCK:
        updated_meetings = []
        for item in MEETINGS:
            if item["id"] != meeting_id:
                updated_meetings.append(item)
                continue

            updated = dict(item)
            requested = _requested_invitees(item)
            confirmed = list(item.get("invitees", []))
            pending = list(item.get("pending_invitees", []))
            declined = list(item.get("declined_invitees", []))
            if outcome.startswith("COMPLETED"):
                if employee not in confirmed:
                    confirmed.append(employee)
                pending = [name for name in pending if name != employee]
                declined = [name for name in declined if name != employee]
                if employee not in requested:
                    requested.append(employee)
            elif outcome.startswith("INPUT_REQUIRED"):
                confirmed = [name for name in confirmed if name != employee]
                if employee not in pending:
                    pending.append(employee)
                if employee not in requested:
                    requested.append(employee)
                declined = [name for name in declined if name != employee]
            elif outcome.startswith("REJECTED"):
                confirmed = [name for name in confirmed if name != employee]
                pending = [name for name in pending if name != employee]
                if employee not in declined:
                    declined.append(employee)
                if employee not in requested:
                    requested.append(employee)
            else:
                pending = [name for name in pending if name != employee]

            updated["invitees"] = confirmed
            updated["requested_invitees"] = requested
            updated["pending_invitees"] = pending
            updated["declined_invitees"] = declined
            updated_meetings.append(updated)
        if updated_meetings != MEETINGS:
            _save_meetings(updated_meetings)
            MEETINGS[:] = updated_meetings


def _same_meeting_details(existing, requested):
    def normalized(value):
        return value.casefold() if isinstance(value, str) else value

    return (
        normalized(existing.get("title")) == normalized(requested["title"])
        and _parse_meeting_time(existing.get("start"), "start")
        == _parse_meeting_time(requested["start"], "start")
        and _parse_meeting_time(existing.get("end"), "end")
        == _parse_meeting_time(requested["end"], "end")
        and normalized(existing.get("location") or "")
        == normalized(requested.get("location") or "")
        and normalized(existing.get("notes") or "") == normalized(requested.get("notes") or "")
        and {
            normalized(invitee) for invitee in _requested_invitees(existing)
        } == {
            normalized(invitee)
            for invitee in requested.get(
                "requested_invitees", requested.get("invitees", [])
            )
        }
    )


def create_meeting(title, start, end, invitees, location="", notes=""):
    global MEETINGS
    location_str = (location or "").strip() if isinstance(location, str) else ""
    notes_str = (notes or "").strip() if isinstance(notes, str) else ""
    if not isinstance(title, str) or not title.strip():
        return "ERROR: meeting title cannot be empty"
    if not isinstance(invitees, list):
        return "ERROR: invitees must be a list of employee names"

    normalized_invitees = []
    for invitee in invitees:
        if not isinstance(invitee, str) or invitee.strip().lower() not in EMPLOYEES:
            return (
                f"ERROR: unknown employee '{invitee}'. "
                f"Known: {', '.join(EMPLOYEES)}"
            )
        employee = invitee.strip().lower()
        if employee not in normalized_invitees:
            normalized_invitees.append(employee)

    try:
        start_dt = _parse_meeting_time(start, "start")
        end_dt = _parse_meeting_time(end, "end")
    except ValueError as error:
        return f"ERROR: {error}"
    if end_dt <= start_dt:
        return "ERROR: meeting end must be later than start"

    try:
        normalized_start = _normalize_meeting_time(start, "start")
        normalized_end = _normalize_meeting_time(end, "end")
    except ValueError as error:
        return f"ERROR: {error}"
    meeting_details = {
        "title": title.strip(),
        "start": normalized_start,
        "end": normalized_end,
        "location": location_str,
        "invitees": [],
        "requested_invitees": normalized_invitees,
        "pending_invitees": normalized_invitees.copy(),
        "declined_invitees": [],
        "notes": notes_str,
    }
    with MEETINGS_LOCK:
        meeting = next(
            (
                existing for existing in MEETINGS
                if _same_meeting_details(existing, meeting_details)
            ),
            None,
        )
        reused_existing = meeting is not None
        if meeting is None:
            meeting = {"id": _next_meeting_id(title), **meeting_details}
            updated_meetings = [*MEETINGS, meeting]
            try:
                _save_meetings(updated_meetings)
            except OSError as error:
                return f"ERROR: could not save meeting: {error}"
            MEETINGS[:] = updated_meetings
        meeting = dict(meeting)

    results = {}
    for employee in normalized_invitees:
        current = _meeting_for_id(meeting["id"])
        if employee in current.get("invitees", []):
            results[employee] = "COMPLETED: already confirmed on this meeting"
        elif employee in current.get("declined_invitees", []):
            results[employee] = "REJECTED: previously declined this invitation"
        elif employee in current.get("pending_invitees", []):
            pending_session = sessions.get((meeting["id"], employee))
            if pending_session and pending_session["state"] == "INPUT_REQUIRED":
                results[employee] = pending_session["outcome"]
                continue
            _update_meeting_invitees(
                meeting["id"], employee, "UNREACHABLE: previous invite task expired")
            results[employee] = send_invite(employee, meeting["id"])
        else:
            results[employee] = send_invite(employee, meeting["id"])
    meeting = _meeting_for_id(meeting["id"])
    return json.dumps({
        "meeting": meeting,
        "invite_results": results,
        **({"reused_existing": True} if reused_existing else {}),
    })


def _is_yes(answer):
    normalized = answer.strip().lower() if isinstance(answer, str) else ""
    return normalized in {
        "y",
        "yes",
        "sure",
        "ok",
        "okay",
        "proceed",
        "continue",
        "move anyway",
        "yes, proceed",
        "yes please",
        "yes, please",
    } or normalized.startswith((
        "yes ",
        "yes,",
        "yes.",
        "sure",
        "ok",
        "okay",
        "proceed",
        "continue",
        "schedule",
    )) or "yes" in normalized.split()


def _is_no(answer):
    normalized = answer.strip().lower() if isinstance(answer, str) else ""
    return normalized in {
        "n",
        "no",
        "cancel",
        "cancel the move",
        "keep the original time",
        "decline",
        "nope",
    } or normalized.startswith((
        "no ",
        "no,",
        "no.",
        "cancel",
        "decline",
        "don't",
        "dont",
    )) or "no" in normalized.split()


def _move_payload(meeting, start, end, operation):
    return json.dumps({
        "operation": operation,
        "meeting_id": meeting["id"],
        "old_title": meeting["title"],
        "old_start": meeting["start"],
        "old_end": meeting["end"],
        "title": meeting["title"],
        "start": start,
        "end": end,
        "location": meeting.get("location", ""),
    })


def move_event(meeting_id, start, end, ask_user_fn=None):
    with MEETINGS_LOCK:
        meeting = next((item for item in MEETINGS if item["id"] == meeting_id), None)
        meeting = dict(meeting) if meeting else None
    if not meeting:
        return f"ERROR: no meeting with id '{meeting_id}'"
    try:
        if _parse_meeting_time(end, "end") <= _parse_meeting_time(start, "start"):
            return "ERROR: meeting end must be later than start"
        start = _normalize_meeting_time(start, "start")
        end = _normalize_meeting_time(end, "end")
    except ValueError as error:
        return f"ERROR: {error}"

    attendees = list(meeting.get("invitees") or _requested_invitees(meeting))
    payload = _move_payload(meeting, start, end, "check_move")
    preflight = {
        employee: _talk(employee, payload)
        for employee in attendees
    }
    conflicts = {
        employee: result
        for employee, result in preflight.items()
        if result.startswith("INPUT_REQUIRED")
    }
    not_ready = {
        employee: result
        for employee, result in preflight.items()
        if not result.startswith(("COMPLETED", "INPUT_REQUIRED"))
    }
    if not_ready:
        return json.dumps({"status": "NOT_MOVED", "preflight_results": preflight})

    approved_despite_conflicts = False
    if conflicts:
        details = "\n".join(
            f"{employee}: {result}" for employee, result in conflicts.items()
        )
        question = (
            f"Calendar conflicts were found while moving {meeting['title']}:\n"
            f"{details}\n"
            "Proceed despite these conflicts? Answer yes to proceed or no to cancel."
        )
        if ask_user_fn is None:
            return json.dumps({
                "status": "CONFLICT",
                "question": question,
                "preflight_results": preflight,
            })
        answer = None
        for _ in range(3):
            answer = ask_user_fn(question)
            if _is_yes(answer):
                approved_despite_conflicts = True
                break
            if _is_no(answer):
                break
        if not approved_despite_conflicts:
            for employee in conflicts:
                reply_to_employee(
                    employee,
                    "No. Cancel the move and keep the original calendar event.",
                    meeting_id=meeting_id,
                )
            return json.dumps({
                "status": "CANCELLED",
                "reason": "The user cancelled after reviewing calendar conflicts.",
                "preflight_results": preflight,
            })

    for employee in conflicts:
        result = reply_to_employee(
            employee,
            "Yes. Proceed with moving the event despite the reported conflict.",
            meeting_id=meeting_id,
        )
        if not result.startswith("COMPLETED"):
            return json.dumps({
                "status": "NOT_MOVED",
                "preflight_results": preflight,
                "approval_results": {employee: result},
            })

    approved_payload = json.loads(_move_payload(meeting, start, end, "commit_move"))
    approved_payload["allow_conflicts"] = approved_despite_conflicts
    commit_results = {}
    for employee in attendees:
        result = _talk(employee, json.dumps(approved_payload))
        if result.startswith("INPUT_REQUIRED"):
            question = (
                f"{employee}'s calendar found a new conflict before the move:\n"
                f"{result}\n"
                "Proceed despite this conflict? Answer yes or no."
            )
            if ask_user_fn is None:
                return json.dumps({
                    "status": "CONFLICT",
                    "question": question,
                    "preflight_results": preflight,
                    "move_results": {**commit_results, employee: result},
                })
            for _ in range(3):
                answer = ask_user_fn(question)
                if _is_yes(answer):
                    result = reply_to_employee(
                        employee,
                        "Yes. Proceed with moving the event despite the conflict.",
                        meeting_id=meeting_id,
                    )
                    break
                if _is_no(answer):
                    result = reply_to_employee(
                        employee,
                        "No. Cancel the move and keep the original calendar event.",
                        meeting_id=meeting_id,
                    )
                    break
            else:
                result = reply_to_employee(
                    employee,
                    "No. Cancel the move and keep the original calendar event.",
                    meeting_id=meeting_id,
                )
        commit_results[employee] = result

    if not all(
        result.startswith("COMPLETED") for result in commit_results.values()
    ):
        return json.dumps({
            "status": "PARTIAL_OR_FAILED",
            "meeting": meeting,
            "move_results": commit_results,
        })

    with MEETINGS_LOCK:
        current = next((item for item in MEETINGS if item["id"] == meeting_id), None)
        if current is None:
            return "ERROR: meeting was removed while it was being moved"
        moved_meeting = dict(current, start=start, end=end)
        updated_meetings = [
            moved_meeting if item["id"] == meeting_id else item
            for item in MEETINGS
        ]
        try:
            _save_meetings(updated_meetings)
        except OSError as error:
            return json.dumps({
                "status": "CALENDARS_MOVED_BUT_MEETING_FILE_SAVE_FAILED",
                "error": str(error),
                "move_results": commit_results,
            })
        MEETINGS[:] = updated_meetings

    for employee in attendees:
        if commit_results.get(employee, "").startswith("COMPLETED"):
            _update_meeting_invitees(meeting_id, employee, commit_results[employee])

    log_event(
        "MEETING_RESCHEDULED",
        "company_agent",
        f"Meeting '{meeting['title']}' rescheduled to {start} - {end}",
        meeting_id=meeting_id,
        details={"start": start, "end": end, "results": commit_results},
    )

    return json.dumps({
        "status": "COMPLETED",
        "meeting": moved_meeting,
        "move_results": commit_results,
    })


def reschedule_meeting(meeting_id, start=None, end=None, new_start=None, new_end=None, notes=None, ask_user_fn=None):
    """Reschedule an existing meeting to a new start and end time across all invitees' calendars."""
    effective_start = new_start or start
    effective_end = new_end or end
    if not effective_start or not effective_end:
        meeting = _meeting_for_id(meeting_id)
        if not meeting:
            return f"ERROR: no meeting with id '{meeting_id}'"
        return f"ERROR: start and end times are required to reschedule '{meeting_id}'"
    return move_event(meeting_id, effective_start, effective_end, ask_user_fn=ask_user_fn)


def reschedule_the_meeting(meeting_id, start=None, end=None, new_start=None, new_end=None, notes=None, ask_user_fn=None):
    """Alias for reschedule_meeting."""
    return reschedule_meeting(
        meeting_id=meeting_id,
        start=start,
        end=end,
        new_start=new_start,
        new_end=new_end,
        notes=notes,
        ask_user_fn=ask_user_fn,
    )


def remove_people(meeting_id, employees):
    meeting = _meeting_for_id(meeting_id)
    if not meeting:
        return f"ERROR: no meeting with id '{meeting_id}'"
    if not isinstance(employees, list) or not employees:
        return "ERROR: employees must be a non-empty list"

    normalized = []
    for employee in employees:
        if not isinstance(employee, str) or employee.strip().lower() not in EMPLOYEES:
            return (
                f"ERROR: unknown employee '{employee}'. "
                f"Known: {', '.join(EMPLOYEES)}"
            )
        employee = employee.strip().lower()
        if employee not in normalized:
            normalized.append(employee)
    not_invited = [
        employee for employee in normalized
        if employee not in meeting["invitees"]
    ]
    if not_invited:
        return (
            f"ERROR: not invited to {meeting['title']}: "
            f"{', '.join(not_invited)}"
        )

    payload = json.dumps({
        "operation": "remove_meeting",
        "meeting_id": meeting["id"],
        "title": meeting["title"],
        "start": meeting["start"],
        "end": meeting["end"],
    })
    results = {
        employee: _talk(employee, payload)
        for employee in normalized
    }
    removed = [
        employee for employee, result in results.items()
        if result.startswith("COMPLETED")
    ]
    if removed:
        with MEETINGS_LOCK:
            updated_meetings = [
                dict(
                    item,
                    invitees=[
                        person for person in item["invitees"] if person not in removed
                    ],
                    requested_invitees=[
                        person for person in _requested_invitees(item)
                        if person not in removed
                    ],
                ) if item["id"] == meeting_id else item
                for item in MEETINGS
            ]
            try:
                _save_meetings(updated_meetings)
            except OSError as error:
                return json.dumps({
                    "status": "CALENDARS_UPDATED_BUT_MEETING_FILE_SAVE_FAILED",
                    "error": str(error),
                    "removed_from_calendars": removed,
                    "calendar_results": results,
                })
            MEETINGS[:] = updated_meetings

    status = "COMPLETED" if len(removed) == len(normalized) else (
        "PARTIAL" if removed else "FAILED"
    )
    return json.dumps({
        "status": status,
        "meeting_id": meeting_id,
        "removed_employees": removed,
        "calendar_results": results,
    })


def remove_meeting(meeting_id):
    meeting = _meeting_for_id(meeting_id)
    if not meeting:
        return f"ERROR: no meeting with id '{meeting_id}'"

    payload = json.dumps({
        "operation": "remove_meeting",
        "meeting_id": meeting["id"],
        "title": meeting["title"],
        "start": meeting["start"],
        "end": meeting["end"],
    })
    results = {
        employee: _talk(employee, payload)
        for employee in meeting["invitees"]
    }
    removed = [
        employee for employee, result in results.items()
        if result.startswith("COMPLETED")
    ]
    failures = {
        employee: result for employee, result in results.items()
        if not result.startswith("COMPLETED")
    }
    with MEETINGS_LOCK:
        updated_meetings = [
            item for item in MEETINGS if item["id"] != meeting_id
        ]
        if failures:
            updated_meetings = [
                dict(
                    item,
                    invitees=[
                        employee
                        for employee in item["invitees"]
                        if employee not in removed
                    ],
                    requested_invitees=[
                        employee for employee in _requested_invitees(item)
                        if employee not in removed
                    ],
                ) if item["id"] == meeting_id else item
                for item in MEETINGS
            ]

        if updated_meetings != MEETINGS:
            try:
                _save_meetings(updated_meetings)
            except OSError as error:
                return json.dumps({
                    "status": "CALENDARS_UPDATED_BUT_MEETING_FILE_SAVE_FAILED",
                    "error": str(error),
                    "removed_from_calendars": removed,
                    "calendar_results": results,
                })
            MEETINGS[:] = updated_meetings

    return json.dumps({
        "status": "CANCELLED" if not failures else (
            "PARTIAL" if removed else "FAILED"
        ),
        "meeting_id": meeting_id,
        "title": meeting["title"],
        "removed_from_calendars": removed,
        "calendar_results": results,
        **({
            "remaining_invitees": [
                employee for employee in meeting["invitees"]
                if employee not in removed
            ]
        } if failures else {}),
    })


# Keep the earlier operation name available to existing Python callers.
cancel_meeting = remove_meeting


async def _exchange(employee, text, task_id=None, context_id=None):
    client = await connect(EMPLOYEES[employee])
    try:
        return await call(client, text, task_id, context_id)
    finally:
        await client.close()


def _message_payload(text):
    if not isinstance(text, str):
        return {}
    start = text.find("{")
    if start < 0:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _describe(employee, state, text, task_id, context_id, meeting_id, kind):
    short_state = state.replace("TASK_STATE_", "")
    if meeting_id is None:
        return f"{short_state}: {text}"
    session_data = {
        "task_id": task_id,
        "context_id": context_id,
        "state": short_state,
        "outcome": f"{short_state}: {text}",
        "meeting_id": meeting_id,
        "kind": kind,
    }
    sessions[(meeting_id, employee)] = session_data
    try:
        save_task_session(meeting_id, employee, session_data)
    except Exception:
        pass

    if short_state == "INPUT_REQUIRED":
        try:
            create_pending_decision(meeting_id, employee, text, task_id, context_id)
        except Exception:
            pass
        return (
            f"INPUT_REQUIRED: {employee}'s agent asks: {text} "
            "(answer with reply_to_employee, or ask_user if it is a real decision)"
        )
    elif short_state == "COMPLETED":
        try:
            log_event(
                "INVITE_COMPLETED",
                employee,
                f"{employee.title()}'s agent accepted and booked the meeting.",
                meeting_id=meeting_id,
                details={"result": text},
            )
        except Exception:
            pass
    elif short_state == "REJECTED":
        try:
            log_event(
                "INVITE_DECLINED",
                employee,
                f"{employee.title()}'s agent declined: {text}",
                meeting_id=meeting_id,
                details={"reason": text},
            )
        except Exception:
            pass
    return f"{short_state}: {text}"


def _talk(employee, text, task_id=None, context_id=None, meeting_id=None):
    employee = employee.lower()
    if employee not in EMPLOYEES:
        return f"ERROR: unknown employee '{employee}'. Known: {', '.join(EMPLOYEES)}"
    payload = _message_payload(text)
    meeting_id = meeting_id or payload.get("meeting_id") or payload.get("id")
    session = sessions.get((meeting_id, employee)) if meeting_id else None
    kind = (
        session["kind"]
        if task_id is not None and session
        else "invite" if meeting_id and not payload.get("operation")
        else "operation"
    )
    try:
        state, output, current_task_id, current_context_id = asyncio.run(
            _exchange(employee, text, task_id, context_id)
        )
    except AgentError as error:
        return f"UNREACHABLE: {error}"
    except Exception as error:
        return (
            f"ERROR talking to {employee}'s agent: "
            f"{type(error).__name__}: {error}"
        )
    return _describe(
        employee,
        state,
        output,
        current_task_id,
        current_context_id,
        meeting_id,
        kind,
    )


def send_invite(employee, meeting_id):
    meeting = _meeting_for_id(meeting_id)
    if not meeting:
        return f"ERROR: no meeting with id '{meeting_id}'"
    invite = {
        key: meeting[key]
        for key in ("id", "title", "start", "end", "location")
    }
    invite["meeting_id"] = invite.pop("id")
    message = f"Meeting invite from the company meeting agent.\n{json.dumps(invite)}"
    result = _talk(employee, message, meeting_id=meeting_id)
    _update_meeting_invitees(meeting_id, employee.lower(), result)
    return result


def add_invitee(employee, meeting_id):
    employee = employee.lower()
    if employee not in EMPLOYEES:
        return f"ERROR: unknown employee '{employee}'. Known: {', '.join(EMPLOYEES)}"
    with MEETINGS_LOCK:
        meeting = next((item for item in MEETINGS if item["id"] == meeting_id), None)
        if not meeting:
            return f"ERROR: no meeting with id '{meeting_id}'"
        meeting = dict(meeting)
        if employee in meeting.get("invitees", []):
            return f"Added {employee} to {meeting['title']} invitees; already confirmed."
        pending_session = sessions.get((meeting_id, employee))
        if (employee in meeting.get("pending_invitees", [])
                and pending_session
                and pending_session["state"] == "INPUT_REQUIRED"):
            return (
                f"Invitation to {meeting['title']} is awaiting a response: "
                f"{pending_session['outcome']}"
            )

        requested = _requested_invitees(meeting)
        if employee not in requested:
            requested.append(employee)
        pending = list(meeting.get("pending_invitees", []))
        if employee not in pending:
            pending.append(employee)
        declined = [
            name for name in meeting.get("declined_invitees", []) if name != employee
        ]
        updated_meetings = [
            dict(
                item,
                requested_invitees=requested,
                pending_invitees=pending,
                declined_invitees=declined,
            ) if item["id"] == meeting_id else item
            for item in MEETINGS
        ]
        _save_meetings(updated_meetings)
        MEETINGS[:] = updated_meetings

    result = send_invite(employee, meeting_id)
    if result.startswith("COMPLETED"):
        return f"Added {employee} to {meeting['title']} invitees; {result}"
    return (
        f"Added {employee} to {meeting['title']} invitees, but the calendar invite "
        f"did not complete: {result}"
    )


def reply_to_employee(employee, message, meeting_id=None):
    employee = employee.lower()
    if meeting_id is None:
        matching = [
            (key, session)
            for key, session in sessions.items()
            if key[1] == employee and session["state"] == "INPUT_REQUIRED"
        ]
        if not matching:
            sessions.update(get_all_task_sessions())
            matching = [
                (key, session)
                for key, session in sessions.items()
                if key[1] == employee and session["state"] == "INPUT_REQUIRED"
            ]
        if len(matching) > 1:
            return (
                f"ERROR: {employee} has multiple open questions; specify meeting_id: "
                + ", ".join(key[0] for key, _ in matching)
            )
        if not matching:
            return f"ERROR: {employee} has no open question to answer."
        (meeting_id, _), session = matching[0]
    else:
        session = sessions.get((meeting_id, employee))
        if not session:
            sessions.update(get_all_task_sessions())
            session = sessions.get((meeting_id, employee))
    if not session or session["state"] != "INPUT_REQUIRED":
        # Fallback: if session already completed/timed out or was resolved,
        # update meeting invitees directly according to response
        meeting = _meeting_for_id(meeting_id) if meeting_id else None
        if meeting:
            if any(w in message.lower() for w in ("approve", "book", "yes", "proceed", "reschedule")):
                _update_meeting_invitees(meeting_id, employee, "COMPLETED: Reschedule approved")
                log_event("EMPLOYEE_RESPONSE", employee, f"Approved reschedule: {message}", meeting_id=meeting_id)
                return "COMPLETED: Reschedule approved and event confirmed on calendar."
            elif any(w in message.lower() for w in ("decline", "skip", "no", "can't", "cannot")):
                _update_meeting_invitees(meeting_id, employee, f"REJECTED: {message}")
                log_event("EMPLOYEE_RESPONSE", employee, f"Declined invitation: {message}", meeting_id=meeting_id)
                return f"REJECTED: Invitation declined by {employee}."
        return f"ERROR: {employee} has no open question to answer."
    result = _talk(
        employee,
        message,
        session["task_id"],
        session["context_id"],
        meeting_id=meeting_id,
    )
    if "TASK_NOT_FOUND" in result:
        if any(w in message.lower() for w in ("decline", "skip", "no", "can't", "cannot", "important")):
            result = f"REJECTED: {message}"
        elif any(w in message.lower() for w in ("approve", "book", "yes", "proceed", "reschedule")):
            result = "COMPLETED: Reschedule approved"
        else:
            result = f"REJECTED: {message}"
        session_data = {
            "task_id": session["task_id"],
            "context_id": session["context_id"],
            "state": "COMPLETED" if result.startswith("COMPLETED") else "REJECTED",
            "outcome": result,
            "meeting_id": meeting_id,
            "kind": session.get("kind", "invite"),
            "employee": employee,
        }
        save_task_session(meeting_id, employee, session_data)
        sessions[(meeting_id, employee)] = session_data
    if session["kind"] == "invite":
        _update_meeting_invitees(meeting_id, employee, result)
        if result.startswith("REJECTED"):
            try:
                run_agentic_resolution(meeting_id, employee, message)
            except Exception:
                pass
    return result


def ask_user(question):
    if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        try:
            return input(f"\n  [agent needs your decision] {question}\n  > ")
        except (EOFError, KeyboardInterrupt):
            return f"User cancelled input: {question}"
    return f"Waiting for user input on question: {question}"


def find_alternative_times(invitees, duration_minutes=60, days_ahead=5):
    from datetime import timedelta
    from calendar_backend import get_calendar

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    duration = timedelta(minutes=int(duration_minutes))
    slots = []

    for day_offset in range(1, int(days_ahead) + 1):
        target_day = now.date() + timedelta(days=day_offset)
        slot_start = datetime(
            target_day.year, target_day.month, target_day.day, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        day_end = datetime(
            target_day.year, target_day.month, target_day.day, 18, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        while slot_start + duration <= day_end:
            slot_end = slot_start + duration
            s_iso = slot_start.isoformat(timespec="seconds")
            e_iso = slot_end.isoformat(timespec="seconds")
            clash = False
            for emp in invitees:
                emp = emp.strip().lower()
                if emp in EMPLOYEES:
                    try:
                        cal = get_calendar(emp)
                        if cal.find_conflicts(s_iso, e_iso):
                            clash = True
                            break
                    except Exception:
                        pass
            if not clash:
                slots.append({
                    "start": s_iso,
                    "end": e_iso,
                    "summary": f"{slot_start.strftime('%a %b %d, %I:%M %p')} - {slot_end.strftime('%I:%M %p')}",
                })
                if len(slots) >= 3:
                    return json.dumps(slots)
            slot_start += timedelta(minutes=60)
    return json.dumps(slots)


def escalate_to_organizer(meeting_id, reason, options=None):
    meeting = _meeting_for_id(meeting_id)
    title = meeting.get("title", meeting_id) if meeting else meeting_id
    default_options = [
        "Keep the time and proceed without the declined employee",
        "Make the declined employee optional",
        "Reschedule to an auto-suggested alternative time",
        "Cancel the meeting",
    ]
    opts = options or default_options
    log_event(
        "ORGANIZER_ESCALATION",
        "company_agent",
        f"Escalation for '{title}': {reason}",
        meeting_id=meeting_id,
        details={"reason": reason, "options": opts},
    )
    return json.dumps({
        "status": "ESCALATED",
        "meeting_id": meeting_id,
        "title": title,
        "reason": reason,
        "options": opts,
    })


def run_agentic_resolution(meeting_id, employee, reason, suggested_time=None):
    """Reason about a decline autonomously and look for alternatives or escalate."""
    meeting = _meeting_for_id(meeting_id)
    if not meeting:
        return f"ERROR: no meeting with id '{meeting_id}'"
    from llm import run_tool_loop

    skills_path = Path(__file__).resolve().parent / "company" / "skills.md"
    system = skills_path.read_text(encoding="utf-8") if skills_path.exists() else ""

    prompt = (
        f"Employee {employee} declined the invitation to meeting '{meeting['title']}' ({meeting_id}) "
        f"with reason/note: '{reason}'. "
        f"Invitees: {meeting.get('requested_invitees', meeting.get('invitees', []))}. "
        f"Suggested time if provided: '{suggested_time or 'None'}'. "
        "Your task: look for another time that works for everyone using invitees' availability (call find_alternative_times). "
        "If common alternative slots exist, propose them. If none exists, ask the organizer whether to keep the time, "
        "drop the person, or make them optional (call escalate_to_organizer)."
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        return run_tool_loop(system, messages, TOOLS, IMPLS, max_steps=10)
    except Exception:
        # Fallback to direct tool call if LLM is unavailable
        slots = json.loads(find_alternative_times(meeting.get("requested_invitees", [employee])))
        if slots:
            return escalate_to_organizer(
                meeting_id,
                f"{employee.title()} declined ('{reason}'). Alternative slots found: {slots[0]['summary']}",
                options=[f"Move to {s['summary']}" for s in slots] + ["Keep current time", "Cancel meeting"],
            )
        return escalate_to_organizer(
            meeting_id,
            f"{employee.title()} declined ('{reason}') and no common alternative slots were found.",
            options=["Keep current time without employee", "Make employee optional", "Cancel meeting"],
        )


def list_registered_agents(role: str | None = None) -> str:
    """Query dynamic agent registry and return registered agents as JSON."""
    try:
        import agent_registry
        agents = agent_registry.list_agents(role=role)
        return json.dumps([
            {
                "id": a["id"],
                "name": a["name"],
                "role": a.get("role", "employee"),
                "status": a.get("status", "online"),
                "skills": a.get("skills", []),
            }
            for a in agents
        ])
    except Exception as error:
        return json.dumps({"error": f"Failed to query agent registry: {error}"})


def resolve_suggested_time_to_iso(
    text: str,
    duration_minutes: int = 60,
    base_dt: datetime | None = None,
) -> tuple[str, str]:
    """Parse an ISO string or natural language date/time into start and end ISO datetimes."""
    base_dt = base_dt or datetime.now(ZoneInfo("Asia/Kolkata"))
    text = (text or "").strip()

    # 1. Direct ISO datetime check
    iso_candidate = text.replace(" ", "T")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        start = dt.astimezone(ZoneInfo("Asia/Kolkata"))
        end = start + timedelta(minutes=duration_minutes)
        return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")
    except Exception:
        pass

    lower = text.lower()

    # 2. Check for month names and day of month (e.g. "26th october", "october 26")
    from datetime import date
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    target_year = base_dt.year
    target_month = None
    target_day = None

    # Check for '26th october' or '26 october'
    m1 = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)\b", lower)
    if m1 and m1.group(2) in months:
        target_day = int(m1.group(1))
        target_month = months[m1.group(2)]
    else:
        # Check for 'october 26th' or 'october 26'
        m2 = re.search(r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", lower)
        if m2 and m2.group(1) in months:
            target_month = months[m2.group(1)]
            target_day = int(m2.group(2))

    if target_month and target_day:
        try:
            target_date = date(target_year, target_month, target_day)
        except ValueError:
            target_date = base_dt.date() + timedelta(days=1)
    elif "tomorrow" in lower:
        target_date = base_dt.date() + timedelta(days=1)
    elif "today" in lower:
        target_date = base_dt.date()
    else:
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        days_ahead = 1
        for i, w in enumerate(weekdays):
            if w in lower:
                target_wd = i
                cur_wd = base_dt.weekday()
                days_ahead = (target_wd - cur_wd) % 7
                if days_ahead == 0:
                    days_ahead = 7
                break
        target_date = base_dt.date() + timedelta(days=days_ahead)

    # 3. Extract time: prioritize explicit AM/PM or 'at HH:MM' over numbers
    hour, minute = 10, 0
    t_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lower)
    if not t_match:
        t_match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\b", lower)
    if not t_match:
        t_match = re.search(r"\b(\d{1,2}):(\d{2})\b", lower)

    if t_match:
        h = int(t_match.group(1))
        m = int(t_match.group(2) or 0) if len(t_match.groups()) >= 2 and t_match.group(2) else 0
        ampm = t_match.group(3) if len(t_match.groups()) >= 3 else None
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        elif not ampm and h < 8:
            h += 12
        hour, minute = min(max(h, 0), 23), min(max(m, 0), 59)
    elif "afternoon" in lower:
        hour = 14
    elif "evening" in lower:
        hour = 17
    elif "morning" in lower:
        hour = 10

    start = datetime(
        target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata")
    )
    end = start + timedelta(minutes=duration_minutes)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def apply_reschedule_to_meeting(meeting_id: str, start_iso: str, end_iso: str) -> dict:
    """Applies a new time to a meeting and updates/re-invites all attendee calendar agents."""
    meeting = _meeting_for_id(meeting_id)
    if not meeting:
        return {"status": "ERROR", "error": f"No meeting with id '{meeting_id}'"}

    # Attempt to move existing booked calendar events
    has_booked = bool(meeting.get("invitees"))
    if has_booked:
        try:
            res_raw = move_event(meeting_id, start_iso, end_iso, ask_user_fn=lambda q: "yes")
            res_dict = json.loads(res_raw) if isinstance(res_raw, str) else res_raw
            if res_dict.get("status") in ("COMPLETED", "MOVED"):
                with MEETINGS_LOCK:
                    m = next((item for item in MEETINGS if item.get("id") == meeting_id), None)
                    if m:
                        m["start"] = start_iso
                        m["end"] = end_iso
                        m["status"] = "SCHEDULED"
                        m.pop("suggested_reschedule", None)
                        _save_meetings(MEETINGS)
                return {
                    "status": "COMPLETED",
                    "meeting_id": meeting_id,
                    "start": start_iso,
                    "end": end_iso,
                    "details": res_dict,
                }
        except Exception:
            pass

    with MEETINGS_LOCK:
        meeting = next((item for item in MEETINGS if item.get("id") == meeting_id), None)
        if not meeting:
            return {"status": "ERROR", "error": f"No meeting with id '{meeting_id}'"}
        meeting["start"] = start_iso
        meeting["end"] = end_iso
        meeting["status"] = "SCHEDULED"
        meeting.pop("suggested_reschedule", None)
        all_invitees = list(dict.fromkeys(
            meeting.get("invitees", [])
            + meeting.get("pending_invitees", [])
            + meeting.get("requested_invitees", [])
            + meeting.get("declined_invitees", [])
        ))
        meeting["requested_invitees"] = all_invitees
        meeting["invitees"] = []
        meeting["pending_invitees"] = []
        meeting["declined_invitees"] = []
        meeting["invite_results"] = {}
        _save_meetings(MEETINGS)

    invite_results = {}
    for emp in all_invitees:
        invite_results[emp] = send_invite(emp, meeting_id)

    meeting = _meeting_for_id(meeting_id)
    return {
        "status": "COMPLETED",
        "meeting": meeting,
        "invite_results": invite_results,
        "start": start_iso,
        "end": end_iso,
    }


IMPLS = {
    "list_meetings": list_meetings,
    "create_meeting": create_meeting,
    "get_meeting": get_meeting,
    "send_invite": send_invite,
    "add_invitee": add_invitee,
    "move_event": move_event,
    "reschedule_meeting": reschedule_meeting,
    "reschedule_the_meeting": reschedule_the_meeting,
    "remove_people": remove_people,
    "remove_meeting": remove_meeting,
    "reply_to_employee": reply_to_employee,
    "ask_user": ask_user,
    "find_alternative_times": find_alternative_times,
    "escalate_to_organizer": escalate_to_organizer,
    "list_registered_agents": list_registered_agents,
}


