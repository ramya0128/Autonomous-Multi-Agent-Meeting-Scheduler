import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agents import calendar_backend


class FakeCalendarPersistenceTests(unittest.TestCase):
    def test_overlap_uses_actual_half_open_time_ranges(self):
        requested = ("2040-08-12T10:00:00", "2040-08-12T11:00:00")

        self.assertTrue(calendar_backend._overlaps(
            *requested, "2040-08-12T10:30:00", "2040-08-12T11:30:00"))
        self.assertTrue(calendar_backend._overlaps(
            *requested, "2040-08-12T09:30:00", "2040-08-12T10:30:00"))
        self.assertFalse(calendar_backend._overlaps(
            *requested, "2040-08-12T11:00:00", "2040-08-12T12:00:00"))
        self.assertFalse(calendar_backend._overlaps(
            *requested, "2040-08-12T09:00:00", "2040-08-12T10:00:00"))

    def test_overlap_compares_instants_across_time_zones(self):
        self.assertTrue(calendar_backend._overlaps(
            "2040-08-12T10:00:00+05:30",
            "2040-08-12T11:00:00+05:30",
            "2040-08-12T04:45:00Z",
            "2040-08-12T05:15:00Z",
        ))

    def test_overlap_rejects_invalid_intervals(self):
        with self.assertRaisesRegex(ValueError, "end later than their start"):
            calendar_backend._overlaps(
                "2040-08-12T11:00:00",
                "2040-08-12T10:00:00",
                "2040-08-12T10:00:00",
                "2040-08-12T12:00:00",
            )

    def test_employee_google_calendar_ids_are_configured(self):
        self.assertEqual(calendar_backend.GOOGLE_CALENDAR_IDS, {
            "priya": (
                "59a8d5d4bb1a7a8c1b7b82b17de4b0eec155be2e11c8a06330a802aebb52297d"
                "@group.calendar.google.com"
            ),
            "arjun": (
                "fa3946c38c3f91849bf35a2d8f2e70cc3096958c47350b22fb10474e2ddca149"
                "@group.calendar.google.com"
            ),
            "meera": (
                "c2d9b3afc12f2bc162d6ec8584d09ea317b37aa4015908799c501a9cab257914"
                "@group.calendar.google.com"
            ),
        })

    def test_created_event_is_saved_and_reloaded_for_conflict_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_dir = Path(temp_dir) / "calendars"
                calendar_dir.mkdir()
                path = calendar_dir / "meera.json"
                path.write_text("[]", encoding="utf-8")

                calendar = calendar_backend.FakeCalendar("meera")
                event = calendar.create_event(
                    "Sprint Review", "2040-08-12T10:00:00", "2040-08-12T11:00:00", "Room 2")

                saved = json.loads(path.read_text(encoding="utf-8"))
                reloaded = calendar_backend.FakeCalendar("meera")

        self.assertEqual(saved, [event])
        self.assertEqual(reloaded.find_conflicts(
            "2040-08-12T10:30:00", "2040-08-12T11:30:00"), [event])

    def test_create_event_is_idempotent_for_existing_meeting_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_dir = Path(temp_dir) / "calendars"
                calendar_dir.mkdir()
                path = calendar_dir / "meera.json"
                path.write_text("[]", encoding="utf-8")
                calendar = calendar_backend.FakeCalendar("meera")

                first = calendar.create_event(
                    "Sprint Review",
                    "2040-08-12T10:00:00",
                    "2040-08-12T11:00:00",
                    "Room 2",
                    "sprint-review",
                )
                repeated = calendar.create_event(
                    "Sprint Review",
                    "2040-08-12T10:00:00",
                    "2040-08-12T11:00:00",
                    "Room 2",
                    "sprint-review",
                )
                saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(first, repeated)
        self.assertEqual(saved, [first])

    def test_new_fake_event_id_does_not_collide_after_deletion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_dir = Path(temp_dir) / "calendars"
                calendar_dir.mkdir()
                path = calendar_dir / "meera.json"
                path.write_text(json.dumps([
                    {"id": "evt-1", "title": "old", "start": "2040-08-12T08:00:00",
                     "end": "2040-08-12T09:00:00"},
                    {"id": "evt-2", "title": "existing", "start": "2040-08-12T09:00:00",
                     "end": "2040-08-12T10:00:00"},
                ]), encoding="utf-8")
                calendar = calendar_backend.FakeCalendar("meera")
                calendar.remove_event(
                    "old", "old", "2040-08-12T08:00:00", "2040-08-12T09:00:00")

                created = calendar.create_event(
                    "new", "2040-08-12T10:00:00", "2040-08-12T11:00:00")
                saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(created["id"], "evt-3")
        self.assertEqual(len({event.get("id") for event in saved}), 2)

    def test_fake_calendar_handles_events_without_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_dir = Path(temp_dir) / "calendars"
                calendar_dir.mkdir()
                path = calendar_dir / "meera.json"
                path.write_text(json.dumps([{
                    "title": "legacy",
                    "start": "2040-08-12T08:00:00",
                    "end": "2040-08-12T09:00:00",
                }]), encoding="utf-8")
                calendar = calendar_backend.FakeCalendar("meera")

                created = calendar.create_event(
                    "new", "2040-08-12T10:00:00", "2040-08-12T11:00:00")

        self.assertEqual(created["id"], "evt-2")

    def test_move_replaces_old_event_and_preserves_meeting_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_dir = Path(temp_dir) / "calendars"
                calendar_dir.mkdir()
                path = calendar_dir / "meera.json"
                path.write_text(json.dumps([{
                    "id": "evt-4",
                    "meeting_id": "sprint",
                    "title": "Sprint Planning",
                    "start": "2040-08-12T10:00:00",
                    "end": "2040-08-12T11:00:00",
                    "location": "Zoom",
                }]), encoding="utf-8")
                calendar = calendar_backend.FakeCalendar("meera")

                moved = calendar.move_event(
                    "sprint", "Sprint Planning", "2040-08-12T10:00:00",
                    "2040-08-12T11:00:00", "Sprint Planning",
                    "2040-08-13T10:00:00", "2040-08-13T11:00:00", "Zoom")

                saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(moved["id"], "evt-4")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["start"], "2040-08-13T10:00:00")
        self.assertEqual(saved[0]["meeting_id"], "sprint")

    def test_move_rejects_conflicts_without_changing_old_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_dir = Path(temp_dir) / "calendars"
                calendar_dir.mkdir()
                path = calendar_dir / "meera.json"
                old_event = {
                    "id": "evt-4", "meeting_id": "sprint", "title": "Sprint Planning",
                    "start": "2040-08-12T10:00:00", "end": "2040-08-12T11:00:00",
                    "location": "Zoom",
                }
                conflict = {
                    "id": "evt-5", "title": "Client Call", "start": "2040-08-13T10:30:00",
                    "end": "2040-08-13T11:30:00", "location": "",
                }
                path.write_text(json.dumps([old_event, conflict]), encoding="utf-8")
                calendar = calendar_backend.FakeCalendar("meera")

                with self.assertRaisesRegex(ValueError, "conflicts"):
                    calendar.move_event(
                        "sprint", "Sprint Planning", "2040-08-12T10:00:00",
                        "2040-08-12T11:00:00", "Sprint Planning",
                        "2040-08-13T10:00:00", "2040-08-13T11:00:00", "Zoom")

                saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(saved, [old_event, conflict])

    def test_move_excludes_legacy_event_without_calendar_id_from_conflicts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_dir = Path(temp_dir) / "calendars"
                calendar_dir.mkdir()
                path = calendar_dir / "meera.json"
                path.write_text(json.dumps([{
                    "meeting_id": "sprint",
                    "title": "Sprint Planning",
                    "start": "2040-08-12T10:00:00",
                    "end": "2040-08-12T11:00:00",
                    "location": "Zoom",
                }]), encoding="utf-8")
                calendar = calendar_backend.FakeCalendar("meera")

                moved = calendar.move_event(
                    "sprint", "Sprint Planning", "2040-08-12T10:00:00",
                    "2040-08-12T11:00:00", "Sprint Planning",
                    "2040-08-12T10:30:00", "2040-08-12T11:30:00", "Zoom")

        self.assertEqual(moved["id"], "evt-2")
        self.assertEqual(moved["start"], "2040-08-12T10:30:00")

    def test_remove_event_persists_removal_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(calendar_backend, "DATA", Path(temp_dir)):
                calendar_dir = Path(temp_dir) / "calendars"
                calendar_dir.mkdir()
                path = calendar_dir / "meera.json"
                meeting_event = {
                    "id": "evt-1", "meeting_id": "sprint", "title": "Sprint Planning",
                    "start": "2040-08-12T10:00:00", "end": "2040-08-12T11:00:00",
                    "location": "Zoom",
                }
                other_event = {
                    "id": "evt-2", "title": "Client Call", "start": "2040-08-13T10:00:00",
                    "end": "2040-08-13T11:00:00", "location": "",
                }
                path.write_text(json.dumps([meeting_event, other_event]), encoding="utf-8")
                calendar = calendar_backend.FakeCalendar("meera")

                result = calendar.remove_event(
                    "sprint", "Sprint Planning", "2040-08-12T10:00:00",
                    "2040-08-12T11:00:00")
                already_absent = calendar.remove_event(
                    "sprint", "Sprint Planning", "2040-08-12T10:00:00",
                    "2040-08-12T11:00:00")
                saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(result["removed"])
        self.assertFalse(already_absent["removed"])
        self.assertEqual(saved, [other_event])

    def test_google_create_event_reuses_existing_event_for_meeting_id(self):
        calendar = calendar_backend.GoogleCalendar.__new__(
            calendar_backend.GoogleCalendar
        )
        service = Mock()
        events = Mock()
        service.events.return_value = events
        calendar.svc = service
        calendar.calendar_id = calendar_backend.GOOGLE_CALENDAR_IDS["meera"]
        existing_event = {
            "id": "google-event-1",
            "summary": "Sprint Review",
            "location": "Room 2",
            "start": {"dateTime": "2040-08-12T10:00:00+05:30"},
            "end": {"dateTime": "2040-08-12T11:00:00+05:30"},
            "extendedProperties": {
                "private": {"meeting_id": "sprint-review"},
            },
            "htmlLink": "https://calendar.google.com/event",
        }
        events.list.return_value.execute.return_value = {
            "items": [existing_event],
        }

        result = calendar.create_event(
            "Sprint Review",
            "2040-08-12T10:00:00",
            "2040-08-12T11:00:00",
            "Room 2",
            "sprint-review",
        )

        self.assertEqual(result["id"], "google-event-1")
        self.assertEqual(result["meeting_id"], "sprint-review")
        events.list.assert_called_once_with(
            calendarId=calendar_backend.GOOGLE_CALENDAR_IDS["meera"],
            privateExtendedProperty="meeting_id=sprint-review",
            maxResults=2,
        )
        events.insert.assert_not_called()

    def test_google_conflicts_filter_returned_events_by_actual_interval(self):
        calendar = calendar_backend.GoogleCalendar.__new__(
            calendar_backend.GoogleCalendar
        )
        service = Mock()
        events = Mock()
        service.events.return_value = events
        calendar.svc = service
        calendar.calendar_id = calendar_backend.GOOGLE_CALENDAR_IDS["meera"]
        events.list.return_value.execute.return_value = {
            "items": [{
                "id": "overlap",
                "summary": "Overlapping appointment",
                "start": {"dateTime": "2040-08-12T10:30:00+05:30"},
                "end": {"dateTime": "2040-08-12T11:30:00+05:30"},
            }, {
                "id": "after",
                "summary": "Starts at requested end",
                "start": {"dateTime": "2040-08-12T11:00:00+05:30"},
                "end": {"dateTime": "2040-08-12T12:00:00+05:30"},
            }, {
                "id": "before",
                "summary": "Ends at requested start",
                "start": {"dateTime": "2040-08-12T09:00:00+05:30"},
                "end": {"dateTime": "2040-08-12T10:00:00+05:30"},
            }],
        }

        conflicts = calendar.find_conflicts(
            "2040-08-12T10:00:00+05:30",
            "2040-08-12T11:00:00+05:30",
        )

        self.assertEqual([event["id"] for event in conflicts], ["overlap"])

    def test_google_create_event_propagates_api_errors_instead_of_using_fake_calendar(self):
        calendar = calendar_backend.GoogleCalendar.__new__(
            calendar_backend.GoogleCalendar
        )
        events = Mock()
        service = Mock()
        service.events.return_value = events
        calendar.svc = service
        calendar.calendar_id = calendar_backend.GOOGLE_CALENDAR_IDS["meera"]
        events.list.return_value.execute.side_effect = OSError(
            "Google Calendar is unreachable"
        )

        with self.assertRaisesRegex(OSError, "Google Calendar is unreachable"):
            calendar.create_event(
                "Sprint Review",
                "2040-08-12T10:00:00",
                "2040-08-12T11:00:00",
                "Room 2",
                "sprint-review",
            )

        events.insert.assert_not_called()

    def test_google_list_events_reads_all_pages_from_employee_calendar(self):
        calendar = calendar_backend.GoogleCalendar.__new__(
            calendar_backend.GoogleCalendar
        )
        calendar.calendar_id = calendar_backend.GOOGLE_CALENDAR_IDS["priya"]
        service = Mock()
        events = Mock()
        service.events.return_value = events
        calendar.svc = service
        events.list.return_value.execute.side_effect = [
            {
                "items": [{
                    "id": "google-event-1",
                    "summary": "Planning",
                    "start": {"dateTime": "2040-08-12T10:00:00+05:30"},
                    "end": {"dateTime": "2040-08-12T11:00:00+05:30"},
                }],
                "nextPageToken": "next",
            },
            {
                "items": [{
                    "id": "google-event-2",
                    "summary": "Review",
                    "start": {"dateTime": "2040-08-13T10:00:00+05:30"},
                    "end": {"dateTime": "2040-08-13T11:00:00+05:30"},
                }],
            },
        ]

        result = calendar.list_events()

        self.assertEqual([event["id"] for event in result], [
            "google-event-1",
            "google-event-2",
        ])
        self.assertEqual(events.list.call_count, 2)
        self.assertEqual(
            events.list.call_args_list[0].kwargs["calendarId"],
            calendar_backend.GOOGLE_CALENDAR_IDS["priya"],
        )
        self.assertEqual(
            events.list.call_args_list[1].kwargs["pageToken"],
            "next",
        )


if __name__ == "__main__":
    unittest.main()
