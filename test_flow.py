"""Offline A2A plumbing test using only temporary fake-calendar data.

Run with ``CALENDAR_BACKEND=fake LLM_MODE=fake python test_flow.py``.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENTS_DIR = ROOT / "agents"
MEETING_START = "2040-08-12T10:00:00+05:30"
MEETING_END = "2040-08-12T11:00:00+05:30"


def _seed_data(data_dir: Path):
    calendars_dir = data_dir / "calendars"
    calendars_dir.mkdir(parents=True)
    (data_dir / "meetings.json").write_text(json.dumps([{
        "id": "offsite",
        "title": "Team Offsite",
        "start": MEETING_START,
        "end": MEETING_END,
        "location": "Room 1",
        "invitees": ["arjun", "meera", "priya"],
        "requested_invitees": ["arjun", "meera", "priya"],
        "pending_invitees": [],
        "declined_invitees": [],
        "notes": "",
    }], indent=2), encoding="utf-8")
    calendars = {
        "arjun": [],
        "meera": [{
            "id": "leave",
            "title": "On leave",
            "start": "2040-08-12T09:00:00+05:30",
            "end": "2040-08-12T17:00:00+05:30",
            "location": "",
        }],
        "priya": [{
            "id": "client-call",
            "title": "Client call",
            "start": "2040-08-12T10:00:00+05:30",
            "end": "2040-08-12T11:00:00+05:30",
            "location": "",
        }],
    }
    for employee, events in calendars.items():
        (calendars_dir / f"{employee}.json").write_text(
            json.dumps(events, indent=2), encoding="utf-8")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_agents(procs, ports):
    pending = set(ports)
    deadline = time.monotonic() + 30
    while pending and time.monotonic() < deadline:
        for process, port in zip(procs, ports):
            if port not in pending:
                continue
            if process.poll() is not None:
                error = process.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Employee agent on port {port} exited: {error}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    pending.remove(port)
            except OSError:
                pass
        if pending:
            time.sleep(0.1)
    if pending:
        raise RuntimeError(f"Employee agents did not start on ports {sorted(pending)}")
    time.sleep(0.5)


def main():
    env = {
        **os.environ,
        "LLM_MODE": "fake",
        "CALENDAR_BACKEND": "fake",
        "SHOW_TOOLS": "0",
        "PYTHON_DOTENV_DISABLED": "1",
    }
    failures = 0

    def check(label, got, expect):
        nonlocal failures
        ok = got.startswith(expect)
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}\n      -> {got[:140]}")

    with tempfile.TemporaryDirectory(prefix="a2a-test-flow-") as temp_dir:
        env["DATA_DIR"] = temp_dir
        _seed_data(Path(temp_dir))
        employee_ports = [_free_port() for _ in range(3)]
        procs = [
            subprocess.Popen(
                [sys.executable, "employee_agent.py", name, str(port)],
                cwd=AGENTS_DIR,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            for name, port in zip(("priya", "arjun", "meera"), employee_ports)
        ]
        try:
            _wait_for_agents(procs, employee_ports)
            os.environ.update(env)
            sys.path.insert(0, str(AGENTS_DIR))
            import company_agent as company
            company.company_tools.EMPLOYEES = {
                name: f"http://127.0.0.1:{port}"
                for name, port in zip(("priya", "arjun", "meera"), employee_ports)
            }

            check("arjun is free -> books", company.send_invite("arjun", "offsite"), "COMPLETED")
            check("meera on leave -> rejects", company.send_invite("meera", "offsite"), "REJECTED")
            check(
                "priya has a clash -> asks a question",
                company.send_invite("priya", "offsite"),
                "INPUT_REQUIRED",
            )
            check(
                "reply on the SAME task -> books",
                company.reply_to_employee("priya", "Yes, book anyway."),
                "COMPLETED",
            )
            check(
                "replying again (task finished) -> guarded",
                company.reply_to_employee("priya", "hello"),
                "ERROR",
            )
            check("unknown employee", company.send_invite("bob", "offsite"), "ERROR")
            procs[1].terminate()
            time.sleep(1)
            check(
                "agent down -> clean error",
                company.send_invite("arjun", "offsite"),
                "UNREACHABLE",
            )
        finally:
            for process in procs:
                if process.poll() is None:
                    process.terminate()
            for process in procs:
                process.wait(timeout=10)

    print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
