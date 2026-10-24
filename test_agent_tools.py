import asyncio
import importlib
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents"))

import company_agent
from agents import calendar_backend
import employee_tools


class AgentSkillsTests(unittest.TestCase):
    def test_model_stays_pinned_to_gpt_oss_even_if_environment_requests_llama(self):
        from agents import llm

        with patch.dict(os.environ, {"GROQ_MODEL": "llama-3.3-70b-versatile"}):
            importlib.reload(llm)
            self.assertEqual(llm.MODEL, "openai/gpt-oss-120b")
        importlib.reload(llm)

    def test_company_agent_loads_runtime_skills_from_markdown(self):
        self.assertIn("remove_meeting", company_agent.SYSTEM)
        self.assertIn("move_event", company_agent.SYSTEM)

    def test_employee_agent_skills_are_parameterized_by_employee_name(self):
        from employee_agent import SYSTEM

        self.assertIn("{name}", SYSTEM)
        self.assertIn("check_conflicts", SYSTEM)

    def test_agent_entrypoints_do_not_override_shell_environment(self):
        with patch("dotenv.load_dotenv") as load_dotenv:
            importlib.reload(company_agent)
            importlib.import_module("employee_agent")

        self.assertEqual(load_dotenv.call_count, 2)
        self.assertTrue(all(
            call.kwargs.get("override") is False
            for call in load_dotenv.call_args_list
        ))


class CompanyToolTests(unittest.TestCase):
    def test_tool_schemas_and_dispatch_handlers_stay_in_sync(self):
        names = {tool["function"]["name"] for tool in company_agent.TOOLS}

        self.assertEqual(names, set(company_agent.IMPLS))
        self.assertEqual(company_agent.TOOLS, company_agent.company_tools.TOOLS)
        self.assertIn("remove_meeting", names)
        self.assertNotIn("cancel_meeting", names)
        self.assertIn("reschedule_meeting", names)
        self.assertIn("reschedule_the_meeting", names)

    def test_reschedule_meeting_tool_registered(self):
        self.assertIn("reschedule_meeting", company_agent.IMPLS)
        self.assertIn("reschedule_the_meeting", company_agent.IMPLS)
        self.assertTrue(callable(company_agent.reschedule_meeting))
        self.assertTrue(callable(company_agent.reschedule_the_meeting))

    def test_reschedule_meeting_delegates_to_move_event(self):
        with patch.object(company_agent.company_tools, "move_event", return_value="COMPLETED") as mock_move:
            result = company_agent.reschedule_meeting(
                meeting_id="sprint",
                start="2026-10-23T15:00:00",
                end="2026-10-23T16:00:00",
            )
            self.assertEqual(result, "COMPLETED")
            mock_move.assert_called_once_with("sprint", "2026-10-23T15:00:00", "2026-10-23T16:00:00", ask_user_fn=None)

    def test_reschedule_the_meeting_alias(self):
        with patch.object(company_agent.company_tools, "move_event", return_value="COMPLETED") as mock_move:
            result = company_agent.reschedule_the_meeting(
                meeting_id="sprint",
                new_start="2026-10-23T15:00:00",
                new_end="2026-10-23T16:00:00",
            )
            self.assertEqual(result, "COMPLETED")
            mock_move.assert_called_once_with("sprint", "2026-10-23T15:00:00", "2026-10-23T16:00:00", ask_user_fn=None)

    def test_tool_loop_persists_paused_tool_trace_for_chat_continuation(self):
        from agents import llm

        tool_call = SimpleNamespace(
            id="ask-1",
            function=SimpleNamespace(
                name="ask_user",
                arguments=json.dumps({"question": "Which time?"}),
            ),
        )
        responses = [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=None,
                tool_calls=[tool_call],
            ))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="Scheduling now.",
                tool_calls=None,
            ))]),
        ]
        requests = []

        class Completions:
            def create(self, **kwargs):
                requests.append(kwargs["messages"])
                return responses.pop(0)

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        messages = [{"role": "user", "content": "Schedule a review."}]
        waiting = []
        with patch.object(llm, "get_client", return_value=client):
            first = llm.run_tool_loop(
                "system",
                messages,
                [],
                {"ask_user": lambda question: waiting.append(question) or "Wait."},
                stop_when=lambda: bool(waiting),
                persist_messages=True,
            )
            messages.append({"role": "user", "content": "At 2 PM."})
            second = llm.run_tool_loop(
                "system",
                messages,
                [],
                {},
                persist_messages=True,
            )

        self.assertEqual(first, "")
        self.assertEqual(waiting, ["Which time?"])
        self.assertEqual(second, "Scheduling now.")
        self.assertEqual(messages[-2]["role"], "user")
        self.assertEqual(messages[-1], {
            "role": "assistant",
            "content": "Scheduling now.",
        })
        self.assertEqual(requests[1][-3]["role"], "assistant")
        self.assertEqual(requests[1][-2]["role"], "tool")
        self.assertEqual(requests[1][-1]["content"], "At 2 PM.")


