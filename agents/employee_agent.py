"""Employee calendar agent. One process per employee:
    python employee_agent.py priya 9101
Receives a meeting invite over A2A, uses an LLM (Groq) with calendar tools, and ends in one of:
  COMPLETED       event created
  INPUT_REQUIRED  asks the organizer a question (e.g. clash) and waits for the answer
  REJECTED        employee cannot attend
  FAILED          something broke (LLM error, calendar error)
"""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

import employee_tools
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentSkill, Part, Role

from calendar_backend import get_calendar
from common import make_card, open_task, serve
from llm import MODEL, run_tool_loop

SYSTEM = (Path(__file__).resolve().parent / "employee" / "skills.md").read_text(
    encoding="utf-8"
)

TOOLS = employee_tools.TOOLS


def history_to_messages(context: RequestContext) -> list[dict]:
    """Rebuild the conversation for the LLM from the A2A task history (this is how a
    paused task 'remembers'). Merges consecutive same-role messages."""
    task = context.current_task
    items = list(task.history) if task else []
    if not any(m.message_id == context.message.message_id for m in items):
        items.append(context.message)
    out: list[dict] = []
    for m in items:
        text = "".join(p.text for p in m.parts if p.text).strip()
        if not text:
            continue
        role = "user" if m.role == Role.ROLE_USER else "assistant"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    return out


def _calendar_request(context: RequestContext) -> tuple[dict | None, list[str]]:
    task = context.current_task
    messages = list(task.history) if task else []
    if not any(message.message_id == context.message.message_id for message in messages):
        messages.append(context.message)
    user_texts = [
        "".join(part.text for part in message.parts if part.text).strip()
        for message in messages if message.role == Role.ROLE_USER
    ]
    if not user_texts:
        return None, []
    try:
        request = json.loads(user_texts[0])
    except json.JSONDecodeError:
        return None, []
    if request.get("operation") not in {
        "check_move",
        "commit_move",
        "remove_meeting",
        "remove_event",
    }:
        return None, []
    return request, user_texts[1:]


def _invite_from_messages(messages: list[dict]) -> dict:
    for message in messages:
        if message["role"] != "user" or "{" not in message["content"]:
            continue
        encoded = message["content"][message["content"].find("{"):]
        try:
            invite, _ = json.JSONDecoder().raw_decode(encoded)
        except json.JSONDecodeError:
            continue
        if isinstance(invite, dict):
            return invite
    raise ValueError("Invite message did not contain valid meeting JSON")


