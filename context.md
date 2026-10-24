# Project Context: A2A Meeting Scheduler

## 1. Executive Summary

This project is an **Agent-to-Agent (A2A) Meeting Scheduling System** built on top of the official `a2a-sdk` (v1.2.1) and Groq LLMs (`openai/gpt-oss-120b`).

The core architecture separates company-level coordination from individual employee calendars:
- A central **Company Meeting Agent** serves as the A2A *client* and maintains the source of truth for company meetings (`data/meetings.json`).
- Each employee (**Priya**, **Arjun**, **Meera**) has their own autonomous **Employee Calendar Agent** acting as an A2A *server* (each running as a distinct HTTP JSON-RPC process on ports `9101`, `9102`, `9103`).
- When meetings are scheduled, rescheduled, or cancelled, the Company Agent interacts with the individual Employee Agents over A2A.
- Each Employee Agent checks its local calendar for schedule overlaps, decides whether to accept, decline, or pause to ask for human clarification, and manages its own calendar events.

---

## 2. High-Level Architecture & Flow

```
                                  +-------------------------------------+
                                  |     User / Browser Web UI (8080)    |
                                  | (Chat UI, Dashboard, Profiles, Nav) |
                                  +------------------+------------------+
                                                     |
                                                     v
                                  +-------------------------------------+
                                  |        Company Meeting Agent        |
                                  |       (CLI or web_ui backend)       |
                                  |   LLM (Groq) + company_tools.py     |
                                  |     Stores: data/meetings.json      |
                                  +---+--------------+--------------+---+
                                      |              |              |
                                  A2A |          A2A |          A2A |
                              JSON-RPC|      JSON-RPC|      JSON-RPC|
                               :9101  |        :9102 |        :9103 |
                                      v              v              v
                        +---------------+  +---------------+  +---------------+
                        | Priya's Agent |  | Arjun's Agent |  | Meera's Agent |
                        | (Port 9101)   |  | (Port 9102)   |  | (Port 9103)   |
                        +-------+-------+  +-------+-------+  +-------+-------+
                                |                  |                  |
                                v                  v                  v
                        +---------------+  +---------------+  +---------------+
                        | Priya Cal     |  | Arjun Cal     |  | Meera Cal     |
                        | (Fake/Google) |  | (Fake/Google) |  | (Fake/Google) |
                        +---------------+  +---------------+  +---------------+
```

---

## 3. Directory & File Structure

```
d:/a2a-meetings/
├── .env / .env.example          # Environment settings (Groq key, Google Calendar config, LLM mode)
├── requirements.txt            # Python dependencies (a2a-sdk, uvicorn, httpx, groq, google API libs)
├── README.md                   # Project overview, setup, and operational runbook
├── context.md                  # Comprehensive technical project documentation (this file)
├── web_ui.py                   # Starlette/Uvicorn web dashboard backend & REST/chat endpoints
├── migrate_meeting_times.py    # Utility to normalize ISO datetimes in data/meetings.json
│
├── agents/                     # Agent implementations & tools
│   ├── a2a_client.py           # A2A client utilities (card resolution, connection, message dispatch)
│   ├── calendar_backend.py     # Calendar implementations (FakeCalendar JSON & GoogleCalendar API v3)
│   ├── common.py               # Shared A2A server helpers (AgentCard, Task initializers, Starlette routes)
│   ├── company_agent.py        # Company Agent CLI entry point & operational bindings
│   ├── company_tools.py        # Company agent tool schemas, dispatch functions, session & meeting stores
│   ├── employee_agent.py       # Employee Agent server entry point & A2A Task executor
│   ├── employee_tools.py       # Employee calendar tools (check_conflicts, create_event, move, remove)
│   ├── fake_llm.py             # Deterministic mock LLM for offline A2A testing without API keys
│   ├── llm.py                  # Groq client integration & tool-execution loop
│   ├── company/
│   │   └── skills.md           # System prompt and operational guidelines for the Company Agent
│   └── employee/
│       └── skills.md           # Parameterized system prompt for Employee Agents ({name})
│
├── data/                       # Persistent state
│   ├── meetings.json           # Company meetings store
│   ├── calendars/              # Fake calendar JSON stores
│   │   ├── arjun.json
│   │   ├── meera.json
│   │   └── priya.json
│   └── google_calendar_token.json # Shared OAuth credentials for Google Calendar mode
│
├── ui/
│   └── index.html              # Single-page application frontend (vanilla JS/CSS, chat, calendar views)
│
└── test_*.py                   # Test suite
    ├── test_agent_tools.py     # Unit tests for tool schemas, skills, and LLM message loops
    ├── test_calendar_backend.py# Unit tests for interval overlap, fake calendar, and Google calendar
    ├── test_flow.py            # End-to-end integration test spawning agent subprocesses offline
    ├── test_meeting_invitee.py # Tests for invitee handling, concurrency, persistence, and deduplication
    └── test_web_ui.py          # Tests for Starlette dashboard endpoints, background jobs, and chat flows
```

