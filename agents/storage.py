"""Durable storage and audit logging for A2A Meeting Scheduler.

Manages:
- Users & authentication sessions (data/users.json)
- Durable A2A tasks (data/tasks.json)
- Pending employee decisions (data/pending_decisions.json)
- Append-only audit log & live feed (data/audit_log.json)
"""
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(os.getenv(
    "DATA_DIR",
    str(Path(__file__).resolve().parent.parent / "data"),
)).expanduser()
TZ = ZoneInfo("Asia/Kolkata")

_STORE_LOCK = threading.RLock()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        for attempt in range(5):
            try:
                os.replace(temp_path, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


# ============================================================================
# User Accounts & Authentication
# ============================================================================
USERS_PATH = DATA_DIR / "users.json"

DEFAULT_USERS = {
    "organizer": {
        "username": "organizer",
        "name": "Meeting Organizer",
        "role": "organizer",
        "password_hash": hashlib.sha256("admin123".encode()).hexdigest(),
    },
    "priya": {
        "username": "priya",
        "name": "Priya Sharma",
        "role": "employee",
        "password_hash": hashlib.sha256("priya123".encode()).hexdigest(),
    },
    "arjun": {
        "username": "arjun",
        "name": "Arjun Mehta",
        "role": "employee",
        "password_hash": hashlib.sha256("arjun123".encode()).hexdigest(),
    },
    "meera": {
        "username": "meera",
        "name": "Meera Iyer",
        "role": "employee",
        "password_hash": hashlib.sha256("meera123".encode()).hexdigest(),
    },
}

_SESSIONS: dict[str, dict] = {}


def init_storage():
    """Ensure all required json files exist."""
    with _STORE_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not USERS_PATH.exists():
            _atomic_write(USERS_PATH, json.dumps(DEFAULT_USERS, indent=2))
        tasks_p = DATA_DIR / "tasks.json"
        if not tasks_p.exists():
            _atomic_write(tasks_p, json.dumps({}, indent=2))
        dec_p = DATA_DIR / "pending_decisions.json"
        if not dec_p.exists():
            _atomic_write(dec_p, json.dumps([], indent=2))
        audit_p = DATA_DIR / "audit_log.json"
        if not audit_p.exists():
            _atomic_write(audit_p, json.dumps([], indent=2))


init_storage()


def authenticate_user(username: str, password: str) -> dict | None:
    username = username.strip().lower()
    users = _read_json(USERS_PATH, DEFAULT_USERS)
    user = users.get(username)
    if not user:
        return None
    pwd_hash = hashlib.sha256(password.strip().encode()).hexdigest()
    if user.get("password_hash") != pwd_hash:
        return None
    token = str(uuid.uuid4())
    session_data = {
        "token": token,
        "username": username,
        "name": user["name"],
        "role": user["role"],
        "created_at": time.time(),
        "expires_at": time.time() + (86400 * 7),
    }
    with _STORE_LOCK:
        _SESSIONS[token] = session_data
    return session_data


def get_user_session(token: str) -> dict | None:
    if not token:
        return None
    with _STORE_LOCK:
        session = _SESSIONS.get(token)
        if not session:
            return None
        if time.time() > session.get("expires_at", 0):
            del _SESSIONS[token]
            return None
        return dict(session)


def revoke_session(token: str) -> None:
    with _STORE_LOCK:
        _SESSIONS.pop(token, None)


# Inter-agent auth token
AGENT_AUTH_TOKEN = os.getenv("AGENT_AUTH_TOKEN", "a2a-secret-token-key-2026")


def verify_agent_token(auth_header: str | None) -> bool:
    if not AGENT_AUTH_TOKEN:
        return True
    if not auth_header:
        return False
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1] == AGENT_AUTH_TOKEN
    return auth_header == AGENT_AUTH_TOKEN


# ============================================================================
# Audit Logging / Live Event Feed
# ============================================================================
AUDIT_LOG_PATH = DATA_DIR / "audit_log.json"


def log_event(event_type: str, actor: str, message: str,
              meeting_id: str | None = None, details: dict | None = None) -> dict:
    """Record a persistent audit log event that also powers the company live feed."""
    event = {
        "id": f"evt-{uuid.uuid4().hex[:8]}",
        "timestamp": _now_iso(),
        "event_type": event_type,
        "actor": actor,
        "message": message,
        "meeting_id": meeting_id,
        "details": details or {},
    }
    with _STORE_LOCK:
        events = _read_json(AUDIT_LOG_PATH, [])
        events.append(event)
        # Cap audit log to recent 500 entries to prevent unbounded growth
        if len(events) > 500:
            events = events[-500:]
        _atomic_write(AUDIT_LOG_PATH, json.dumps(events, indent=2, ensure_ascii=False))
    return event


def get_audit_log(limit: int = 50, meeting_id: str | None = None,
                  actor: str | None = None) -> list[dict]:
    with _STORE_LOCK:
        events = _read_json(AUDIT_LOG_PATH, [])
    if meeting_id:
        events = [e for e in events if e.get("meeting_id") == meeting_id]
    if actor:
        events = [e for e in events if e.get("actor") == actor]
    return list(reversed(events[-limit:]))


# ============================================================================
# Pending Decisions (Conflict flow)
# ============================================================================
PENDING_DECISIONS_PATH = DATA_DIR / "pending_decisions.json"
DEFAULT_DECISION_TIMEOUT_SECONDS = int(os.getenv("DECISION_TIMEOUT_SECONDS", "300"))  # 5 min


