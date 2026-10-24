import copy
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents"))
import company_agent


TEST_MEETINGS = [
    {
        "id": "offsite",
        "title": "Team Offsite",
        "start": "2026-10-23T15:00:00",
        "end": "2026-10-23T17:00:00",
        "location": "Room 4B",
        "invitees": ["priya", "arjun", "meera"],
        "notes": "Q4 planning",
    },
    {
        "id": "sprint",
        "title": "Sprint Planning",
        "start": "2026-10-19T10:00:00",
        "end": "2026-10-19T11:00:00",
        "location": "Zoom",
        "invitees": ["priya", "arjun"],
        "notes": "Planning",
    },
]


class AddInviteeTests(unittest.TestCase):
    def setUp(self):
        self.original_meetings = company_agent.company_tools.MEETINGS
        self.original_path = company_agent.company_tools.MEETINGS_PATH
        self.original_sessions = dict(company_agent.company_tools.sessions)
        company_agent.company_tools.sessions.clear()
        self.meetings = copy.deepcopy(TEST_MEETINGS)
        self.temp_dir = tempfile.TemporaryDirectory()
        company_agent.company_tools.MEETINGS = self.meetings
        company_agent.company_tools.MEETINGS_PATH = Path(self.temp_dir.name) / "meetings.json"
        company_agent.company_tools.MEETINGS_PATH.write_text(json.dumps(self.meetings), encoding="utf-8")

    def tearDown(self):
        company_agent.company_tools.MEETINGS = self.original_meetings
        company_agent.company_tools.MEETINGS_PATH = self.original_path
        company_agent.company_tools.sessions.clear()
        company_agent.company_tools.sessions.update(self.original_sessions)
        self.temp_dir.cleanup()

    def test_add_invitee_is_confirmed_only_after_agent_completes(self):
        with patch.object(
            company_agent.company_tools, "_talk", return_value="COMPLETED: booked"
        ) as talk:
            result = company_agent.add_invitee("Meera", "sprint")

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved[1]["invitees"], ["priya", "arjun", "meera"])
        self.assertEqual(saved[1]["requested_invitees"], ["priya", "arjun", "meera"])
        self.assertEqual(saved[1]["pending_invitees"], [])
        self.assertIn("Added meera to Sprint Planning invitees", result)
        talk.assert_called_once()

    def test_existing_invitee_is_not_resent(self):
        with patch.object(company_agent.company_tools, "send_invite") as send:
            company_agent.add_invitee("meera", "offsite")

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["invitees"].count("meera"), 1)
        send.assert_not_called()

    def test_pending_invitee_is_not_resent_while_question_is_open(self):
        meeting = self.meetings[1]
        meeting["pending_invitees"] = ["meera"]
        company_agent.company_tools.sessions[("sprint", "meera")] = {
            "state": "INPUT_REQUIRED",
            "outcome": "INPUT_REQUIRED: resolve conflict",
            "kind": "invite",
            "task_id": "task-1",
            "context_id": "context-1",
        }
        with patch.object(company_agent.company_tools, "send_invite") as send:
            result = company_agent.add_invitee("meera", "sprint")

        self.assertIn("awaiting a response", result)
        send.assert_not_called()

    def test_invalid_employee_or_meeting_does_not_send_invite(self):
        with patch.object(company_agent.company_tools, "send_invite") as send:
            self.assertTrue(company_agent.add_invitee("bob", "sprint").startswith("ERROR:"))
            self.assertTrue(company_agent.add_invitee("meera", "missing").startswith("ERROR:"))

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved, self.meetings)
        send.assert_not_called()


