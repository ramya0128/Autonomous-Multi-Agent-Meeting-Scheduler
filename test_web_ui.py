import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
from starlette.testclient import TestClient

import web_ui


class MeetingDashboardTests(unittest.TestCase):
    def setUp(self):
        self.original_meetings = web_ui.company.company_tools.MEETINGS
        self.original_path = web_ui.company.company_tools.MEETINGS_PATH
        self.original_sessions = dict(web_ui.company.company_tools.sessions)
        web_ui.company.company_tools.sessions.clear()
        self.original_chat_sessions = dict(web_ui.CHAT_SESSIONS)
        web_ui.CHAT_SESSIONS.clear()
        self.meetings = copy.deepcopy(self.original_meetings)
        if not any(item["id"] == "salary-discussion" for item in self.meetings):
            self.meetings.append({
                "id": "salary-discussion",
                "title": "Salary discussion",
                "start": "2040-08-12T10:00:00",
                "end": "2040-08-12T11:00:00",
                "location": "Room 1",
                "invitees": ["arjun"],
                "notes": "Test meeting",
            })
        if not any(item["id"] == "sprint" for item in self.meetings):
            self.meetings.append({
                "id": "sprint",
                "title": "Sprint Planning",
                "start": "2026-10-23T14:00:00+05:30",
                "end": "2026-10-23T15:00:00+05:30",
                "location": "Conference Room A",
                "invitees": ["meera"],
                "requested_invitees": ["meera"],
                "pending_invitees": [],
                "declined_invitees": [],
                "notes": "Weekly sprint planning",
            })
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_employees = web_ui.company.company_tools.EMPLOYEES
        web_ui.company.company_tools.EMPLOYEES = {
            "priya": "http://127.0.0.1:9101",
            "arjun": "http://127.0.0.1:9102",
            "meera": "http://127.0.0.1:9103",
        }
        web_ui.company.company_tools.MEETINGS = self.meetings
        web_ui.company.company_tools.MEETINGS_PATH = Path(self.temp_dir.name) / "meetings.json"
        web_ui.company.company_tools.MEETINGS_PATH.write_text(json.dumps(self.meetings), encoding="utf-8")
        self.original_proposals_path = web_ui.storage.PROPOSALS_PATH
        web_ui.storage.PROPOSALS_PATH = Path(self.temp_dir.name) / "pending_proposals.json"
        self.client = TestClient(web_ui.app)

    def tearDown(self):
        self.client.close()
        web_ui.storage.PROPOSALS_PATH = self.original_proposals_path
        web_ui.company.company_tools.EMPLOYEES = self.original_employees
        web_ui.company.company_tools.MEETINGS = self.original_meetings
        web_ui.company.company_tools.MEETINGS_PATH = self.original_path
        web_ui.company.company_tools.sessions.clear()
        web_ui.company.company_tools.sessions.update(self.original_sessions)
        web_ui.CHAT_SESSIONS.clear()
        web_ui.CHAT_SESSIONS.update(self.original_chat_sessions)
        self.temp_dir.cleanup()

    def test_dashboard_is_served(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Company meeting assistant", response.text)
        self.assertIn('id="chat-form"', response.text)
        self.assertIn('id="meeting-nav"', response.text)
        self.assertIn('data-meeting-filter="all"', response.text)
        self.assertNotIn('id="create-form"', response.text)
        self.assertIn("Remove meeting", response.text)
        self.assertIn('data-action="remove-meeting"', response.text)
        self.assertIn('id="employee-request-list"', response.text)
        self.assertNotIn('id="invite-prompt-dialog"', response.text)
        self.assertIn("Send to my calendar agent", response.text)
        self.assertIn("preflight_results", response.text)
        self.assertIn("move_results", response.text)
        self.assertIn("Agent response:", response.text)
        self.assertIn("Cannot reach the Gather dashboard server", response.text)
        self.assertNotIn('id="calendar-list"', response.text)

    def test_employee_profile_route_serves_dashboard_and_rejects_unknown_employee(self):
        response = self.client.get("/employees/priya")

        self.assertEqual(response.status_code, 200)
        self.assertIn("employee-request-list", response.text)
        self.assertEqual(self.client.get("/employees/unknown").status_code, 404)

    def test_state_exposes_open_employee_questions_for_employee_profile(self):
        web_ui.company.company_tools.sessions[("salary-discussion", "arjun")] = {
            "state": "INPUT_REQUIRED",
            "task_id": "task-1",
            "context_id": "context-1",
            "kind": "invite",
            "outcome": (
                "INPUT_REQUIRED: There is a client-call clash. Should I book the meeting "
                "anyway or decline? (answer with reply_to_employee)"
            ),
        }
        web_ui.company.company_tools.sessions[("salary-discussion", "meera")] = {
            "state": "INPUT_REQUIRED",
            "task_id": "move-task",
            "context_id": "move-context",
            "kind": "operation",
            "outcome": "INPUT_REQUIRED: company-side move preflight conflict",
        }
        with (
            patch.object(web_ui, "_employee_statuses", return_value=[]),
            patch.object(web_ui, "_load_calendar_events", return_value=([], None)),
        ):
            response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["employee_requests"], [{
            "meeting_id": "salary-discussion",
            "employee": "arjun",
            "prompt": "There is a client-call clash. Should I book the meeting anyway or decline?",
            "meeting_title": "Salary discussion",
            "start": "2040-08-12T10:00:00",
            "end": "2040-08-12T11:00:00",
            "kind": "invite",
        }])

    def test_chat_api_pauses_for_a_question_and_continues_same_conversation(self):
        calls = []

        def run_chat(system, messages, tools, implementations, **options):
            calls.append((system, list(messages), options))
            if len(calls) == 1:
                self.assertTrue(options["stop_when"] is not None)
                implementations["ask_user"]("Which time should I use?")
                messages.extend([
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "ask-1",
                            "type": "function",
                            "function": {"name": "ask_user", "arguments": "{}"},
                        }],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "ask-1",
                        "name": "ask_user",
                        "content": "Waiting for the user's next chat message.",
                    },
                ])
                return ""
            self.assertEqual(messages[-1], {
                "role": "user",
                "content": "Tomorrow at 2 PM.",
            })
            self.assertEqual(messages[-2]["role"], "tool")
            return "I’ll schedule it for tomorrow at 2 PM."

        with patch.object(web_ui.company, "run_tool_loop", side_effect=run_chat):
            first = self.client.post("/api/chat", json={
                "message": "Schedule a review with Priya. Which time?",
            })
            conversation_id = first.json()["conversation_id"]
            second = self.client.post("/api/chat", json={
                "conversation_id": conversation_id,
                "message": "Tomorrow at 2 PM.",
            })

        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["needs_response"])
        self.assertEqual(first.json()["assistant"], "Which time should I use?")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(second.json()["needs_response"])
        self.assertEqual(
            second.json()["assistant"],
            "I’ll schedule it for tomorrow at 2 PM.",
        )
        self.assertEqual(second.json()["conversation_id"], conversation_id)
        self.assertIn("Current date and time in Asia/Kolkata", calls[0][0])

    def test_chat_reschedule_conflict_returns_structured_question_without_stdin(self):
        def run_chat(_system, _messages, _tools, implementations, **_options):
            return implementations["move_event"](
                meeting_id="salary-discussion",
                start="2040-08-13T10:00:00",
                end="2040-08-13T11:00:00",
            )

        with (
            patch.object(
                web_ui.company.company_tools,
                "_talk",
                return_value="INPUT_REQUIRED: Client call overlaps",
            ),
            patch.object(web_ui.company, "run_tool_loop", side_effect=run_chat),
            patch("builtins.input", side_effect=AssertionError("chat must not read stdin")),
        ):
            response = self.client.post("/api/chat", json={"message": "Move my meeting"})

        self.assertEqual(response.status_code, 200, response.text)
        result = json.loads(response.json()["assistant"])
        self.assertEqual(result["status"], "CONFLICT")
        self.assertIn("Proceed despite these conflicts", result["question"])

    def test_chat_api_rejects_empty_or_non_object_requests(self):
        self.assertEqual(
            self.client.post("/api/chat", json={"message": " "}).status_code,
            400,
        )
        response = self.client.post("/api/chat", json=[])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"],
            "The chat request must be a JSON object",
        )

    def test_mutation_endpoints_reject_non_object_json_bodies(self):
        job_id = "invalid-body-test"
        web_ui.MOVE_JOBS[job_id] = web_ui.MoveJob()
        paths = [
            "/api/meetings",
            "/api/meetings/salary-discussion/invitees",
            "/api/meetings/salary-discussion/invitees/arjun/reply",
            "/api/meetings/salary-discussion/remove-people",
            "/api/meetings/salary-discussion/move",
            f"/api/moves/{job_id}/decision",
        ]
        try:
            for path in paths:
                with self.subTest(path=path):
                    response = self.client.post(path, json=[])
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn("JSON object", response.json()["error"])
        finally:
            web_ui.MOVE_JOBS.pop(job_id, None)

    def test_expired_move_jobs_and_idle_chat_sessions_are_purged(self):
        move_job_id = "expired-job-test"
        web_ui.MOVE_JOBS[move_job_id] = web_ui.MoveJob()
        web_ui.MOVE_JOBS[move_job_id].state = "complete"
        web_ui.MOVE_JOBS[move_job_id].updated_at -= 601
        web_ui.CHAT_SESSIONS["expired-chat-test"] = {
            "messages": [],
            "updated_at": time.monotonic() - 7201,
        }

        web_ui._purge_expired_state()

        self.assertNotIn(move_job_id, web_ui.MOVE_JOBS)
        self.assertNotIn("expired-chat-test", web_ui.CHAT_SESSIONS)

    def test_employee_calendar_page_is_served_with_employee_navigation(self):
        response = self.client.get("/calendars")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="calendar-page"', response.text)
        self.assertIn('id="calendar-employee-nav"', response.text)
        self.assertIn('href="/calendars"', response.text)

    def test_state_reads_google_events_for_each_employee_calendar(self):
        google_events = {
            "priya": [{"id": "p-event", "title": "Priya event"}],
            "arjun": [{"id": "a-event", "title": "Arjun event"}],
            "meera": [{"id": "m-event", "title": "Meera event"}],
        }
        with (
            patch.dict("os.environ", {"CALENDAR_BACKEND": "google"}),
            patch.object(
                web_ui,
                "_employee_statuses",
                return_value=[
                    {"id": name, "name": name.title(), "status": "online", "url": ""}
                    for name in ("priya", "arjun", "meera")
                ],
            ),
            patch.object(
                web_ui,
                "get_calendar",
                side_effect=lambda name: type(
                    "CalendarReader",
                    (),
                    {"list_events": lambda _self: google_events[name]},
                )(),
            ) as get_calendar,
        ):
            response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["calendars"], google_events)
        self.assertEqual(response.json()["calendar_errors"], {})
        self.assertEqual(get_calendar.call_count, 3)

    def test_state_reports_google_calendar_offline_without_loading_fake_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calendar_dir = Path(temp_dir) / "calendars"
            calendar_dir.mkdir()
            fake_events = [{"id": "local-event", "title": "Must not be returned"}]
            for employee in ("priya", "arjun", "meera"):
                (calendar_dir / f"{employee}.json").write_text(
                    json.dumps(fake_events),
                    encoding="utf-8",
                )
            with (
                patch.object(web_ui, "DATA", Path(temp_dir)),
                patch.dict("os.environ", {"CALENDAR_BACKEND": "google"}),
                patch.object(web_ui, "_employee_statuses", return_value=[
                    {"id": name, "name": name.title(), "status": "online", "url": ""}
                    for name in ("priya", "arjun", "meera")
                ]),
                patch.object(web_ui, "get_calendar", side_effect=RuntimeError(
                    "Unable to find the server at www.googleapis.com"
                )),
            ):
                response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["calendars"], {
            "priya": [],
            "arjun": [],
            "meera": [],
        })
        self.assertEqual(response.json()["calendar_errors"], {
            name: "RuntimeError: Unable to find the server at www.googleapis.com"
            for name in ("priya", "arjun", "meera")
        })
        self.assertIn("Google Calendar is offline or unavailable", web_ui.INDEX.read_text(
            encoding="utf-8"
        ))

    def test_create_meeting_api_persists_and_reports_a2a_invites(self):
        with patch.object(web_ui.company.company_tools, "send_invite",
                          return_value="COMPLETED: booked over A2A") as send:
            response = self.client.post("/api/meetings", json={
                "title": "UI Planning Session",
                "start": "2040-08-12T10:00",
                "end": "2040-08-12T11:00",
                "invitees": ["meera"],
                "location": "Room 2",
                "notes": "Dashboard test",
            })

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        saved = json.loads(web_ui.company.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(body["meeting"]["id"], "ui-planning-session")
        self.assertEqual(body["invite_results"]["meera"], "COMPLETED: booked over A2A")
        self.assertEqual(saved[-1], body["meeting"])
        send.assert_called_once_with("meera", "ui-planning-session")

    def test_create_meeting_api_returns_conflict_prompt_explicitly(self):
        with patch.object(
            web_ui.company.company_tools,
            "send_invite",
            return_value=(
                "INPUT_REQUIRED: meera's agent asks: There is already a conflicting "
                "event. Replace it or decline? (answer with reply_to_employee)"
            ),
        ):
            response = self.client.post("/api/meetings", json={
                "title": "Conflict prompt test",
                "start": "2040-08-12T10:00",
                "end": "2040-08-12T11:00",
                "invitees": ["meera"],
            })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["invite_prompts"]["meera"],
            "meera's agent asks: There is already a conflicting event. Replace it or decline?",
        )

    def test_create_meeting_api_reuses_exact_duplicate_id(self):
        existing = next(
            item for item in self.meetings if item["id"] == "salary-discussion"
        )
        with patch.object(
            web_ui.company.company_tools,
            "send_invite",
            return_value="COMPLETED: existing event reused",
        ) as send:
            response = self.client.post("/api/meetings", json={
                "title": existing["title"],
                "start": existing["start"],
                "end": existing["end"],
                "invitees": existing["invitees"],
                "location": existing["location"],
                "notes": existing["notes"],
            })

        saved = json.loads(
            web_ui.company.company_tools.MEETINGS_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["reused_existing"])
        self.assertEqual(response.json()["meeting"]["id"], "salary-discussion")
        self.assertEqual(
            sum(item["id"] == "salary-discussion" for item in saved),
            1,
        )
        send.assert_not_called()

    def test_create_meeting_api_returns_validation_errors(self):
        response = self.client.post("/api/meetings", json={
            "title": "Bad time",
            "start": "not-a-datetime",
            "end": "2040-08-12T11:00",
            "invitees": [],
        })

        self.assertEqual(response.status_code, 400)
        self.assertIn("valid ISO datetime", response.json()["error"])

    def test_invitee_reply_api_resumes_employee_task(self):
        with patch.object(
            web_ui.company,
            "reply_to_employee",
            return_value="COMPLETED: meeting booked",
        ) as reply:
            response = self.client.post(
                "/api/meetings/salary-discussion/invitees/arjun/reply",
                json={"message": "Yes, book it anyway."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "employee": "arjun",
            "outcome": "COMPLETED: meeting booked",
            "prompt": None,
        })
        reply.assert_called_once_with(
            "arjun", "Yes, book it anyway.", "salary-discussion")

    def test_pending_invitee_reply_updates_company_record(self):
        meeting = next(
            item for item in self.meetings if item["id"] == "salary-discussion"
        )
        meeting["invitees"] = []
        meeting["requested_invitees"] = ["arjun"]
        meeting["pending_invitees"] = ["arjun"]
        web_ui.company.company_tools.sessions[("salary-discussion", "arjun")] = {
            "state": "INPUT_REQUIRED",
            "task_id": "task-1",
            "context_id": "context-1",
            "kind": "invite",
            "outcome": "INPUT_REQUIRED: resolve a conflict",
        }

        async def reject_invitation(employee, message, task_id, context_id):
            self.assertEqual((employee, message), ("arjun", "Decline this invite."))
            self.assertEqual((task_id, context_id), ("task-1", "context-1"))
            return "TASK_STATE_REJECTED", "Invitation declined", task_id, context_id

        with patch.object(
            web_ui.company.company_tools,
            "_exchange",
            side_effect=reject_invitation,
        ):
            response = self.client.post(
                "/api/meetings/salary-discussion/invitees/arjun/reply",
                json={"message": "Decline this invite."},
            )

        saved = json.loads(
            web_ui.company.company_tools.MEETINGS_PATH.read_text(encoding="utf-8")
        )
        meeting = next(item for item in saved if item["id"] == "salary-discussion")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(meeting["invitees"], [])
        self.assertEqual(meeting["pending_invitees"], [])
        self.assertEqual(meeting["declined_invitees"], ["arjun"])

    def test_invitee_reply_api_rejects_employee_not_invited(self):
        response = self.client.post(
            "/api/meetings/salary-discussion/invitees/meera/reply",
            json={"message": "Yes"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not invited", response.json()["error"])

    def test_invitee_reply_api_accepts_employee_in_requested_invitees(self):
        # Even if meeting['invitees'] is empty, requested_invitees should be accepted
        meetings = [
            {
                "id": "mors",
                "title": "mors",
                "start": "2026-10-20T09:29:00",
                "end": "2026-10-20T10:00:00",
                "invitees": [],
                "requested_invitees": ["arjun"],
                "pending_invitees": [],
                "declined_invitees": [],
            }
        ]
        with patch.object(web_ui.company.company_tools, "MEETINGS", meetings), \
             patch.object(web_ui.company, "reply_to_employee", return_value="COMPLETED: Reschedule approved") as reply:
            response = self.client.post(
                "/api/meetings/mors/invitees/arjun/reply",
                json={"message": "Approve reschedule"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("COMPLETED", response.json()["outcome"])
            reply.assert_called_once_with("arjun", "Approve reschedule", "mors")

    def test_reschedule_meeting_api_route(self):
        response = self.client.post(
            "/api/meetings/sprint/reschedule",
            json={"start": "2026-10-23T15:00:00", "end": "2026-10-23T16:00:00"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertIn("job_id", response.json())

    def test_remove_meeting_api_calls_company_remove_meeting_tool(self):
        with patch.object(web_ui.company, "remove_meeting", return_value=json.dumps({
            "status": "CANCELLED",
            "meeting_id": "sprint",
            "removed_from_calendars": ["meera"],
            "calendar_results": {"meera": "COMPLETED: removed"},
        })) as remove:
            response = self.client.post("/api/meetings/sprint/remove", json={})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "CANCELLED")
        remove.assert_called_once_with("sprint")

    def test_remove_meeting_api_runs_company_operation_and_persists_removal(self):
        meeting = next(
            item for item in self.meetings if item["id"] == "salary-discussion"
        )
        self.assertEqual(meeting["invitees"], ["arjun"])
        with patch.object(
            web_ui.company.company_tools,
            "_talk",
            return_value="COMPLETED: calendar event removed",
        ) as talk:
            response = self.client.post(
                "/api/meetings/salary-discussion/remove",
                json={},
            )

        saved = json.loads(
            web_ui.company.company_tools.MEETINGS_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "CANCELLED")
        self.assertNotIn(
            "salary-discussion",
            [item["id"] for item in saved],
        )
        self.assertEqual(talk.call_count, 1)
        payload = json.loads(talk.call_args.args[1])
        self.assertEqual(payload["operation"], "remove_meeting")
        self.assertEqual(payload["meeting_id"], "salary-discussion")

    def test_remove_meeting_api_returns_not_found_for_unknown_meeting(self):
        with patch.object(web_ui.company, "remove_meeting") as remove:
            response = self.client.post("/api/meetings/missing-meeting/remove", json={})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["error"],
            "No meeting with id 'missing-meeting'",
        )
        remove.assert_not_called()

    def test_cancel_route_remains_an_alias_for_existing_clients(self):
        with patch.object(web_ui.company, "remove_meeting", return_value=json.dumps({
            "status": "CANCELLED",
            "meeting_id": "sprint",
            "removed_from_calendars": [],
            "calendar_results": {},
        })) as remove:
            response = self.client.post("/api/meetings/sprint/cancel", json={})

        self.assertEqual(response.status_code, 200)
        remove.assert_called_once_with("sprint")

    def test_employee_suggest_time_creates_proposal_and_notifies_organizer(self):
        # 1. Employee replies suggesting another time
        reply_payload = {
            "message": "Suggest another time: Tomorrow at 3:00 PM (Previous commitment)",
        }
        with patch.object(web_ui.company, "reply_to_employee", return_value="COMPLETED"):
            resp = self.client.post("/api/meetings/sprint/invitees/meera/reply", json=reply_payload)
        self.assertEqual(resp.status_code, 200)

        # 2. Check pending proposal in storage and state
        proposals = web_ui.storage.get_pending_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["meeting_id"], "sprint")
        self.assertEqual(proposals[0]["employee"], "meera")
        self.assertEqual(proposals[0]["suggested_time"], "Tomorrow at 3:00 PM")
        self.assertEqual(proposals[0]["status"], "PENDING")

        state_resp = self.client.get("/api/state")
        self.assertEqual(state_resp.status_code, 200)
        state_data = state_resp.json()
        self.assertIn("pending_proposals", state_data)
        self.assertEqual(len(state_data["pending_proposals"]), 1)
        self.assertEqual(state_data["pending_proposals"][0]["id"], proposals[0]["id"])

        # 3. Check meeting object was updated to PENDING_ORGANIZER_APPROVAL
        m = next((item for item in web_ui.company.company_tools.MEETINGS if item["id"] == "sprint"), None)
        self.assertIsNotNone(m)
        self.assertEqual(m.get("status"), "PENDING_ORGANIZER_APPROVAL")
        self.assertEqual(m.get("suggested_reschedule", {}).get("suggested_time"), "Tomorrow at 3:00 PM")

    def test_organizer_accepts_proposal_via_chat(self):
        # Setup proposal
        web_ui._handle_employee_time_suggestion(
            meeting_id="sprint",
            employee="meera",
            suggested_time="Tomorrow at 3:00 PM",
            note="Team sync clash",
        )
        proposals = web_ui.storage.get_pending_proposals()
        self.assertEqual(len(proposals), 1)

        # Mock agent talk and calendar
        with patch.object(web_ui.company.company_tools, "send_invite", return_value="COMPLETED"), \
             patch.object(web_ui.company.company_tools, "move_event", return_value=json.dumps({"status": "MOVED"})):
            chat_resp = self.client.post("/api/chat", json={
                "message": "Yes, please schedule it",
                "conversation_id": "test-conv-1",
            })
        self.assertEqual(chat_resp.status_code, 200)
        data = chat_resp.json()
        self.assertIn("successfully scheduled", data["assistant"].lower())

        # Check proposal is resolved
        proposals_after = web_ui.storage.get_pending_proposals()
        self.assertEqual(len(proposals_after), 0)

        # Check meeting was rescheduled
        m = next((item for item in web_ui.company.company_tools.MEETINGS if item["id"] == "sprint"), None)
        self.assertIsNotNone(m)
        self.assertIn("T15:00:00", m["start"])

    def test_organizer_declines_proposal_via_chat(self):
        web_ui._handle_employee_time_suggestion(
            meeting_id="sprint",
            employee="meera",
            suggested_time="Tomorrow at 3:00 PM",
        )
        self.assertEqual(len(web_ui.storage.get_pending_proposals()), 1)

        chat_resp = self.client.post("/api/chat", json={
            "message": "No, that does not work",
            "conversation_id": "test-conv-2",
        })
        self.assertEqual(chat_resp.status_code, 200)
        data = chat_resp.json()
        self.assertIn("declined", data["assistant"].lower())

        # Check proposal is resolved
        self.assertEqual(len(web_ui.storage.get_pending_proposals()), 0)

        # Check meeting suggested_reschedule was cleared
        m = next((item for item in web_ui.company.company_tools.MEETINGS if item["id"] == "sprint"), None)
        self.assertNotIn("suggested_reschedule", m)

    def test_respond_to_proposal_api(self):
        prop = web_ui._handle_employee_time_suggestion(
            meeting_id="sprint",
            employee="meera",
            suggested_time="Tomorrow at 4:00 PM",
        )
        proposal_id = prop["id"]

        with patch.object(web_ui.company.company_tools, "send_invite", return_value="COMPLETED"), \
             patch.object(web_ui.company.company_tools, "move_event", return_value=json.dumps({"status": "MOVED"})):
            resp = self.client.post(f"/api/proposals/{proposal_id}/respond", json={"decision": "yes"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["decision"], "approved")

        self.assertEqual(len(web_ui.storage.get_pending_proposals()), 0)

    def test_resolve_suggested_time_to_iso(self):
        fn = web_ui.company.company_tools.resolve_suggested_time_to_iso
        # ISO string input
        s, e = fn("2026-11-01T10:00:00+05:30")
        self.assertEqual(s, "2026-11-01T10:00:00+05:30")
        self.assertEqual(e, "2026-11-01T11:00:00+05:30")

        # Tomorrow at 3:00 PM
        s2, e2 = fn("Tomorrow at 3:00 PM")
        self.assertIn("T15:00:00+05:30", s2)
        self.assertIn("T16:00:00+05:30", e2)


if __name__ == "__main__":
    unittest.main()