---

## 4. Key Components & Implementation Details

### 4.1 A2A Protocol Implementation (`agents/common.py`, `agents/a2a_client.py`)
- **Protocol**: JSON-RPC over HTTP, complying with A2A SDK 1.2.1 specifications.
- **Agent Discovery**: Each agent exposes an `AgentCard` at `/.well-known/agent.json` defining skills, protocol version, streaming capabilities, and URL endpoints.
- **Task Lifecycle**:
  - New tasks emit a `Task` object in state `TASK_STATE_SUBMITTED`.
  - Ongoing progress updates send `TaskStatus` with state `TASK_STATE_WORKING`.
  - When decisions are required, status transitions to `TASK_STATE_INPUT_REQUIRED`.
  - On acceptance/success, an artifact is added and status transitions to `TASK_STATE_COMPLETED`.
  - When attendance is rejected, status transitions to `TASK_STATE_REJECTED`.
  - Unhandled exceptions transition to `TASK_STATE_FAILED`.
- **Client Side (`a2a_client.py`)**: Uses `A2ACardResolver` to locate the agent card, creates an `A2AClient`, and consumes streaming events (`send_message`). It supports passing `task_id` and `context_id` to resume paused conversations.

### 4.2 Company Agent (`agents/company_agent.py`, `agents/company_tools.py`)
- Acts as the organizer and A2A client.
- **Tools exposed to LLM**:
  - `list_meetings`: Lists all meetings with status of confirmed, pending, and declined invitees.
  - `get_meeting`: Returns full details for a meeting by ID.
  - `create_meeting`: Validates start/end times (ensuring end > start), normalizes datetimes to `Asia/Kolkata`, performs case-insensitive deduplication, persists to `data/meetings.json`, and sends A2A invites.
  - `send_invite`: Dispatches an invite to an employee agent over A2A.
  - `add_invitee`: Dynamically adds an attendee to an existing meeting and sends an invite.
  - `move_event`: Initiates a two-phase reschedule protocol:
    1. Preflight check (`check_move`) across all invitees' agents.
    2. If conflicts are detected, prompts the organizer or user (`ask_user`).
    3. If approved, commits changes (`commit_move`) across all invitees' calendars and updates `data/meetings.json`.
  - `remove_people`: Asks selected employees' agents to remove the event via A2A `remove_meeting` operation and removes confirmed invitees from the meeting.
  - `remove_meeting` / `cancel_meeting`: Orchestrates full cancellation by requesting all invitees to delete the event from their calendars. Deletes the meeting record only when all agents confirm.
  - `reply_to_employee`: Resumes an `INPUT_REQUIRED` task by forwarding the reply to the specific employee agent.
  - `ask_user`: Pauses to request decision/input from the human operator.