class CreateMeetingTests(unittest.TestCase):
    def setUp(self):
        self.original_meetings = company_agent.company_tools.MEETINGS
        self.original_path = company_agent.company_tools.MEETINGS_PATH
        self.original_sessions = dict(company_agent.company_tools.sessions)
        company_agent.company_tools.sessions.clear()
        self.meetings = copy.deepcopy(TEST_MEETINGS)
        self.temp_dir = tempfile.TemporaryDirectory()
        company_agent.company_tools.MEETINGS = self.meetings
        company_agent.company_tools.MEETINGS_PATH = Path(self.temp_dir.name) / "meetings.json"
        company_agent.company_tools.MEETINGS_PATH.write_text(json.dumps(self.meetings), encoding="utf-8")

    def tearDown(self):
        company_agent.company_tools.MEETINGS = self.original_meetings
        company_agent.company_tools.MEETINGS_PATH = self.original_path
        company_agent.company_tools.sessions.clear()
        company_agent.company_tools.sessions.update(self.original_sessions)
        self.temp_dir.cleanup()

    def test_create_persists_and_sends_to_each_unique_invitee(self):
        expected_id = "design-review"
        suffix = 2
        existing_ids = {meeting["id"] for meeting in self.meetings}
        while expected_id in existing_ids:
            expected_id = f"design-review-{suffix}"
            suffix += 1

        with patch.object(
            company_agent.company_tools, "_talk", return_value="COMPLETED: booked"
        ) as talk:
            result = json.loads(company_agent.create_meeting(
                "Design Review", "2026-10-24T10:00:00", "2026-10-24T11:00:00",
                ["Meera", "meera", "ARJUN"], location="Room 2", notes="Review design"))

        created = result["meeting"]
        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(created["id"], expected_id)
        self.assertEqual(created["invitees"], ["meera", "arjun"])
        self.assertEqual(saved[-1], created)
        self.assertEqual(result["invite_results"]["meera"], "COMPLETED: booked")
        self.assertEqual(result["invite_results"]["arjun"], "COMPLETED: booked")
        self.assertEqual(talk.call_count, 2)

    def test_create_uses_unique_ids_for_duplicate_titles(self):
        with patch.object(company_agent.company_tools, "_talk", return_value="COMPLETED: booked"):
            first = json.loads(company_agent.create_meeting(
                "Team Offsite", "2026-10-24T10:00:00", "2026-10-24T11:00:00", []))
            second = json.loads(company_agent.create_meeting(
                "Team Offsite", "2026-10-25T10:00:00", "2026-10-25T11:00:00", []))

        self.assertEqual(first["meeting"]["id"], "team-offsite")
        self.assertEqual(second["meeting"]["id"], "team-offsite-2")

    def test_exact_duplicate_reuses_existing_id_and_does_not_add_a_meeting(self):
        original = copy.deepcopy(self.meetings[0])
        with patch.object(
            company_agent.company_tools,
            "_talk",
            return_value="COMPLETED: already booked",
        ) as send:
            result = json.loads(company_agent.create_meeting(
                original["title"],
                original["start"],
                original["end"],
                list(reversed(original["invitees"])),
                location=original["location"],
                notes=original["notes"],
            ))

        saved = json.loads(
            company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8")
        )
        self.assertTrue(result["reused_existing"])
        self.assertEqual(result["meeting"]["id"], original["id"])
        self.assertEqual(saved, self.meetings)
        send.assert_not_called()

    def test_duplicate_matching_ignores_text_case_and_preserves_existing_record(self):
        original = copy.deepcopy(self.meetings[1])
        with patch.object(
            company_agent.company_tools,
            "_talk",
            return_value="COMPLETED: already booked",
        ) as send:
            result = json.loads(company_agent.create_meeting(
                original["title"].lower(),
                original["start"],
                original["end"],
                ["PRIYA", "ARJUN"],
                location=original["location"].upper(),
                notes=original["notes"].lower(),
            ))

        saved = json.loads(
            company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8")
        )
        self.assertTrue(result["reused_existing"])
        self.assertEqual(result["meeting"], original)
        self.assertEqual(saved, self.meetings)
        send.assert_not_called()

    def test_meeting_with_different_notes_is_not_a_duplicate(self):
        original = copy.deepcopy(self.meetings[0])
        with patch.object(
            company_agent.company_tools,
            "_talk",
            return_value="COMPLETED: booked",
        ):
            result = json.loads(company_agent.create_meeting(
                original["title"],
                original["start"],
                original["end"],
                original["invitees"],
                location=original["location"],
                notes="Different agenda",
            ))

        self.assertNotEqual(result["meeting"]["id"], original["id"])
        self.assertNotIn("reused_existing", result)

    def test_invalid_meeting_is_not_persisted_or_sent(self):
        original = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        with patch.object(company_agent.company_tools, "send_invite") as send:
            bad_time = company_agent.create_meeting(
                "Invalid", "not-a-time", "2026-10-24T11:00:00", ["meera"])
            invalid_employee = company_agent.create_meeting(
                "Invalid", "2026-10-24T10:00:00", "2026-10-24T11:00:00", ["bob"])
            reversed_time = company_agent.create_meeting(
                "Invalid", "2026-10-24T11:00:00", "2026-10-24T10:00:00", ["meera"])

        self.assertTrue(bad_time.startswith("ERROR:"))
        self.assertTrue(invalid_employee.startswith("ERROR:"))
        self.assertTrue(reversed_time.startswith("ERROR:"))
        self.assertEqual(json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8")), original)
        send.assert_not_called()

    def test_equivalent_datetime_formats_reuse_same_meeting(self):
        with patch.object(company_agent.company_tools, "send_invite") as send_invite:
            first = json.loads(company_agent.create_meeting(
                "Normalized review",
                "2040-08-12T10:00",
                "2040-08-12T11:00",
                [],
            ))
            repeated = json.loads(company_agent.create_meeting(
                "Normalized review",
                "2040-08-12T10:00:00+05:30",
                "2040-08-12T11:00:00+05:30",
                [],
            ))

        self.assertEqual(first["meeting"]["id"], repeated["meeting"]["id"])
        self.assertEqual(first["meeting"]["start"], "2040-08-12T10:00:00+05:30")
        self.assertEqual(first["meeting"]["end"], "2040-08-12T11:00:00+05:30")
        self.assertTrue(repeated["reused_existing"])
        send_invite.assert_not_called()

    def test_concurrent_meeting_creations_preserve_every_update(self):
        def create(index):
            return company_agent.create_meeting(
                f"Concurrent meeting {index}",
                "2040-08-12T10:00:00",
                "2040-08-12T11:00:00",
                [],
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(create, range(32)))

        created = [
            meeting for meeting in self.meetings
            if meeting["title"].startswith("Concurrent meeting ")
        ]
        saved = json.loads(
            company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertTrue(all(json.loads(result)["meeting"] for result in results))
        self.assertEqual(len(created), 32)
        self.assertEqual(
            len([meeting for meeting in saved
                 if meeting["title"].startswith("Concurrent meeting ")]),
            32,
        )
        self.assertEqual(
            list(Path(self.temp_dir.name).glob(".meetings.json.*.tmp")),
            [],
        )

    def test_move_conflict_without_callback_returns_question_instead_of_reading_stdin(self):
        with patch.object(
            company_agent.company_tools,
            "_talk",
            return_value="INPUT_REQUIRED: conflict with another event",
        ), patch("builtins.input", side_effect=AssertionError("stdin must not be read")):
            result = json.loads(company_agent.move_event(
                "sprint",
                "2040-08-13T10:00:00",
                "2040-08-13T11:00:00",
            ))

        self.assertEqual(result["status"], "CONFLICT")
        self.assertIn("Proceed despite these conflicts", result["question"])

    def test_move_confirmation_reprompts_at_most_three_times(self):
        answers = []

        def invalid_answer(_question):
            answers.append(True)
            return "maybe"

        with (
            patch.object(
                company_agent.company_tools,
                "_talk",
                return_value="INPUT_REQUIRED: conflict with another event",
            ),
            patch.object(
                company_agent.company_tools,
                "reply_to_employee",
                return_value="COMPLETED: cancelled",
            ),
        ):
            result = json.loads(company_agent.move_event(
                "sprint",
                "2040-08-13T10:00:00",
                "2040-08-13T11:00:00",
                ask_user_fn=invalid_answer,
            ))

        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(len(answers), 3)

    def test_meeting_time_migration_keeps_ids_and_normalizes_values(self):
        before_ids = [meeting["id"] for meeting in self.meetings]
        company_agent.company_tools.migrate_meeting_times()
        saved = json.loads(
            company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))

        self.assertEqual([meeting["id"] for meeting in saved], before_ids)
        self.assertEqual(saved[0]["start"], "2026-10-23T15:00:00+05:30")
        self.assertEqual(saved[0]["end"], "2026-10-23T17:00:00+05:30")


class InviteSessionTests(unittest.TestCase):
    def setUp(self):
        tools = company_agent.company_tools
        self.original_meetings = tools.MEETINGS
        self.original_path = tools.MEETINGS_PATH
        self.original_sessions = dict(tools.sessions)
        tools.sessions.clear()
        self.meetings = [{
            "id": "review-one",
            "title": "Review One",
            "start": "2026-10-23T10:00:00",
            "end": "2026-10-23T11:00:00",
            "location": "Zoom",
            "invitees": [],
            "requested_invitees": ["meera"],
            "pending_invitees": ["meera"],
            "declined_invitees": [],
        }, {
            "id": "review-two",
            "title": "Review Two",
            "start": "2026-10-24T10:00:00",
            "end": "2026-10-24T11:00:00",
            "location": "Zoom",
            "invitees": [],
            "requested_invitees": ["meera"],
            "pending_invitees": ["meera"],
            "declined_invitees": [],
        }]
        self.temp_dir = tempfile.TemporaryDirectory()
        tools.MEETINGS = self.meetings
        tools.MEETINGS_PATH = Path(self.temp_dir.name) / "meetings.json"
        tools.MEETINGS_PATH.write_text(json.dumps(self.meetings), encoding="utf-8")

    def tearDown(self):
        tools = company_agent.company_tools
        tools.MEETINGS = self.original_meetings
        tools.MEETINGS_PATH = self.original_path
        tools.sessions.clear()
        tools.sessions.update(self.original_sessions)
        self.temp_dir.cleanup()

    def test_send_invite_tracks_pending_then_completed_and_declined_outcomes(self):
        with patch.object(
            company_agent.company_tools,
            "_talk",
            return_value="INPUT_REQUIRED: replace a conflicting event?",
        ):
            company_agent.company_tools.send_invite("meera", "review-one")

        meeting = company_agent.company_tools._meeting_for_id("review-one")
        self.assertEqual(meeting["invitees"], [])
        self.assertEqual(meeting["pending_invitees"], ["meera"])

        company_agent.company_tools._update_meeting_invitees(
            "review-one", "meera", "COMPLETED: event booked"
        )
        meeting = company_agent.company_tools._meeting_for_id("review-one")
        self.assertEqual(meeting["invitees"], ["meera"])
        self.assertEqual(meeting["pending_invitees"], [])

        company_agent.company_tools._update_meeting_invitees(
            "review-one", "meera", "REJECTED: invitation declined"
        )
        meeting = company_agent.company_tools._meeting_for_id("review-one")
        self.assertEqual(meeting["invitees"], [])
        self.assertEqual(meeting["declined_invitees"], ["meera"])

    def test_pending_sessions_are_isolated_by_meeting_and_reply_resumes_target(self):
        calls = []

        async def exchange(employee, text, task_id=None, context_id=None):
            calls.append((employee, text, task_id, context_id))
            if task_id is None:
                payload = json.loads(text.split("\n", 1)[1])
                meeting_id = payload["meeting_id"]
                return (
                    "TASK_STATE_INPUT_REQUIRED",
                    f"Question for {meeting_id}",
                    f"task-{meeting_id}",
                    f"context-{meeting_id}",
                )
            return (
                "TASK_STATE_COMPLETED",
                "Invitation accepted",
                task_id,
                context_id,
            )

        with patch.object(company_agent.company_tools, "_exchange", side_effect=exchange):
            for meeting_id in ("review-one", "review-two"):
                company_agent.company_tools.send_invite("meera", meeting_id)
            result = company_agent.company_tools.reply_to_employee(
                "meera", "Accept review one", meeting_id="review-one"
            )

        self.assertTrue(result.startswith("COMPLETED"))
        self.assertEqual(calls[-1][2:], ("task-review-one", "context-review-one"))
        self.assertEqual(
            company_agent.company_tools.sessions[("review-two", "meera")]["state"],
            "INPUT_REQUIRED",
        )
        self.assertEqual(
            company_agent.company_tools._meeting_for_id("review-one")["invitees"],
            ["meera"],
        )
        self.assertEqual(
            company_agent.company_tools._meeting_for_id("review-two")["invitees"],
            [],
        )

    def test_reply_without_meeting_id_rejects_ambiguous_employee_sessions(self):
        for meeting_id in ("review-one", "review-two"):
            company_agent.company_tools.sessions[(meeting_id, "meera")] = {
                "state": "INPUT_REQUIRED",
                "task_id": f"task-{meeting_id}",
                "context_id": f"context-{meeting_id}",
                "kind": "invite",
                "outcome": "INPUT_REQUIRED: answer",
            }

        result = company_agent.company_tools.reply_to_employee("meera", "Yes")

        self.assertIn("multiple open questions", result)
        self.assertIn("review-one", result)
        self.assertIn("review-two", result)


class MoveEventTests(unittest.TestCase):
    def setUp(self):
        self.original_meetings = company_agent.company_tools.MEETINGS
        self.original_path = company_agent.company_tools.MEETINGS_PATH
        self.meetings = copy.deepcopy(TEST_MEETINGS)
        self.meeting = next(meeting for meeting in self.meetings if meeting["id"] == "sprint")
        self.meeting["invitees"] = ["meera"]
        self.temp_dir = tempfile.TemporaryDirectory()
        company_agent.company_tools.MEETINGS = self.meetings
        company_agent.company_tools.MEETINGS_PATH = Path(self.temp_dir.name) / "meetings.json"
        company_agent.company_tools.MEETINGS_PATH.write_text(json.dumps(self.meetings), encoding="utf-8")

    def tearDown(self):
        company_agent.company_tools.MEETINGS = self.original_meetings
        company_agent.company_tools.MEETINGS_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_move_checks_then_commits_over_a2a_and_persists_meeting(self):
        def talk(employee, text, task_id=None, context_id=None):
            request = json.loads(text)
            self.assertEqual(employee, "meera")
            self.assertEqual(request["meeting_id"], "sprint")
            return "COMPLETED: READY" if request["operation"] == "check_move" else "COMPLETED: MOVED"

        with patch.object(company_agent.company_tools, "_talk", side_effect=talk):
            result = json.loads(company_agent.move_event(
                "sprint", "2026-10-20T10:00:00", "2026-10-20T11:00:00"))

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        moved = next(meeting for meeting in saved if meeting["id"] == "sprint")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(moved["start"], "2026-10-20T10:00:00+05:30")
        self.assertEqual(moved["end"], "2026-10-20T11:00:00+05:30")

    def test_conflict_prompts_user_and_cancelling_keeps_original_meeting(self):
        with patch.object(company_agent.company_tools, "_talk", return_value="INPUT_REQUIRED: Client Call overlaps"), \
                patch.object(company_agent.company_tools, "reply_to_employee", return_value="REJECTED: cancelled") as reply:
            result = json.loads(company_agent.move_event(
                "sprint", "2026-10-20T10:00:00", "2026-10-20T11:00:00",
                ask_user_fn=lambda _question: "no"))

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        unchanged = next(meeting for meeting in saved if meeting["id"] == "sprint")
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(unchanged, self.meeting)
        reply.assert_called_once_with(
            "meera",
            "No. Cancel the move and keep the original calendar event.",
            meeting_id="sprint",
        )

    def test_conflict_approval_replies_to_agent_before_commit(self):
        with patch.object(company_agent.company_tools, "_talk",
                          side_effect=["INPUT_REQUIRED: Client Call overlaps",
                                       "COMPLETED: MOVED"]) as talk, \
                patch.object(company_agent.company_tools, "reply_to_employee",
                             return_value="COMPLETED: READY") as reply:
            result = json.loads(company_agent.move_event(
                "sprint", "2026-10-20T10:00:00", "2026-10-20T11:00:00",
                ask_user_fn=lambda _question: "yes"))

        self.assertEqual(result["status"], "COMPLETED")
        self.assertTrue(json.loads(talk.call_args_list[-1].args[1])["allow_conflicts"])
        reply.assert_called_once_with(
            "meera",
            "Yes. Proceed with moving the event despite the reported conflict.",
            meeting_id="sprint",
        )

    def test_reply_to_employee_recovers_gracefully_on_task_not_found(self):
        company_agent.company_tools.sessions[("sprint", "meera")] = {
            "task_id": "old-task",
            "context_id": "old-ctx",
            "state": "INPUT_REQUIRED",
            "kind": "invite",
            "outcome": "INPUT_REQUIRED: clash",
        }
        with patch.object(company_agent.company_tools, "_talk", return_value="UNREACHABLE: TASK_NOT_FOUND"):
            result = company_agent.company_tools.reply_to_employee("meera", "Decline: cannot make it", meeting_id="sprint")
            self.assertTrue(result.startswith("REJECTED"))
            self.assertEqual(company_agent.company_tools.sessions[("sprint", "meera")]["state"], "REJECTED")


class RemovePeopleTests(unittest.TestCase):
    def setUp(self):
        self.original_meetings = company_agent.company_tools.MEETINGS
        self.original_path = company_agent.company_tools.MEETINGS_PATH
        self.meetings = copy.deepcopy(TEST_MEETINGS)
        self.meeting = next(meeting for meeting in self.meetings if meeting["id"] == "sprint")
        self.meeting["invitees"] = ["meera", "arjun"]
        self.temp_dir = tempfile.TemporaryDirectory()
        company_agent.company_tools.MEETINGS = self.meetings
        company_agent.company_tools.MEETINGS_PATH = Path(self.temp_dir.name) / "meetings.json"
        company_agent.company_tools.MEETINGS_PATH.write_text(json.dumps(self.meetings), encoding="utf-8")

    def tearDown(self):
        company_agent.company_tools.MEETINGS = self.original_meetings
        company_agent.company_tools.MEETINGS_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_remove_people_uses_employee_agents_then_persists_invitee_removal(self):
        def talk(employee, payload, task_id=None, context_id=None):
            request = json.loads(payload)
            self.assertEqual(request["operation"], "remove_meeting")
            self.assertEqual(request["meeting_id"], "sprint")
            return f"COMPLETED: {employee} calendar event removed"

        with patch.object(company_agent.company_tools, "_talk", side_effect=talk) as send:
            result = json.loads(company_agent.remove_people(
                "sprint", ["Meera", "meera"]))

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        remaining = next(meeting for meeting in saved if meeting["id"] == "sprint")["invitees"]
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["removed_employees"], ["meera"])
        self.assertEqual(remaining, ["arjun"])
        saved_meeting = next(item for item in saved if item["id"] == "sprint")
        self.assertEqual(saved_meeting["requested_invitees"], ["arjun"])
        send.assert_called_once()

    def test_partial_remove_only_drops_employees_confirmed_by_agents(self):
        with patch.object(company_agent.company_tools, "_talk",
                          side_effect=["COMPLETED: removed", "UNREACHABLE: agent down"]):
            result = json.loads(company_agent.remove_people("sprint", ["meera", "arjun"]))

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        remaining = next(meeting for meeting in saved if meeting["id"] == "sprint")["invitees"]
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["removed_employees"], ["meera"])
        self.assertEqual(remaining, ["arjun"])
        saved_meeting = next(item for item in saved if item["id"] == "sprint")
        self.assertEqual(saved_meeting["requested_invitees"], ["arjun"])

    def test_invalid_remove_request_does_not_contact_agents_or_change_meeting(self):
        with patch.object(company_agent.company_tools, "_talk") as send:
            unknown = company_agent.remove_people("sprint", ["bob"])
            not_invited = company_agent.remove_people("sprint", ["priya"])

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertTrue(unknown.startswith("ERROR:"))
        self.assertTrue(not_invited.startswith("ERROR:"))
        self.assertEqual(saved, self.meetings)
        send.assert_not_called()


