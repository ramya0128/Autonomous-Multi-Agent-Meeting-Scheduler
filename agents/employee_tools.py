"""Tool schemas and calendar tool handlers for employee agents."""
import json

from a2a.types import Part


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_conflicts",
            "description": (
                "Check this employee's actual calendar and return events whose "
                "time intervals overlap the requested start/end. Events that only "
                "touch the requested interval's boundaries are not conflicts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "ISO datetime, e.g. 2026-10-23T15:00:00",
                    },
                    "end": {"type": "string", "description": "ISO datetime"},
                    "exclude_event_id": {"type": ["string", "null"]},
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Add and persist the accepted event to this employee's calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "location": {"type": ["string", "null"]},
                    "meeting_id": {"type": ["string", "null"]},
                },
                "required": ["title", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_organizer",
            "description": "Pause and ask the meeting organizer one question.",
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
            "name": "decline_meeting",
            "description": "Decline the invite because the employee cannot attend.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_the_meeting",
            "description": "Approve rescheduling or book the meeting event on this employee's calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": ["string", "null"], "description": "Optional approval note or reason"},
                    "start": {"type": ["string", "null"], "description": "Optional new start datetime"},
                    "end": {"type": ["string", "null"], "description": "Optional new end datetime"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_meeting",
            "description": "Alias for reschedule_the_meeting. Approve rescheduling and book the meeting event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": ["string", "null"], "description": "Optional approval note or reason"},
                    "start": {"type": ["string", "null"], "description": "Optional new start datetime"},
                    "end": {"type": ["string", "null"], "description": "Optional new end datetime"},
                },
            },
        },
    },
]

A2A_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remove_meeting",
            "description": (
                "Remove the specified meeting from this employee's calendar when "
                "the company agent requests removal over A2A."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["meeting_id", "title", "start", "end"],
            },
        },
    },
]


def build_implementations(calendar, employee_name, invite, outcome):
    """Create handlers bound to one employee's calendar and current A2A task."""

    def check_conflicts(start, end, exclude_event_id=None):
        return json.dumps(
            calendar.find_conflicts(
                start,
                end,
                exclude_event_id=exclude_event_id,
                exclude_meeting_id=invite.get("meeting_id"),
            )
        )

    def create_event(title, start, end, location="", meeting_id=None):
        event = calendar.create_event(
            title,
            start,
            end,
            location,
            meeting_id or invite.get("meeting_id"),
        )
        outcome["event"] = event
        print(f"[{employee_name}] booked: {event}")
        return json.dumps(event)

    def ask_organizer(question):
        outcome["ask"] = question
        return "Question sent to the organizer. Stop now."

    def decline_meeting(reason):
        outcome["decline"] = reason
        return "Declined. Stop now."

    def reschedule_the_meeting(reason="Reschedule approved", start=None, end=None):
        event = calendar.create_event(
            invite.get("title", ""),
            start or invite.get("start"),
            end or invite.get("end"),
            invite.get("location", ""),
            invite.get("meeting_id"),
        )
        outcome["event"] = event
        print(f"[{employee_name}] reschedule approved and booked: {event}")
        return json.dumps(event)

    def reschedule_meeting(reason="Reschedule approved", start=None, end=None):
        return reschedule_the_meeting(reason=reason, start=start, end=end)

    return {
        "check_conflicts": check_conflicts,
        "create_event": create_event,
        "ask_organizer": ask_organizer,
        "decline_meeting": decline_meeting,
        "reschedule_the_meeting": reschedule_the_meeting,
        "reschedule_meeting": reschedule_meeting,
    }


async def handle_move(calendar, employee_name, request, answers, updater, say) -> None:
    try:
        old_event = calendar.find_event(
            request["meeting_id"],
            request["old_title"],
            request["old_start"],
            request["old_end"],
        )
        if old_event is None:
            await updater.failed(say(
                f"No calendar event found for meeting '{request['meeting_id']}'."
            ))
            return

        conflicts = calendar.find_conflicts(
            request["start"],
            request["end"],
            exclude_event_id=old_event.get("id"),
            exclude_event=old_event,
        )
        operation = request["operation"]
        approved = bool(request.get("allow_conflicts"))
        answer = answers[-1].strip().lower() if answers else ""
        answer_yes = answer in {"y", "proceed", "continue", "move anyway"} or (
            answer.startswith("yes")
        )
        answer_no = answer in {"n", "cancel"} or answer.startswith("no")

        if operation == "check_move":
            if conflicts and not answers:
                await updater.requires_input(say(json.dumps({
                    "status": "CONFLICT",
                    "meeting_id": request["meeting_id"],
                    "conflicts": conflicts,
                })))
            elif conflicts and answer_no:
                await updater.reject(say(
                    "MOVE_CANCELLED: the original event was kept."
                ))
            elif conflicts and not answer_yes:
                await updater.requires_input(say(json.dumps({
                    "status": "CONFLICT",
                    "meeting_id": request["meeting_id"],
                    "conflicts": conflicts,
                })))
            else:
                await updater.add_artifact(
                    [Part(text=json.dumps({
                        "status": "READY",
                        "meeting_id": request["meeting_id"],
                        "conflicts": conflicts,
                    }))],
                    name="move_check",
                )
                await updater.complete()
            return

        if conflicts and not approved and not answer_yes:
            if answer_no:
                await updater.reject(say(
                    "MOVE_CANCELLED: the original event was kept."
                ))
            else:
                await updater.requires_input(say(json.dumps({
                    "status": "CONFLICT",
                    "meeting_id": request["meeting_id"],
                    "conflicts": conflicts,
                })))
            return

        event = calendar.move_event(
            request["meeting_id"],
            request["old_title"],
            request["old_start"],
            request["old_end"],
            request["title"],
            request["start"],
            request["end"],
            request.get("location", ""),
            allow_conflicts=approved or answer_yes,
        )
    except (KeyError, TypeError, ValueError, OSError) as error:
        await updater.failed(say(
            f"Could not move calendar event: {type(error).__name__}: {error}"
        ))
        return

    await updater.add_artifact(
        [Part(text=json.dumps({
            "status": "MOVED",
            "employee": employee_name,
            "event": event,
        }))],
        name="moved_event",
    )
    await updater.complete()


async def remove_meeting(calendar, employee_name, request, updater, say) -> None:
    try:
        result = calendar.remove_event(
            request["meeting_id"],
            request["title"],
            request["start"],
            request["end"],
        )
    except (KeyError, TypeError, ValueError, OSError) as error:
        await updater.failed(say(
            f"Could not remove calendar event: {type(error).__name__}: {error}"
        ))
        return

    await updater.add_artifact(
        [Part(text=json.dumps({
            "status": "REMOVED" if result["removed"] else "ALREADY_ABSENT",
            "employee": employee_name,
            **result,
        }))],
        name="removed_event",
    )
    await updater.complete()


A2A_HANDLERS = {
    "remove_meeting": remove_meeting,
    "remove_event": remove_meeting,
}
