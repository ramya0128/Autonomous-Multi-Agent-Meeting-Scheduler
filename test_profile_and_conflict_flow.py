"""Tests for employee profiles, authentication, data isolation, conflict decision flow, and live audit feed."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
from starlette.testclient import TestClient

import web_ui
import agents.storage as storage
import agents.company_tools as company_tools


class ProfileAndConflictFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        
        # Patch DATA_DIR and USERS_PATH in storage
        self.storage_patch = patch.object(storage, "DATA_DIR", self.data_dir)
        self.storage_users_patch = patch.object(storage, "USERS_PATH", self.data_dir / "users.json")
        self.storage_patch.start()
        self.storage_users_patch.start()
        storage.init_storage()
        
        # Clear in-memory storage caches
        with storage._STORE_LOCK:
            storage._SESSIONS.clear()
        
        self.client = TestClient(web_ui.app)

    def tearDown(self):
        self.client.close()
        self.storage_users_patch.stop()
        self.storage_patch.stop()
        self.temp_dir.cleanup()

    def test_auth_login_success_and_me(self):
        # 1. Login as Priya
        resp = self.client.post("/api/auth/login", json={"username": "priya", "password": "priya123"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("token", data)
        token = data["token"]
        self.assertEqual(data["user"]["username"], "priya")
        self.assertEqual(data["user"]["role"], "employee")

        # 2. Check /api/auth/me with Bearer token
        me_resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_resp.status_code, 200)
        self.assertTrue(me_resp.json()["authenticated"])
        self.assertEqual(me_resp.json()["user"]["name"], "Priya Sharma")

    def test_auth_login_invalid_credentials(self):
        resp = self.client.post("/api/auth/login", json={"username": "priya", "password": "wrongpassword"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid username or password", resp.json()["error"])

    def test_employee_data_isolation(self):
        # Login as Priya
        priya_login = self.client.post("/api/auth/login", json={"username": "priya", "password": "priya123"}).json()
        priya_token = priya_login["token"]

        # Priya can access her own state
        resp_own = self.client.get("/api/employees/priya/state", headers={"Authorization": f"Bearer {priya_token}"})
        self.assertEqual(resp_own.status_code, 200)
        self.assertEqual(resp_own.json()["employee"]["id"], "priya")

        # Priya cannot access Arjun's state (403 Forbidden)
        resp_forbidden = self.client.get("/api/employees/arjun/state", headers={"Authorization": f"Bearer {priya_token}"})
        self.assertEqual(resp_forbidden.status_code, 403)
        self.assertIn("Forbidden", resp_forbidden.json()["error"])

    def test_live_feed_recording_and_retrieval(self):
        storage.log_event("CONFLICT_DETECTED", "priya", "Priya's agent found a conflict and is waiting on Priya.", meeting_id="sync-1")
        storage.log_event("DECISION_APPROVED", "priya", "Priya approved rescheduling. Rescheduling the meeting.", meeting_id="sync-1")

        resp = self.client.get("/api/feed")
        self.assertEqual(resp.status_code, 200)
        feed = resp.json()["feed"]
        self.assertGreaterEqual(len(feed), 2)
        events = [f.get("message") or f.get("event") for f in feed]
        self.assertIn("Priya's agent found a conflict and is waiting on Priya.", events)
        self.assertIn("Priya approved rescheduling. Rescheduling the meeting.", events)

    def test_decision_creation_and_response_flow(self):
        # Create a pending decision for Priya
        decision = storage.create_pending_decision(
            meeting_id="offsite-sync",
            employee="priya",
            question="Client call overlaps the Team Offsite. Reschedule the call or skip the offsite?",
            task_id="task-101",
            context_id="ctx-101",
            conflicts=[{"event": "Client call"}],
        )
        decision_id = decision["id"]

        # Check pending decisions endpoint
        resp = self.client.get("/api/decisions?employee=priya")
        self.assertEqual(resp.status_code, 200)
        decisions = resp.json()["decisions"]
        self.assertTrue(any(d["id"] == decision_id for d in decisions))

        # Respond to decision as Priya
        priya_login = self.client.post("/api/auth/login", json={"username": "priya", "password": "priya123"}).json()
        priya_token = priya_login["token"]

        with patch.object(web_ui.company, "reply_to_employee", return_value="COMPLETED: Rescheduled successfully") as mock_reply:
            resp_action = self.client.post(
                f"/api/decisions/{decision_id}/respond",
                headers={"Authorization": f"Bearer {priya_token}"},
                json={"action": "approve_reschedule", "note": "Move client call to afternoon"},
            )
            self.assertEqual(resp_action.status_code, 200, resp_action.text)
            self.assertEqual(resp_action.json()["status"], "COMPLETED")
            mock_reply.assert_called_once()

        # Decision should now be resolved
        updated_decisions = storage.get_pending_decisions(employee="priya")
        self.assertFalse(any(d["id"] == decision_id for d in updated_decisions))

    def test_company_tools_agentic_alternatives_and_escalate(self):
        # Test find_alternative_times
        res_str = company_tools.find_alternative_times(
            invitees=["priya", "arjun"],
            duration_minutes=30,
            days_ahead=3,
        )
        slots = json.loads(res_str)
        self.assertIsInstance(slots, list)
        self.assertTrue(len(slots) > 0)
        self.assertIn("start", slots[0])
        self.assertIn("end", slots[0])

        # Test escalate_to_organizer
        esc_str = company_tools.escalate_to_organizer(
            meeting_id="offsite-sync",
            reason="Priya cannot reschedule her conflict.",
            options=["Keep time and drop Priya", "Reschedule to another day", "Cancel meeting"],
        )
        esc_data = json.loads(esc_str)
        self.assertEqual(esc_data["status"], "ESCALATED")
        self.assertEqual(esc_data["meeting_id"], "offsite-sync")
        self.assertEqual(len(esc_data["options"]), 3)


if __name__ == "__main__":
    unittest.main()
