"""Supervisor to run and auto-heal all A2A agents and web UI simultaneously.

Usage:
    python run_all.py

Starts:
  - Priya's Agent  (:9101)
  - Arjun's Agent  (:9102)
  - Meera's Agent  (:9103)
  - Web Dashboard  (:8080)

If any agent or server process crashes or stops, the supervisor automatically
restarts it so your application never unexpectedly goes down.
"""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def get_services():
    """Discover registered agents from agent_registry and return service descriptors."""
    services = []
    try:
        sys.path.insert(0, str(ROOT / "agents"))
        import agent_registry
        agents = agent_registry.list_agents(role="employee")
        for a in agents:
            url = a.get("url", "")
            port = 9101
            match = re.search(r":(\d+)", url)
            if match:
                port = int(match.group(1))
            services.append({
                "id": a["id"],
                "name": f"{a.get('name', a['id'])}'s Agent",
                "cmd": [PYTHON, str(ROOT / "agents" / "employee_agent.py"), a["id"], str(port)],
                "cwd": str(ROOT / "agents"),
                "port": port,
            })
    except Exception as e:
        print(f"[Supervisor] Note: could not load agent registry, using defaults: {e}")

    if not services:
        services = [
            {
                "id": "priya",
                "name": "Priya's Agent",
                "cmd": [PYTHON, str(ROOT / "agents" / "employee_agent.py"), "priya", "9101"],
                "cwd": str(ROOT / "agents"),
                "port": 9101,
            },
            {
                "id": "arjun",
                "name": "Arjun's Agent",
                "cmd": [PYTHON, str(ROOT / "agents" / "employee_agent.py"), "arjun", "9102"],
                "cwd": str(ROOT / "agents"),
                "port": 9102,
            },
            {
                "id": "meera",
                "name": "Meera's Agent",
                "cmd": [PYTHON, str(ROOT / "agents" / "employee_agent.py"), "meera", "9103"],
                "cwd": str(ROOT / "agents"),
                "port": 9103,
            },
        ]
    services.append({
        "id": "web_dashboard",
        "name": "Web Dashboard",
        "cmd": [PYTHON, str(ROOT / "web_ui.py")],
        "cwd": str(ROOT),
        "port": 8080,
    })
    return services


SERVICES = get_services()


def sync_services_with_registry(services):
    """Dynamically track registered agents so adding or deleting in the UI immediately updates supervisor."""
    try:
        import agent_registry
        registered_agents = {a["id"]: a for a in agent_registry.list_agents(role="employee")}
    except Exception:
        return

    # 1. Stop and remove services that were unregistered from the database
    to_remove = []
    for svc in list(services):
        agent_id = svc.get("id")
        if agent_id and agent_id != "web_dashboard":
            if agent_id not in registered_agents:
                print(f"[Supervisor] Agent '{svc['name']}' was removed from registry. Stopping service...")
                proc = svc.get("proc")
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
                to_remove.append(svc)
    for svc in to_remove:
        if svc in services:
            services.remove(svc)

    # 2. Automatically launch any newly registered agents
    existing_ids = {s.get("id") for s in services}
    for agent_id, a in registered_agents.items():
        if agent_id not in existing_ids:
            url = a.get("url", "")
            port = 9101
            match = re.search(r":(\d+)", url)
            if match:
                port = int(match.group(1))
            new_svc = {
                "id": agent_id,
                "name": f"{a.get('name', agent_id)}'s Agent",
                "cmd": [PYTHON, str(ROOT / "agents" / "employee_agent.py"), agent_id, str(port)],
                "cwd": str(ROOT / "agents"),
                "port": port,
            }
            services.append(new_svc)
            print(f"[Supervisor] Newly registered agent detected: {new_svc['name']}.")
            start_service(new_svc)


def start_service(svc):
    print(f"[Supervisor] Launching {svc['name']} on port {svc['port']}...")
    env = dict(os.environ)
    proc = subprocess.Popen(
        svc["cmd"],
        cwd=svc["cwd"],
        env=env,
    )
    svc["proc"] = proc
    return proc


def main():
    print("=" * 65)
    print("  GATHER A2A MEETING SCHEDULER - SUPERVISOR")
    print("=" * 65)
    print("Starting all agents and web UI with automatic health monitoring.")
    print("Web Dashboard: http://127.0.0.1:8080")
    print("Press Ctrl+C to stop all services.\n")

    for svc in list(SERVICES):
        start_service(svc)
        time.sleep(0.5)

    print("\n[Supervisor] All services are online and being monitored.")

    crash_counts = {}
    try:
        while True:
            time.sleep(2)
            sync_services_with_registry(SERVICES)
            for svc in list(SERVICES):
                proc = svc.get("proc")
                if proc is not None:
                    ret = proc.poll()
                    if ret is not None:
                        svc_id = svc.get("id", svc["name"])
                        crash_counts[svc_id] = crash_counts.get(svc_id, 0) + 1
                        if crash_counts[svc_id] > 5:
                            print(f"\n[Supervisor] ERROR: {svc['name']} repeatedly crashed ({crash_counts[svc_id]} times). Pausing auto-restart.")
                            svc["proc"] = None
                            continue
                        print(f"\n[Supervisor] WARNING: {svc['name']} stopped (exit code {ret}). Auto-restarting...")
                        start_service(svc)
    except KeyboardInterrupt:
        print("\n\n[Supervisor] Shutting down all services...")
        for svc in SERVICES:
            proc = svc.get("proc")
            if proc and proc.poll() is None:
                print(f"[Supervisor] Stopping {svc['name']}...")
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
        print("[Supervisor] All services stopped cleanly.")


if __name__ == "__main__":
    main()