class EmployeeExecutor(AgentExecutor):
    def __init__(self, name: str):
        self.name = name
        self.calendar = get_calendar(name, interactive=True)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await open_task(context, event_queue)
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        say = lambda text: updater.new_agent_message([Part(text=text)])

        await updater.start_work(say(f"Checking {self.name}'s calendar..."))
        calendar_request, answers = _calendar_request(context)
        if calendar_request:
            operation_handler = employee_tools.A2A_HANDLERS.get(
                calendar_request["operation"]
            )
            if operation_handler:
                await operation_handler(
                    self.calendar,
                    self.name,
                    calendar_request,
                    updater,
                    say,
                )
            else:
                await employee_tools.handle_move(
                    self.calendar,
                    self.name,
                    calendar_request,
                    answers,
                    updater,
                    say,
                )
            return
        messages = history_to_messages(context)
        outcome: dict = {}
        try:
            invite = _invite_from_messages(messages)
        except ValueError as e:
            await updater.failed(say(str(e)))
            return
        impls = employee_tools.build_implementations(
            self.calendar, self.name, invite, outcome)
        try:
            final_text = await asyncio.to_thread(
                run_tool_loop, SYSTEM.replace("{name}", self.name), messages, TOOLS, impls,
                lambda: "ask" in outcome or "decline" in outcome or "event" in outcome)
        except Exception as e:      # LLM down, bad API key, calendar error...
            try:
                conflicts = self.calendar.find_conflicts(
                    invite.get("start", ""),
                    invite.get("end", ""),
                    exclude_meeting_id=invite.get("meeting_id"),
                )
                if not conflicts:
                    event = self.calendar.create_event(
                        invite.get("title", ""),
                        invite.get("start", ""),
                        invite.get("end", ""),
                        invite.get("location", ""),
                        invite.get("meeting_id"),
                    )
                    result = {
                        **event,
                        "employee": self.name,
                        "summary": f"Booked on calendar (model error bypassed: {type(e).__name__}: {e}).",
                    }
                    await updater.add_artifact([Part(text=json.dumps(result))], name="booking")
                    await updater.complete()
                    return
                else:
                    clash = conflicts[0]
                    c_title = clash.get("title", "Existing event")
                    c_time = f"({clash.get('start', '')} - {clash.get('end', '')})"
                    question = f"'{c_title}' {c_time} overlaps '{invite.get('title', 'Meeting')}'. Reschedule or decline?"
                    await updater.requires_input(say(question))
                    return
            except Exception as inner_err:
                await updater.failed(say(f"Could not process the invite: model error ({type(e).__name__}: {e}), calendar error ({type(inner_err).__name__}: {inner_err})"))
                return
            await updater.failed(say(f"Could not process the invite: {type(e).__name__}: {e}"))
            return

        if "ask" in outcome:
            await updater.requires_input(say(outcome["ask"]))
        elif "decline" in outcome:
            await updater.reject(say(outcome["decline"]))
        elif "event" in outcome:
            result = {**outcome["event"], "employee": self.name, "summary": final_text}
            await updater.add_artifact([Part(text=json.dumps(result))], name="booking")
            await updater.complete()
        else:
            last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            if any(w in last_user_msg.lower() for w in ("approve", "book anyway", "yes", "reschedule", "proceed")):
                event = self.calendar.create_event(
                    invite.get("title", ""),
                    invite.get("start", ""),
                    invite.get("end", ""),
                    invite.get("location", ""),
                    invite.get("meeting_id"),
                )
                result = {**event, "employee": self.name, "summary": final_text or "Reschedule approved and booked."}
                await updater.add_artifact([Part(text=json.dumps(result))], name="booking")
                await updater.complete()
            elif any(w in last_user_msg.lower() for w in ("decline", "can't", "skip", "cannot", "no")):
                await updater.reject(say(final_text or "Declined by employee"))
            else:
                conflicts = self.calendar.find_conflicts(
                    invite.get("start", ""),
                    invite.get("end", ""),
                    exclude_meeting_id=invite.get("meeting_id"),
                )
                if not conflicts:
                    event = self.calendar.create_event(
                        invite.get("title", ""),
                        invite.get("start", ""),
                        invite.get("end", ""),
                        invite.get("location", ""),
                        invite.get("meeting_id"),
                    )
                    result = {
                        **event,
                        "employee": self.name,
                        "summary": final_text or f"No calendar conflicts found. Confirmed and booked for {self.name.title()}.",
                    }
                    await updater.add_artifact([Part(text=json.dumps(result))], name="booking")
                    await updater.complete()
                else:
                    clash = conflicts[0]
                    c_title = clash.get("title", "Existing event")
                    c_time = f"({clash.get('start', '')} - {clash.get('end', '')})"
                    question = f"'{c_title}' {c_time} overlaps '{invite.get('title', 'Meeting')}'. Reschedule or decline?"
                    await updater.requires_input(say(question))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python employee_agent.py <name> <port>   e.g. priya 9101")
    name, port = sys.argv[1].lower(), int(sys.argv[2])
    skills = [
        AgentSkill(
            id="book_meeting",
            name="Book a meeting on this employee's calendar",
            description=(
                f"Receives a meeting invite (title, start, end, location) and books it on "
                f"{name.title()}'s calendar after checking for conflicts. May ask the organizer "
                f"a question if there is a clash, or reject if {name.title()} cannot attend. "
                f"Output: JSON of the created event."
            ),
            tags=["calendar", "meetings", "booking"],
            examples=[
                'Invite: {"title":"Team Offsite","start":"2026-10-23T15:00:00",'
                '"end":"2026-10-23T17:00:00","location":"Room 4B"}'
            ],
        ),
        AgentSkill(
            id="check_conflicts",
            name="Check calendar conflicts",
            description=(
                f"Checks {name.title()}'s calendar for overlapping events within the requested "
                f"start and end time window."
            ),
            tags=["calendar", "conflicts", "availability"],
        ),
        AgentSkill(
            id="reschedule_meeting",
            name="Reschedule meeting on calendar",
            description=(
                f"Handles two-phase reschedule protocol (check_move preflight and commit_move) "
                f"to move meetings safely without unapproved clashes."
            ),
            tags=["calendar", "reschedule", "move"],
        ),
        AgentSkill(
            id="cancel_meeting",
            name="Cancel or remove calendar meeting",
            description=(
                f"Removes and cleans up cancelled or deleted meetings from {name.title()}'s calendar."
            ),
            tags=["calendar", "cancel", "remove"],
        ),
    ]
    card = make_card(
        f"{name.title()}'s Calendar Agent",
        f"Manages {name.title()}'s calendar, checks A2A reschedule requests for "
        f"conflicts, and accepts meeting invites.",
        port,
        skills,
    )
    # Initialize executor (and calendar verification) first
    executor = EmployeeExecutor(name)

    try:
        import agent_registry
        skill_ids = [s.id for s in skills]
        existing = agent_registry.get_agent(name)
        if existing:
            agent_registry.update_agent_status(name, "online")
            agent_registry.update_agent_skills(name, skill_ids)
        else:
            display_name = agent_registry.get_employee_names().get(name, f"{name.title()}")
            agent_registry.register_agent(
                agent_id=name,
                url=f"http://127.0.0.1:{port}",
                name=display_name,
                role="employee",
                skills=skill_ids,
                status="online",
            )
    except Exception as reg_err:
        print(f"Warning: could not register with agent registry: {reg_err}")
    print(f"{name.title()}'s agent on :{port} (model: {MODEL})")
    serve(card, executor, port)
