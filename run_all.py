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
                "name": "Priya's Agent",
                "cmd": [PYTHON, str(ROOT / "agents" / "employee_agent.py"), "priya", "9101"],
                "cwd": str(ROOT / "agents"),
                "port": 9101,
            },
            {
                "name": "Arjun's Agent",
                "cmd": [PYTHON, str(ROOT / "agents" / "employee_agent.py"), "arjun", "9102"],
                "cwd": str(ROOT / "agents"),
                "port": 9102,
            },
            {
                "name": "Meera's Agent",
                "cmd": [PYTHON, str(ROOT / "agents" / "employee_agent.py"), "meera", "9103"],
                "cwd": str(ROOT / "agents"),
                "port": 9103,
            },
        ]
    services.append({
        "name": "Web Dashboard",
        "cmd": [PYTHON, str(ROOT / "web_ui.py")],
        "cwd": str(ROOT),
        "port": 8080,
    })
    return services


SERVICES = get_services()


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

    for svc in SERVICES:
        start_service(svc)
        time.sleep(0.5)

    print("\n[Supervisor] All services are online and being monitored.")

    try:
        while True:
            time.sleep(2)
            for svc in SERVICES:
                proc = svc.get("proc")
                if proc is not None:
                    ret = proc.poll()
                    if ret is not None:
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
