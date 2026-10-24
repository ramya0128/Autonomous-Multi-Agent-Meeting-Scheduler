"""Unit and integration tests for the A2A Agent Registry."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "agents"))

import agent_registry
import company_tools
import web_ui
from starlette.testclient import TestClient


class AgentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_data_dir = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.temp_dir.name
        agent_registry.init_registry()

    def tearDown(self):
        if self.orig_data_dir is not None:
            os.environ["DATA_DIR"] = self.orig_data_dir
        else:
            os.environ.pop("DATA_DIR", None)
        self.temp_dir.cleanup()

    def test_init_creates_default_agents(self):
        agents = agent_registry.list_agents()
        agent_ids = [a["id"] for a in agents]
        self.assertIn("priya", agent_ids)
        self.assertIn("arjun", agent_ids)
        self.assertIn("meera", agent_ids)
        self.assertEqual(len(agents), 3)

    def test_register_agent_success(self):
        new_agent = agent_registry.register_agent(
            agent_id="rahul",
            url="http://127.0.0.1:9104",
            name="Rahul Verma",
            role="employee",
            skills=["calendar", "reschedule"],
            metadata={"department": "Engineering"},
        )
        self.assertEqual(new_agent["id"], "rahul")
        self.assertEqual(new_agent["name"], "Rahul Verma")
        self.assertEqual(new_agent["url"], "http://127.0.0.1:9104")
        self.assertEqual(new_agent["skills"], ["calendar", "reschedule"])

        # Check retrieval
        retrieved = agent_registry.get_agent("rahul")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "Rahul Verma")

        # Verify persistence in SQLite database
        db_path = Path(self.temp_dir.name) / "agent_registry.db"
        self.assertTrue(db_path.exists())
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT name, url, role FROM agents WHERE id = 'rahul';").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "Rahul Verma")
            self.assertEqual(row[1], "http://127.0.0.1:9104")
            self.assertEqual(row[2], "employee")
        finally:
            conn.close()

        # Verify synchronized JSON export for backward compatibility
        reg_path = Path(self.temp_dir.name) / "agent_registry.json"
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        self.assertIn("rahul", data)

    def test_register_agent_validation(self):
        # Empty id
        with self.assertRaises(ValueError):
            agent_registry.register_agent("", "http://127.0.0.1:9105")

        # Invalid characters in id
        with self.assertRaises(ValueError):
            agent_registry.register_agent("rahul verma", "http://127.0.0.1:9105")

        # Invalid url
        with self.assertRaises(ValueError):
            agent_registry.register_agent("valid_id", "ftp://127.0.0.1:9105")

    def test_unregister_agent(self):
        # Register first
        agent_registry.register_agent("temp_bot", "http://127.0.0.1:9199")
        self.assertIsNotNone(agent_registry.get_agent("temp_bot"))

        # Unregister
        success = agent_registry.unregister_agent("temp_bot")
        self.assertTrue(success)
        self.assertIsNone(agent_registry.get_agent("temp_bot"))

        # Unregister nonexistent
        self.assertFalse(agent_registry.unregister_agent("nonexistent_bot"))

    def test_update_agent_status(self):
        self.assertTrue(agent_registry.update_agent_status("priya", "offline"))
        agent = agent_registry.get_agent("priya")
        self.assertEqual(agent["status"], "offline")

    def test_registry_employees_mapping_compatibility(self):
        mapping = agent_registry.RegistryEmployeesMapping()
        # Item access
        self.assertEqual(mapping["priya"], "http://127.0.0.1:9101")
        self.assertIn("priya", mapping)
        self.assertNotIn("ghost_agent", mapping)

        # Keys
        keys = list(mapping.keys())
        self.assertIn("priya", keys)
        self.assertIn("arjun", keys)
        self.assertIn("meera", keys)

        # Dynamic addition via dictionary assignment
        mapping["suresh"] = "http://127.0.0.1:9105"
        self.assertIn("suresh", mapping)
        self.assertEqual(mapping["suresh"], "http://127.0.0.1:9105")

        # Dynamic removal via del
        del mapping["suresh"]
        self.assertNotIn("suresh", mapping)

    def test_company_tools_integration_uses_registry(self):
        # company_tools.EMPLOYEES should dynamically see new agents
        agent_registry.register_agent("kavya", "http://127.0.0.1:9106", name="Kavya Nair")
        self.assertIn("kavya", company_tools.EMPLOYEES)
        self.assertEqual(company_tools.EMPLOYEES["kavya"], "http://127.0.0.1:9106")

        agent_registry.unregister_agent("kavya")
        self.assertNotIn("kavya", company_tools.EMPLOYEES)


class AgentRegistryAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_data_dir = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.temp_dir.name
        agent_registry.init_registry()
        self.client = TestClient(web_ui.app)

    def tearDown(self):
        self.client.close()
        if self.orig_data_dir is not None:
            os.environ["DATA_DIR"] = self.orig_data_dir
        else:
            os.environ.pop("DATA_DIR", None)
        self.temp_dir.cleanup()

    def test_get_registered_agents_api(self):
        resp = self.client.get("/api/registry/agents")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("agents", data)
        ids = [a["id"] for a in data["agents"]]
        self.assertIn("priya", ids)
        self.assertIn("arjun", ids)

    def test_register_agent_api(self):
        payload = {
            "id": "ananya",
            "name": "Ananya Roy",
            "url": "http://127.0.0.1:9107",
            "role": "employee",
            "skills": ["calendar", "meetings"],
            "metadata": {"team": "AI Research"},
        }
        resp = self.client.post("/api/registry/agents", json=payload)
        self.assertEqual(resp.status_code, 200)
        res_data = resp.json()
        self.assertTrue(res_data["success"])
        self.assertEqual(res_data["agent"]["id"], "ananya")

        # Verify get by id
        get_resp = self.client.get("/api/registry/agents/ananya")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["agent"]["name"], "Ananya Roy")

    def test_register_agent_api_validation_error(self):
        resp = self.client.post("/api/registry/agents", json={"id": "bad id!!", "url": "not-a-url"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_unregister_agent_api(self):
        agent_registry.register_agent("deleteme", "http://127.0.0.1:9108")
        resp = self.client.delete("/api/registry/agents/deleteme")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        # Subsequent get returns 404
        get_resp = self.client.get("/api/registry/agents/deleteme")
        self.assertEqual(get_resp.status_code, 404)

    def test_ping_agent_api(self):
        with patch("web_ui.get_agent_card", new_callable=AsyncMock) as mock_card:
            mock_card.return_value = "Priya's Agent Card"
            resp = self.client.post("/api/registry/agents/priya/ping")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "online")

    def test_registry_html_page_served(self):
        resp = self.client.get("/registry")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("A2A Agent Registry", resp.text)
        self.assertIn("Registered Autonomous Agents", resp.text)
        self.assertIn('id="register-agent-dialog"', resp.text)

    def test_register_agent_api_forbidden_for_employee(self):
        with patch("web_ui._get_request_user", return_value={"username": "priya", "role": "employee"}):
            payload = {"id": "sneha", "name": "Sneha Sen", "url": "http://127.0.0.1:9109"}
            resp = self.client.post("/api/registry/agents", json=payload)
            self.assertEqual(resp.status_code, 403)
            self.assertIn("Forbidden", resp.json()["error"])

    def test_register_agent_api_auto_port_and_calendar(self):
        with patch("web_ui._spawn_employee_agent_process") as mock_spawn:
            payload = {"id": "sneha", "name": "Sneha Sen"}
            resp = self.client.post("/api/registry/agents", json=payload)
            self.assertEqual(resp.status_code, 200)
            res = resp.json()
            self.assertTrue(res["success"])
            self.assertTrue(res["agent"]["url"].startswith("http://127.0.0.1:"))
            self.assertEqual(mock_spawn.call_count, 1)


if __name__ == "__main__":
    unittest.main()