- **Persistence & Concurrency**:
  - Uses `MEETINGS_LOCK` (re-entrant lock) for thread-safe access.
  - Writes meetings atomically using temporary files (`tempfile.NamedTemporaryFile` + `os.replace` + `os.fsync`) to prevent corrupted records.
  - Tracks ongoing tasks in `sessions: dict[tuple[str, str], dict]` keyed by `(meeting_id, employee)`.

### 4.3 Employee Agent (`agents/employee_agent.py`, `agents/employee_tools.py`)
- Run as independent server instances: `python employee_agent.py <employee_name> <port>`.
  - Priya on port `9101`
  - Arjun on port `9102`
  - Meera on port `9103`
- **Execution Model (`EmployeeExecutor`)**:
  - Reconstructs conversation history from `context.current_task.history`.
  - Detects structured A2A operations (`check_move`, `commit_move`, `remove_meeting`).
  - For standard meeting invites, runs the LLM tool loop with:
    - `check_conflicts(start, end)`: Queries the calendar for strict interval overlaps (`start < event_end` and `event_start < end`).
    - `create_event(title, start, end, location, meeting_id)`: Books the meeting idempotently by `meeting_id`.
    - `ask_organizer(question)`: Emits `INPUT_REQUIRED` with a clarifying question.
    - `decline_meeting(reason)`: Rejects the invitation with a stated reason.
  - Seed state is tailored to demonstrate all branches:
    - **Arjun**: Free -> automatically completes/books.
    - **Meera**: On leave -> automatically rejects.
    - **Priya**: Has client call -> asks question (`INPUT_REQUIRED`), answered on profile page or CLI.

### 4.4 Calendar Backends (`agents/calendar_backend.py`)
- Dual-mode backend configured via `CALENDAR_BACKEND` environment variable (`fake` or `google`).
- **Timezone**: All datetimes are handled in `Asia/Kolkata` (`ZoneInfo("Asia/Kolkata")`).
- **`FakeCalendar`**:
  - Reads and writes to `data/calendars/<employee>.json`.
  - Generates IDs `evt-1`, `evt-2`, etc.
  - Idempotent creation: matches on `meeting_id` or identical title + start + end.
- **`GoogleCalendar`**:
  - Interacts with Google Calendar API v3 using `google-api-python-client`.
  - Maps employee names to specific Google Calendar IDs.
  - Stores `meeting_id` in Google Calendar event `extendedProperties.private.meeting_id`.
  - Shared OAuth token stored in `data/google_calendar_token.json` with interactive authorization handled by employee agents.

### 4.5 Web Dashboard & API (`web_ui.py`, `ui/index.html`)
- Built using **Starlette** and **Uvicorn** on port `8080`.
- **Pages**:
  - `/`: Main dashboard featuring a chat-first assistant, sidebar schedule, meeting filters, and status indicators.
  - `/calendars`: Overview of all employee calendars.
  - `/employees/{employee}`: Employee profile showing their individual schedule and pending `INPUT_REQUIRED` invitation questions.
- **API Endpoints**:
  - `GET /api/state`: Full system state (meetings, employee online/offline statuses, calendar events, active employee requests).
  - `POST /api/chat`: Chat interaction with the Company Agent. Enforces multi-turn memory via `conversation_id` and intercepts `ask_user` prompts.
  - `POST /api/meetings`: Direct meeting creation.
  - `POST /api/meetings/{id}/invitees`: Add an invitee.
  - `POST /api/meetings/{id}/invitees/{employee}/reply`: Employee submits response to a pending invitation question.
  - `POST /api/meetings/{id}/remove-people`: Remove attendees.
  - `POST /api/meetings/{id}/remove`: Cancel/remove a meeting.
  - `POST /api/meetings/{id}/move`: Asynchronous meeting reschedule returning a `job_id`.
  - `GET /api/moves/{job_id}`: Polls move status (`running`, `awaiting_confirmation`, `complete`).
  - `POST /api/moves/{job_id}/decision`: Confirms or cancels move when conflict arises.