class EmployeeToolTests(unittest.TestCase):
    def test_handlers_use_employee_calendar_and_record_tool_outcomes(self):
        calendar = Mock()
        event = {"id": "event-1", "meeting_id": "sprint"}
        conflicts = [{"title": "Client call"}]
        calendar.find_conflicts.return_value = conflicts
        calendar.create_event.return_value = event
        outcome = {}
        implementations = employee_tools.build_implementations(
            calendar,
            "meera",
            {"meeting_id": "sprint"},
            outcome,
        )

        self.assertEqual(
            json.loads(implementations["check_conflicts"](
                "2026-10-23T10:00:00",
                "2026-10-23T11:00:00",
            )),
            conflicts,
        )
        self.assertEqual(
            json.loads(implementations["create_event"](
                "Sprint review",
                "2026-10-23T10:00:00",
                "2026-10-23T11:00:00",
            )),
            event,
        )
        self.assertEqual(outcome["event"], event)
        self.assertEqual(
            implementations["ask_organizer"]("Can I move a client call?"),
            "Question sent to the organizer. Stop now.",
        )
        self.assertEqual(outcome["ask"], "Can I move a client call?")
        self.assertEqual(
            implementations["decline_meeting"]("On leave"),
            "Declined. Stop now.",
        )
        self.assertEqual(outcome["decline"], "On leave")

        calendar.find_conflicts.assert_called_once_with(
            "2026-10-23T10:00:00",
            "2026-10-23T11:00:00",
            exclude_event_id=None,
            exclude_meeting_id="sprint",
        )
        calendar.create_event.assert_called_once_with(
            "Sprint review",
            "2026-10-23T10:00:00",
            "2026-10-23T11:00:00",
            "",
            "sprint",
        )

    def test_employee_agent_loads_skill_file_and_routes_calendar_tools(self):
        from employee_agent import EmployeeExecutor, SYSTEM

        self.assertIn("{name}", SYSTEM)
        self.assertTrue(hasattr(employee_tools, "handle_move"))
        self.assertTrue(hasattr(employee_tools, "remove_meeting"))
        self.assertIn(
            "remove_meeting",
            {
                tool["function"]["name"]
                for tool in employee_tools.A2A_TOOLS
            },
        )
        self.assertIs(
            employee_tools.A2A_HANDLERS["remove_meeting"],
            employee_tools.remove_meeting,
        )
        self.assertFalse(hasattr(EmployeeExecutor, "_handle_move"))
        self.assertFalse(hasattr(EmployeeExecutor, "_handle_remove"))

    def test_employee_agent_recognizes_remove_meeting_a2a_operation(self):
        from a2a.types import Role
        from employee_agent import _calendar_request

        payload = json.dumps({
            "operation": "remove_meeting",
            "meeting_id": "sprint",
        })
        message = SimpleNamespace(
            message_id="remove-task",
            role=Role.ROLE_USER,
            parts=[SimpleNamespace(text=payload)],
        )
        context = SimpleNamespace(
            current_task=SimpleNamespace(history=[message]),
            message=message,
        )

        request, answers = _calendar_request(context)

        self.assertEqual(request["operation"], "remove_meeting")
        self.assertEqual(request["meeting_id"], "sprint")
        self.assertEqual(answers, [])

    def test_move_handler_completes_ready_move_using_calendar(self):
        calendar = Mock()
        calendar.find_event.return_value = {
            "id": "event-1",
            "meeting_id": "sprint",
        }
        calendar.find_conflicts.return_value = []
        calendar.move_event.return_value = {"id": "event-1", "start": "new"}
        updater = Mock()
        updater.add_artifact = AsyncMock()
        updater.complete = AsyncMock()
        updater.failed = AsyncMock()
        updater.requires_input = AsyncMock()
        updater.reject = AsyncMock()
        request = {
            "operation": "commit_move",
            "meeting_id": "sprint",
            "old_title": "Sprint review",
            "old_start": "2026-10-23T10:00:00",
            "old_end": "2026-10-23T11:00:00",
            "title": "Sprint review",
            "start": "2026-10-24T10:00:00",
            "end": "2026-10-24T11:00:00",
        }

        asyncio.run(employee_tools.handle_move(
            calendar, "meera", request, [], updater, lambda text: text))

        calendar.move_event.assert_called_once()
        updater.add_artifact.assert_awaited_once()
        updater.complete.assert_awaited_once()
        updater.failed.assert_not_awaited()

    def test_remove_meeting_tool_returns_completed_calendar_result(self):
        calendar = Mock()
        calendar.remove_event.return_value = {
            "meeting_id": "sprint",
            "removed": True,
        }
        updater = Mock()
        updater.add_artifact = AsyncMock()
        updater.complete = AsyncMock()
        updater.failed = AsyncMock()
        request = {
            "meeting_id": "sprint",
            "title": "Sprint review",
            "start": "2026-10-23T10:00:00",
            "end": "2026-10-23T11:00:00",
        }

        asyncio.run(employee_tools.remove_meeting(
            calendar, "meera", request, updater, lambda text: text))

        calendar.remove_event.assert_called_once_with(
            "sprint",
            "Sprint review",
            "2026-10-23T10:00:00",
            "2026-10-23T11:00:00",
        )
        updater.add_artifact.assert_awaited_once()
        updater.complete.assert_awaited_once()
        updater.failed.assert_not_awaited()

    def test_remove_meeting_tool_deletes_employee_calendar_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_path = Path(temp_dir) / "calendars" / "meera.json"
                calendar_path.parent.mkdir()
                calendar_path.write_text("[]", encoding="utf-8")
                calendar = calendar_backend.FakeCalendar("meera")
                calendar.create_event(
                    "Sprint review",
                    "2040-08-12T10:00:00",
                    "2040-08-12T11:00:00",
                    "Room 2",
                    "sprint",
                )
                updater = Mock()
                updater.add_artifact = AsyncMock()
                updater.complete = AsyncMock()
                updater.failed = AsyncMock()

                asyncio.run(employee_tools.remove_meeting(
                    calendar,
                    "meera",
                    {
                        "meeting_id": "sprint",
                        "title": "Sprint review",
                        "start": "2040-08-12T10:00:00",
                        "end": "2040-08-12T11:00:00",
                    },
                    updater,
                    lambda text: text,
                ))

                saved_events = json.loads(calendar_path.read_text(encoding="utf-8"))

        self.assertEqual(saved_events, [])
        updater.complete.assert_awaited_once()
        updater.failed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
