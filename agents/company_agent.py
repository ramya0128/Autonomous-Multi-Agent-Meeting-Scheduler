"""Company meeting agent (the A2A client).

Run with ``python company_agent.py`` and type a meeting request.
"""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

import company_tools
from llm import MODEL, run_tool_loop


SYSTEM = (Path(__file__).resolve().parent / "company" / "skills.md").read_text(
    encoding="utf-8"
)

TOOLS = company_tools.TOOLS
IMPLS = company_tools.IMPLS

# Keep the operation functions available to the dashboard and existing integrations.
list_meetings = company_tools.list_meetings
get_meeting = company_tools.get_meeting
create_meeting = company_tools.create_meeting
send_invite = company_tools.send_invite
add_invitee = company_tools.add_invitee
move_event = company_tools.move_event
reschedule_meeting = company_tools.reschedule_meeting
reschedule_the_meeting = company_tools.reschedule_the_meeting
remove_people = company_tools.remove_people
remove_meeting = company_tools.remove_meeting
cancel_meeting = company_tools.remove_meeting
reply_to_employee = company_tools.reply_to_employee
ask_user = company_tools.ask_user
find_alternative_times = company_tools.find_alternative_times
escalate_to_organizer = company_tools.escalate_to_organizer
list_registered_agents = company_tools.list_registered_agents
resolve_suggested_time_to_iso = company_tools.resolve_suggested_time_to_iso
apply_reschedule_to_meeting = company_tools.apply_reschedule_to_meeting


if __name__ == "__main__":
    history: list[dict] = []
    print(
        f"Company meeting agent ready (model: {MODEL}). "
        "Try: Schedule the Team Offsite for everyone invited. (quit to exit)"
    )
    while True:
        request = input("\nYou: ").strip()
        if request.lower() in ("quit", "exit"):
            break
        history.append({"role": "user", "content": request})
        try:
            implementations = dict(IMPLS)
            implementations["move_event"] = lambda **arguments: company_tools.move_event(
                **arguments,
                ask_user_fn=ask_user,
            )
            implementations["reschedule_meeting"] = lambda **arguments: company_tools.reschedule_meeting(
                **arguments,
                ask_user_fn=ask_user,
            )
            implementations["reschedule_the_meeting"] = lambda **arguments: company_tools.reschedule_the_meeting(
                **arguments,
                ask_user_fn=ask_user,
            )
            answer = run_tool_loop(
                SYSTEM, history, TOOLS, implementations, max_steps=20)
        except Exception as error:
            answer = f"(the agent failed: {type(error).__name__}: {error})"
        print(f"\nCompany agent: {answer}")
        history.append({"role": "assistant", "content": answer})
