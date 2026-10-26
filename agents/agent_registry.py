"""Agent Registry for A2A multi-agent meeting scheduler.

Provides dynamic registration, discovery, health tracking, and persistence
for autonomous agents, removing hardcoded employee lists.

Stores registry entries in SQLite database (DATA_DIR / "agent_registry.db")
with thread-safe atomic transactions and automatic JSON export for backward
compatibility.
"""
import copy
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import MutableMapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
_REGISTRY_LOCK = threading.RLock()

DEFAULT_EMPLOYEE_SKILLS = [
    "book_meeting",
    "check_conflicts",
    "reschedule_meeting",
    "cancel_meeting",
]

DEFAULT_EMPLOYEES = {
    "priya": {
        "id": "priya",
        "name": "Priya Sharma",
        "url": "http://127.0.0.1:9101",
        "role": "employee",
        "skills": list(DEFAULT_EMPLOYEE_SKILLS),
        "status": "online",
        "registered_at": "2026-10-23T00:00:00+05:30",
        "last_seen": "2026-10-23T00:00:00+05:30",
        "metadata": {"department": "Engineering", "email": "priya@example.com"},
    },
    "arjun": {
        "id": "arjun",
        "name": "Arjun Mehta",
        "url": "http://127.0.0.1:9102",
        "role": "employee",
        "skills": list(DEFAULT_EMPLOYEE_SKILLS),
        "status": "online",
        "registered_at": "2026-10-23T00:00:00+05:30",
        "last_seen": "2026-10-23T00:00:00+05:30",
        "metadata": {"department": "Product", "email": "arjun@example.com"},
    },
    "meera": {
        "id": "meera",
        "name": "Meera Iyer",
        "url": "http://127.0.0.1:9103",
        "role": "employee",
        "skills": list(DEFAULT_EMPLOYEE_SKILLS),
        "status": "online",
        "registered_at": "2026-10-23T00:00:00+05:30",
        "last_seen": "2026-10-23T00:00:00+05:30",
        "metadata": {"department": "Design", "email": "meera@example.com"},
    },
}


def introspect_agent_skills(url: str, timeout: float = 3.0) -> list[str]:
    """Introspect an agent's published skills by querying its A2A AgentCard endpoint.

    Fetches /.well-known/agent.json from the agent's URL and returns the list of skill
    IDs (or names). Returns an empty list if unreachable or if no skills are declared.
    """
    if not url or not isinstance(url, str):
        return []
    base_url = url.strip().rstrip("/")
    endpoints = [
        f"{base_url}/.well-known/agent-card.json",
        f"{base_url}/.well-known/agent.json",
    ]

    headers = {}
    try:
        from storage import AGENT_AUTH_TOKEN
        if AGENT_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {AGENT_AUTH_TOKEN}"
    except Exception:
        pass

    try:
        import httpx
        with httpx.Client(timeout=timeout, headers=headers) as client:
            for card_url in endpoints:
                try:
                    resp = client.get(card_url)
                    if resp.status_code == 200:
                        card_data = resp.json()
                        raw_skills = card_data.get("skills", [])
                        extracted = []
                        for s in raw_skills:
                            if isinstance(s, dict):
                                skill_id = s.get("id") or s.get("name")
                                if skill_id and skill_id not in extracted:
                                    extracted.append(str(skill_id))
                            elif isinstance(s, str) and s.strip() and s.strip() not in extracted:
                                extracted.append(s.strip())
                        if extracted:
                            return extracted
                except Exception:
                    continue
    except Exception:
        pass
    return []


def update_agent_skills(agent_id: str, skills: list[str]) -> bool:
    """Update skills list for an agent in SQLite."""
    if not agent_id:
        return False
    agent_id = str(agent_id).strip().lower()
    now = _now_iso()
    skills_json = json.dumps(list(skills))

    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            cursor = conn.execute(
                "UPDATE agents SET skills = ?, last_seen = ? WHERE id = ?;",
                (skills_json, now, agent_id),
            )
            if cursor.rowcount > 0:
                _sync_json_export(conn)
                return True
            return False


