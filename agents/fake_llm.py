"""Offline stand-in for Groq, used ONLY for testing the A2A plumbing (LLM_MODE=fake).
It follows the same response shape as the Groq SDK but uses simple rules instead of reasoning."""
import json
import re
from types import SimpleNamespace as NS


def _call(name, **args):
    tc = NS(id="call_1", type="function", function=NS(name=name, arguments=json.dumps(args)))
    return NS(choices=[NS(message=NS(content=None, tool_calls=[tc]))])


def _text(t):
    return NS(choices=[NS(message=NS(content=t, tool_calls=None))])


def _invite(messages):
    first_user = next(m["content"] for m in messages if m["role"] == "user")
    return json.loads(re.search(r"\{.*\}", first_user, re.S).group(0))


class _Completions:
    def create(self, messages, **kw):
        last = messages[-1]
        inv = _invite(messages)
        if last["role"] == "tool":
            if last["name"] == "check_conflicts":
                conflicts = json.loads(last["content"])
                if not conflicts:
                    return _call("create_event", title=inv["title"], start=inv["start"],
                                 end=inv["end"], location=inv.get("location", ""),
                                 meeting_id=inv.get("meeting_id"))
                if any("leave" in c["title"].lower() for c in conflicts):
                    return _call("decline_meeting", reason=f"On leave ({conflicts[0]['title']}).")
                c = conflicts[0]
                return _call("ask_organizer",
                             question=f"Clash with '{c['title']}' ({c['start']}-{c['end']}). Book anyway or skip?")
            return _text("Booked.")
        users = [m for m in messages if m["role"] == "user"]
        if len(users) >= 2:                          # the employee or organizer answered our question
            ans = users[-1]["content"].lower()
            if "decline" in ans or "skip" in ans or "can't reschedule" in ans or "no" in ans.split():
                return _call("decline_meeting", reason="Declined: cannot reschedule.")
            if "suggest" in ans:
                return _call("decline_meeting", reason=f"Declined with suggested alternative: {users[-1]['content']}")
            return _call("create_event", title=inv["title"], start=inv["start"],
                         end=inv["end"], location=inv.get("location", ""),
                         meeting_id=inv.get("meeting_id"))
        return _call("check_conflicts", start=inv["start"], end=inv["end"])


class FakeClient:
    def __init__(self):
        self.chat = NS(completions=_Completions())