---

## 5. Employee Profiles, Authentication & Data Isolation

### 5.1 Account Credentials
| Username | Role | Default Password | Permissions |
|---|---|---|---|
| `organizer` | Organizer | `admin123` | Full company-wide meeting view, create/move/cancel meetings, view all calendars & live company feed. |
| `priya` | Employee | `priya123` | Personal employee dashboard: Priya's calendar, invited meetings, and personal pending decisions only. |
| `arjun` | Employee | `arjun123` | Personal employee dashboard: Arjun's calendar, invited meetings, and personal pending decisions only. |
| `meera` | Employee | `meera123` | Personal employee dashboard: Meera's calendar, invited meetings, and personal pending decisions only. |

### 5.2 Authentication & Security
- **Web UI & API**: Authentication via `POST /api/auth/login`. Returns a bearer session token (`gather-token`) stored in `localStorage` and optional HTTP-only cookie.
- **Route-level Data Isolation**:
  - `GET /api/state`: When authenticated as an employee, `meetings`, `calendars`, `employee_requests`, and `pending_decisions` are automatically filtered to only include records where the employee is an invitee.
  - `GET /api/employees/{id}/state`: Strictly enforced—an employee attempting to view another employee's dashboard receives `403 Forbidden`.
  - `POST /api/decisions/{id}/respond`: Strictly enforced—an employee attempting to answer another employee's pending decision receives `403 Forbidden`.
- **Inter-Agent A2A Security**:
  - All A2A JSON-RPC endpoints on Employee Agents (`:9101`, `:9102`, `:9103`) are protected by `AgentAuthMiddleware` requiring `Authorization: Bearer <AGENT_AUTH_TOKEN>`.
  - Handled transparently by `agents/a2a_client.py` using `ClientConfig(httpx_client=...)`.

---

## 6. Conflict Flow & Structured Decision Cycle

```
[Company Agent]
       |
       | A2A send_message (invite/move)
       v
[Employee Agent]
       | Checks calendar: Unresolvable conflict detected!
       | Names conflicting events and intervals.
       | Sets Task state -> INPUT_REQUIRED.
       v
[Durable Storage: pending_decisions.json]
       | Decision record created (status: PENDING)
       | Live Audit Event logged: "Priya's agent found a conflict and is waiting on Priya."
       v
[Employee Dashboard]
       | Displays Conflict Card with clear question
       | 3 Structured Choices:
       |   1. ✓ Approve reschedule (+ optional note)
       |   2. ✕ Decline (can't reschedule, it's important) (+ optional note)
       |   3. ⏱ Suggest another time (+ slot/time input + optional note)
       v
[Employee Submits Response]
       | POST /api/decisions/{id}/respond (or /reply)
       v
[Company Agent Resumes SAME A2A Task]
       | Re-invokes Employee Agent with same task_id & context_id
       | Employee Agent acts (books, moves, or declines)
       v
[Task Ends in COMPLETED or REJECTED]
       | If Declined -> Company Agent triggers Autonomous Agentic Fallback:
       |   -> Calls `find_alternative_times` to search common free slots
       |   -> If none, calls `escalate_to_organizer` (keep time & drop person, make optional, etc.)
       v
[Live Company Feed Updates]
       | "Priya declined. Company agent is looking for alternative slots."
       | "No alternatives found. Escalating to meeting organizer."
```

---

## 7. Storage Architecture & Persistent Data Models

Located in `data/` (or configurable via `DATA_DIR`):
- `data/users.json`: User accounts, display names, roles, and SHA-256 password hashes.
- `data/agent_registry.db`: SQLite database storing dynamic agent registry records (id, name, url, role, skills, status, timestamps) with synchronized `agent_registry.json` export.
- `data/tasks.json`: Durable A2A task registry tracking active and completed agent tasks (`task_id`, `context_id`, state).
- `data/pending_decisions.json`: Pending employee conflict questions with expiration timestamps.
- `data/audit_log.json`: Append-only audit stream powering the Live Company Feed with actors, event types, messages, and timestamps.
- `data/meetings.json`: Central company meetings store.
- `data/calendars/*.json`: Local calendars for `priya`, `arjun`, `meera`.