def sync_agent_skills(agent_id: str, timeout: float = 3.0) -> list[str] | None:
    """Introspect an agent's live endpoint and update its skills in SQLite."""
    agent = get_agent(agent_id)
    if not agent or not agent.get("url"):
        return None
    skills = introspect_agent_skills(agent["url"], timeout=timeout)
    if skills:
        update_agent_skills(agent_id, skills)
        return skills
    return agent.get("skills", [])


def get_registry_db_path() -> Path:
    data_dir = Path(os.getenv(
        "DATA_DIR",
        str(Path(__file__).resolve().parent.parent / "data"),
    )).expanduser()
    return data_dir / "agent_registry.db"


def get_registry_path() -> Path:
    data_dir = Path(os.getenv(
        "DATA_DIR",
        str(Path(__file__).resolve().parent.parent / "data"),
    )).expanduser()
    return data_dir / "agent_registry.json"


def _get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = get_registry_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


@contextmanager
def _db_connection(db_path: Path | None = None):
    conn = _get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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


def _now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _row_to_agent_dict(row: sqlite3.Row) -> dict:
    skills_raw = row["skills"]
    if isinstance(skills_raw, str):
        try:
            skills = json.loads(skills_raw)
        except Exception:
            skills = [skills_raw]
    else:
        skills = list(skills_raw or [])

    metadata_raw = row["metadata"]
    if isinstance(metadata_raw, str):
        try:
            metadata = json.loads(metadata_raw)
        except Exception:
            metadata = {}
    else:
        metadata = dict(metadata_raw or {})

    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "role": row["role"],
        "skills": skills,
        "status": row["status"],
        "registered_at": row["registered_at"],
        "last_seen": row["last_seen"],
        "metadata": metadata,
    }