def create_pending_decision(
    meeting_id: str,
    employee: str,
    question: str,
    task_id: str,
    context_id: str,
    conflicts: list[dict] | None = None,
    timeout_seconds: int = DEFAULT_DECISION_TIMEOUT_SECONDS,
) -> dict:
    """Creates and persists an employee decision request."""
    employee = employee.lower()
    now = datetime.now(TZ)
    timeout_at = (now + timedelta(seconds=timeout_seconds)).isoformat(timespec="seconds")
    decision_id = f"dec-{uuid.uuid4().hex[:8]}"
    decision = {
        "id": decision_id,
        "meeting_id": meeting_id,
        "employee": employee,
        "question": question,
        "conflicts": conflicts or [],
        "task_id": task_id,
        "context_id": context_id,
        "created_at": now.isoformat(timespec="seconds"),
        "timeout_at": timeout_at,
        "status": "PENDING",  # PENDING, ANSWERED, TIMED_OUT
        "response": None,
    }
    with _STORE_LOCK:
        decisions = _read_json(PENDING_DECISIONS_PATH, [])
        # Replace any existing pending decision for this meeting + employee
        decisions = [
            d for d in decisions
            if not (d.get("meeting_id") == meeting_id and d.get("employee") == employee and d.get("status") == "PENDING")
        ]
        decisions.append(decision)
        _atomic_write(PENDING_DECISIONS_PATH, json.dumps(decisions, indent=2, ensure_ascii=False))

    emp_name = employee.title()
    log_event(
        event_type="CONFLICT_WAITING",
        actor=employee,
        message=f"{emp_name}'s agent found a conflict and is waiting on {emp_name}.",
        meeting_id=meeting_id,
        details={"question": question, "conflicts": conflicts},
    )
    return decision


def get_pending_decisions(employee: str | None = None, include_answered: bool = False) -> list[dict]:
    with _STORE_LOCK:
        decisions = _read_json(PENDING_DECISIONS_PATH, [])
    if employee:
        employee = employee.lower()
        decisions = [d for d in decisions if d.get("employee") == employee]
    if not include_answered:
        decisions = [d for d in decisions if d.get("status") == "PENDING"]
    return decisions


def get_pending_decision_by_id(decision_id: str) -> dict | None:
    with _STORE_LOCK:
        decisions = _read_json(PENDING_DECISIONS_PATH, [])
    for d in decisions:
        if d.get("id") == decision_id:
            return dict(d)
    return None


def resolve_pending_decision(decision_id: str, action: str,
                             suggested_time: str = "", note: str = "") -> dict | None:
    """Marks a decision answered and records the response."""
    with _STORE_LOCK:
        decisions = _read_json(PENDING_DECISIONS_PATH, [])
        target = None
        for d in decisions:
            if d.get("id") == decision_id and d.get("status") == "PENDING":
                target = d
                break
        if not target:
            return None
        target["status"] = "ANSWERED"
        target["response"] = {
            "action": action,  # approve_reschedule, decline, suggest_time
            "suggested_time": suggested_time,
            "note": note,
            "answered_at": _now_iso(),
        }
        _atomic_write(PENDING_DECISIONS_PATH, json.dumps(decisions, indent=2, ensure_ascii=False))
        return dict(target)


def check_and_expire_decision_timeouts() -> list[dict]:
    """Check for overdue decisions, mark them TIMED_OUT, and generate audit escalation."""
    now_dt = datetime.now(TZ)
    expired = []
    with _STORE_LOCK:
        decisions = _read_json(PENDING_DECISIONS_PATH, [])
        changed = False
        for d in decisions:
            if d.get("status") == "PENDING":
                timeout_dt = datetime.fromisoformat(d["timeout_at"])
                if now_dt >= timeout_dt:
                    d["status"] = "TIMED_OUT"
                    d["response"] = {"action": "timeout", "answered_at": _now_iso()}
                    expired.append(dict(d))
                    changed = True
        if changed:
            _atomic_write(PENDING_DECISIONS_PATH, json.dumps(decisions, indent=2, ensure_ascii=False))

    for exp in expired:
        emp = exp["employee"].title()
        log_event(
            event_type="DECISION_TIMEOUT",
            actor="system",
            message=f"{emp} did not respond within deadline. Escalated to organizer.",
            meeting_id=exp["meeting_id"],
            details=exp,
        )
    return expired


# ============================================================================
# Durable A2A Task Registry
# ============================================================================
TASKS_PATH = DATA_DIR / "tasks.json"


def save_task_session(meeting_id: str, employee: str, data: dict) -> None:
    key = f"{meeting_id}:{employee.lower()}"
    with _STORE_LOCK:
        tasks = _read_json(TASKS_PATH, {})
        tasks[key] = {
            **data,
            "meeting_id": meeting_id,
            "employee": employee.lower(),
            "updated_at": _now_iso(),
        }
        _atomic_write(TASKS_PATH, json.dumps(tasks, indent=2, ensure_ascii=False))


def get_task_session(meeting_id: str, employee: str) -> dict | None:
    key = f"{meeting_id}:{employee.lower()}"
    with _STORE_LOCK:
        tasks = _read_json(TASKS_PATH, {})
        return tasks.get(key)


def get_all_task_sessions() -> dict[tuple[str, str], dict]:
    with _STORE_LOCK:
        tasks = _read_json(TASKS_PATH, {})
    res = {}
    for key, val in tasks.items():
        if ":" in key:
            m_id, emp = key.split(":", 1)
            res[(m_id, emp)] = val
    return res