class CancelMeetingTests(unittest.TestCase):
    def setUp(self):
        self.original_meetings = company_agent.company_tools.MEETINGS
        self.original_path = company_agent.company_tools.MEETINGS_PATH
        self.meetings = copy.deepcopy(TEST_MEETINGS)
        self.meeting = next(meeting for meeting in self.meetings if meeting["id"] == "sprint")
        self.meeting["invitees"] = ["meera", "arjun"]
        self.temp_dir = tempfile.TemporaryDirectory()
        company_agent.company_tools.MEETINGS = self.meetings
        company_agent.company_tools.MEETINGS_PATH = Path(self.temp_dir.name) / "meetings.json"
        company_agent.company_tools.MEETINGS_PATH.write_text(json.dumps(self.meetings), encoding="utf-8")

    def tearDown(self):
        company_agent.company_tools.MEETINGS = self.original_meetings
        company_agent.company_tools.MEETINGS_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_cancel_informs_each_employee_then_deletes_company_record(self):
        def talk(employee, payload, task_id=None, context_id=None):
            request = json.loads(payload)
            self.assertEqual(request["operation"], "remove_meeting")
            self.assertEqual(request["meeting_id"], "sprint")
            return f"COMPLETED: {employee} calendar event removed"

        with patch.object(company_agent.company_tools, "_talk", side_effect=talk) as send:
            result = json.loads(company_agent.remove_meeting("sprint"))

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["removed_from_calendars"], ["meera", "arjun"])
        self.assertNotIn("sprint", [meeting["id"] for meeting in saved])
        self.assertEqual(send.call_count, 2)

    def test_partial_cancel_keeps_record_for_employees_not_confirmed(self):
        with patch.object(company_agent.company_tools, "_talk",
                          side_effect=["COMPLETED: removed", "UNREACHABLE: agent down"]):
            result = json.loads(company_agent.remove_meeting("sprint"))

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        remaining = next(meeting for meeting in saved if meeting["id"] == "sprint")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["removed_from_calendars"], ["meera"])
        self.assertEqual(remaining["invitees"], ["arjun"])
        self.assertEqual(remaining["requested_invitees"], ["arjun"])

    def test_cancel_without_invitees_deletes_meeting_without_agent_requests(self):
        self.meeting["invitees"] = []
        company_agent.company_tools.MEETINGS_PATH.write_text(json.dumps(self.meetings), encoding="utf-8")
        with patch.object(company_agent.company_tools, "_talk") as send:
            result = json.loads(company_agent.remove_meeting("sprint"))

        saved = json.loads(company_agent.company_tools.MEETINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "CANCELLED")
        self.assertNotIn("sprint", [meeting["id"] for meeting in saved])
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