def _insert_raw_agent(conn: sqlite3.Connection, info: dict) -> None:
    agent_id = str(info["id"]).strip().lower()
    skills_json = json.dumps(info.get("skills") or ["calendar", "meetings", "book_meeting"])
    metadata_json = json.dumps(info.get("metadata") or {})
    conn.execute(
        """
        INSERT OR REPLACE INTO agents (
            id, name, url, role, skills, status, registered_at, last_seen, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            agent_id,
            info.get("name", agent_id.title()),
            info.get("url", ""),
            info.get("role", "employee"),
            skills_json,
            info.get("status", "online"),
            info.get("registered_at", _now_iso()),
            info.get("last_seen", _now_iso()),
            metadata_json,
        ),
    )


def _sync_json_export(conn: sqlite3.Connection) -> None:
    try:
        path = get_registry_path()
        cursor = conn.execute("SELECT * FROM agents ORDER BY id ASC;")
        rows = cursor.fetchall()
        data = {row["id"]: _row_to_agent_dict(row) for row in rows}
        _atomic_write(path, json.dumps(data, indent=2))
    except Exception:
        pass


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'employee',
            skills TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'online',
            registered_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    cursor = conn.execute("SELECT COUNT(*) FROM agents;")
    count = cursor.fetchone()[0]
    if count == 0:
        json_path = get_registry_path()
        migrated = False
        if json_path.exists():
            try:
                raw_data = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(raw_data, dict) and raw_data:
                    for _agent_id, info in raw_data.items():
                        _insert_raw_agent(conn, info)
                    migrated = True
            except Exception:
                migrated = False
        if not migrated:
            for _agent_id, info in DEFAULT_EMPLOYEES.items():
                _insert_raw_agent(conn, info)
        _sync_json_export(conn)
    else:
        legacy_json = json.dumps(["calendar", "meetings", "book_meeting"])
        cursor = conn.execute("SELECT COUNT(*) FROM agents WHERE skills = ?;", (legacy_json,))
        if cursor.fetchone()[0] > 0:
            conn.execute(
                "UPDATE agents SET skills = ? WHERE skills = ?;",
                (json.dumps(DEFAULT_EMPLOYEE_SKILLS), legacy_json),
            )
            _sync_json_export(conn)


def init_registry() -> dict[str, dict]:
    """Ensure SQLite database and agents table exist, returning all registered agents."""
    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            cursor = conn.execute("SELECT * FROM agents ORDER BY id ASC;")
            rows = cursor.fetchall()
            return {row["id"]: _row_to_agent_dict(row) for row in rows}


def list_agents(role: str | None = None) -> list[dict]:
    """List all registered agents from SQLite, optionally filtered by role."""
    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            if role:
                cursor = conn.execute(
                    "SELECT * FROM agents WHERE role = ? ORDER BY id ASC;", (role,)
                )
            else:
                cursor = conn.execute("SELECT * FROM agents ORDER BY id ASC;")
            rows = cursor.fetchall()
            return [_row_to_agent_dict(row) for row in rows]


def get_agent(agent_id: str) -> dict | None:
    """Retrieve registered agent details from SQLite by normalized ID."""
    if not agent_id:
        return None
    agent_id = str(agent_id).strip().lower()
    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            cursor = conn.execute("SELECT * FROM agents WHERE id = ?;", (agent_id,))
            row = cursor.fetchone()
            return _row_to_agent_dict(row) if row else None


def register_agent(
    agent_id: str,
    url: str,
    name: str | None = None,
    role: str = "employee",
    skills: list[str] | None = None,
    metadata: dict | None = None,
    status: str = "online",
    introspect: bool = True,
) -> dict:
    """Register or update an agent dynamically in SQLite.
    
    Args:
        agent_id: Unique identifier for the agent (e.g. 'priya', 'rahul')
        url: Base HTTP endpoint for the A2A agent (e.g. 'http://127.0.0.1:9104')
        name: Human-friendly display name (e.g. 'Rahul Verma')
        role: Agent role (default 'employee')
        skills: List of skill strings (if None/empty and introspect=True, introspected from AgentCard)
        metadata: Optional dictionary of additional properties
        status: Initial agent status (default 'online')
        introspect: Whether to query the agent's live AgentCard for declared skills
    """
    if not agent_id or not isinstance(agent_id, str):
        raise ValueError("Agent ID is required and must be a non-empty string.")
    
    agent_id = agent_id.strip().lower()
    if not re.match(r"^[a-z0-9_-]+$", agent_id):
        raise ValueError("Agent ID may only contain alphanumeric characters, hyphens, and underscores.")

    if not url or not isinstance(url, str):
        raise ValueError("URL is required and must be a non-empty string.")
    
    url = url.strip().rstrip("/")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("URL must start with http:// or https://")

    if not name or not isinstance(name, str) or not name.strip():
        default_info = DEFAULT_EMPLOYEES.get(agent_id)
        name = default_info["name"] if default_info else agent_id.replace("_", " ").replace("-", " ").title()
    else:
        name = name.strip()

    if (skills is None or len(skills) == 0) and introspect:
        introspected = introspect_agent_skills(url)
        if introspected:
            skills = introspected

    if skills is None or len(skills) == 0:
        default_info = DEFAULT_EMPLOYEES.get(agent_id)
        if default_info and default_info.get("skills"):
            skills = list(default_info["skills"])
        elif (role or "employee") == "employee":
            skills = list(DEFAULT_EMPLOYEE_SKILLS)
        else:
            skills = []

    now = _now_iso()

    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            cursor = conn.execute("SELECT * FROM agents WHERE id = ?;", (agent_id,))
            existing_row = cursor.fetchone()
            is_new = existing_row is None

            if existing_row:
                existing_dict = _row_to_agent_dict(existing_row)
                registered_at = existing_dict.get("registered_at", now)
                merged_meta = {**(existing_dict.get("metadata") or {}), **(metadata or {})}
            else:
                registered_at = now
                merged_meta = metadata or {}

            skills_json = json.dumps(list(skills))
            metadata_json = json.dumps(merged_meta)

            conn.execute(
                """
                INSERT INTO agents (
                    id, name, url, role, skills, status, registered_at, last_seen, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    role = excluded.role,
                    skills = excluded.skills,
                    status = excluded.status,
                    last_seen = excluded.last_seen,
                    metadata = excluded.metadata;
                """,
                (
                    agent_id,
                    name,
                    url,
                    role or "employee",
                    skills_json,
                    status,
                    registered_at,
                    now,
                    metadata_json,
                ),
            )
            _sync_json_export(conn)

    agent_data = {
        "id": agent_id,
        "name": name,
        "url": url,
        "role": role or "employee",
        "skills": list(skills),
        "status": status,
        "registered_at": registered_at,
        "last_seen": now,
        "metadata": merged_meta,
    }

    try:
        import storage
        action = "registered" if is_new else "updated"
        storage.log_event(
            f"agent_{action}",
            actor=agent_id,
            message=f"Agent '{name}' ({agent_id}) {action} in Agent Registry at {url}.",
            details={"agent_id": agent_id, "url": url, "role": role},
        )
    except Exception:
        pass

    return copy.deepcopy(agent_data)


def unregister_agent(agent_id: str) -> bool:
    """Remove an agent from the SQLite registry."""
    if not agent_id:
        return False
    agent_id = str(agent_id).strip().lower()

    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            cursor = conn.execute("SELECT * FROM agents WHERE id = ?;", (agent_id,))
            row = cursor.fetchone()
            if not row:
                return False
            removed_name = row["name"]
            conn.execute("DELETE FROM agents WHERE id = ?;", (agent_id,))
            _sync_json_export(conn)

    try:
        import storage
        storage.log_event(
            "agent_unregistered",
            actor=agent_id,
            message=f"Agent '{removed_name}' ({agent_id}) removed from Agent Registry.",
            details={"agent_id": agent_id},
        )
    except Exception:
        pass

    return True


def update_agent_status(agent_id: str, status: str) -> bool:
    """Update online/offline status and last_seen timestamp in SQLite."""
    if not agent_id:
        return False
    agent_id = str(agent_id).strip().lower()
    now = _now_iso()

    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            cursor = conn.execute(
                "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?;",
                (status, now, agent_id),
            )
            if cursor.rowcount > 0:
                _sync_json_export(conn)
                return True
            return False


def get_employee_urls() -> dict[str, str]:
    """Return dictionary mapping employee agent IDs to endpoint URLs from SQLite."""
    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            cursor = conn.execute(
                "SELECT id, url FROM agents WHERE role = 'employee' AND url != '';"
            )
            return {row["id"]: row["url"] for row in cursor.fetchall()}


def get_employee_names() -> dict[str, str]:
    """Return dictionary mapping employee agent IDs to display names from SQLite."""
    with _REGISTRY_LOCK:
        with _db_connection() as conn:
            _init_db(conn)
            cursor = conn.execute("SELECT id, name FROM agents;")
            return {row["id"]: row["name"] for row in cursor.fetchall()}


def is_known_employee(name: str) -> bool:
    """Check if an employee is registered."""
    if not isinstance(name, str):
        return False
    return name.strip().lower() in get_employee_urls()


class RegistryEmployeesMapping(MutableMapping):
    """Dynamic dict-like proxy for company.company_tools.EMPLOYEES.

    Provides complete backward compatibility with code expecting EMPLOYEES
    to be a dict, while dynamically reading and writing to the SQLite Agent Registry.
    """

    def __getitem__(self, key):
        urls = get_employee_urls()
        norm_key = str(key).strip().lower()
        if norm_key in urls:
            return urls[norm_key]
        raise KeyError(key)

    def __setitem__(self, key, value):
        norm_key = str(key).strip().lower()
        register_agent(norm_key, str(value))

    def __delitem__(self, key):
        norm_key = str(key).strip().lower()
        if not unregister_agent(norm_key):
            raise KeyError(key)

    def __iter__(self):
        return iter(get_employee_urls())

    def __len__(self):
        return len(get_employee_urls())

    def __contains__(self, key):
        if not isinstance(key, str):
            return False
        return key.strip().lower() in get_employee_urls()

    def keys(self):
        return get_employee_urls().keys()

    def values(self):
        return get_employee_urls().values()

    def items(self):
        return get_employee_urls().items()

    def get(self, key, default=None):
        if not isinstance(key, str):
            return default
        return get_employee_urls().get(key.strip().lower(), default)

    def copy(self):
        return dict(get_employee_urls())

    def __repr__(self):
        return repr(get_employee_urls())

    def __str__(self):
        return str(get_employee_urls())


# Initialize registry SQLite database on import
init_registry()