---

## 8. REST & Action API Endpoints

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/registry/agents` | GET | None | Lists all registered agents from the Agent Registry with status, skills, and endpoints. |
| `/api/registry/agents` | POST | None | Dynamically registers or updates an agent (`{id, name, url, role, skills, metadata}`). |
| `/api/registry/agents/{id}` | GET | None | Retrieves registered details for a specific agent. |
| `/api/registry/agents/{id}` | DELETE | None | Unregisters an agent from the registry directory. |
| `/api/registry/agents/{id}/ping` | POST | None | Probes the A2A endpoint connectivity (`get_agent_card`) and updates status. |
| `/api/auth/login` | POST | None | Login with `{username, password}` &rarr; returns `{user, token}`. |
| `/api/auth/logout` | POST | Bearer | Revokes current session token. |
| `/api/auth/me` | GET | Bearer | Returns currently authenticated user identity and role. |
| `/api/feed` | GET | None | Retrieves recent audit events for the Live Company Feed (`?limit=50`). |
| `/api/decisions` | GET | Bearer | Retrieves pending employee conflict questions (`?employee=...`). |
| `/api/decisions/{id}/respond` | POST | Bearer | Submits structured employee decision (`approve_reschedule`, `decline`, `suggest_time`). |
| `/api/employees/{id}/state` | GET | Bearer | Returns isolated profile state for an employee (with 403 authorization guard). |
| `/api/meetings/{id}/invitees/{emp}/reply`| POST | Bearer | Directly replies to an employee agent's open `INPUT_REQUIRED` prompt. |

---

## 9. Environment & Operational Modes

| Variable | Values / Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | string | API key for Groq LLM inference. |
| `LLM_MODE` | `groq` (default) / `fake` | When set to `fake`, uses `fake_llm.py` rules (no API key needed, ideal for tests). |
| `CALENDAR_BACKEND` | `fake` (default) / `google` | Swaps between JSON files in `data/calendars/` and Google Calendar API. |
| `DATA_DIR` | `data/` (default) | Directory path for meetings, tasks, and audit logs (enables test isolation). |
| `AGENT_AUTH_TOKEN` | `agent-secret-key-a2a` | Shared Bearer token for inter-agent A2A JSON-RPC security. |
| `DECISION_TIMEOUT_SECONDS` | `300` (5 minutes) | Expiration window before pending employee decisions escalate. |
| `SHOW_TOOLS` | `1` (default) / `0` | Controls stdout logging of LLM tool invocations. |

---

## 10. How to Run & Verify

### Starting the System
1. **Activate Virtual Environment**:
   ```bash
   .venv\Scripts\activate   # Windows
   ```
2. **Start Employee Agents** (each in a separate terminal):
   ```bash
   python agents/employee_agent.py priya 9101
   python agents/employee_agent.py arjun 9102
   python agents/employee_agent.py meera 9103
   ```
3. **Start the Web Dashboard**:
   ```bash
   python web_ui.py
   ```
   Open `http://127.0.0.1:8080` in your browser. Click **Switch** in the sidebar to log in as **Organizer**, **Priya**, **Arjun**, or **Meera**.

### Running the Complete Test Suite
- **Full Automated Test Suite (83 tests)**:
  ```bash
  .venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py"
  ```
- **End-to-End Offline Plumbing & Conflict Flow Integration Test**:
  ```powershell
  $env:CALENDAR_BACKEND="fake"; $env:LLM_MODE="fake"; .venv\Scripts\python.exe test_flow.py
  ```

