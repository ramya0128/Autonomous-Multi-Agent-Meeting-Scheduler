"""Local web dashboard for the A2A meeting scheduler demo."""
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
import uvicorn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "agents"))
import agents.company_agent as company
import agents.storage as storage
import agents.agent_registry as agent_registry
from agents.a2a_client import get_agent_card
from agents.calendar_backend import DATA, get_calendar

INDEX = ROOT / "ui" / "index.html"


class EmployeeNamesMapping(dict):
    def __getitem__(self, key):
        names = agent_registry.get_employee_names()
        if key in names:
            return names[key]
        return super().get(key, str(key).title())

    def get(self, key, default=None):
        names = agent_registry.get_employee_names()
        if key in names:
            return names[key]
        return super().get(key, default if default is not None else str(key).title())

    def __contains__(self, key):
        return key in agent_registry.get_employee_names() or super().__contains__(key)

    def keys(self):
        combined = dict(agent_registry.get_employee_names())
        combined.update(super().copy())
        return combined.keys()

    def items(self):
        combined = dict(agent_registry.get_employee_names())
        combined.update(super().copy())
        return combined.items()

    def values(self):
        combined = dict(agent_registry.get_employee_names())
        combined.update(super().copy())
        return combined.values()


EMPLOYEE_NAMES = EmployeeNamesMapping({
    "priya": "Priya Sharma",
    "arjun": "Arjun Mehta",
    "meera": "Meera Iyer",
})


def _get_request_user(request: Request) -> dict | None:
    auth = request.headers.get("authorization")
    token = None
    if auth and auth.startswith("Bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.query_params.get("token") or request.cookies.get("session_token")
    return storage.get_user_session(token) if token else None


class MoveJob:
    def __init__(self):
        now = time.monotonic()
        self.state = "running"
        self.prompt = None
        self.answer = None
        self.result = None
        self.error = None
        self.answer_ready = threading.Event()
        self.lock = threading.Lock()
        self.created_at = now
        self.updated_at = now


MOVE_JOBS: dict[str, MoveJob] = {}
MOVE_JOBS_LOCK = threading.Lock()
MOVE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="meeting-move")
CHAT_SESSIONS: dict[str, dict] = {}
CHAT_SESSIONS_LOCK = threading.Lock()


def _json_result(result):
    if isinstance(result, str) and result.startswith("ERROR:"):
        return JSONResponse({"error": result.removeprefix("ERROR:").strip()}, status_code=400)
    try:
        return json.loads(result) if isinstance(result, str) else result
    except json.JSONDecodeError:
        return {"result": result}


def _input_required_prompt(outcome):
    if not isinstance(outcome, str):
        return None
    match = re.search(
        r"(?:TASK_STATE_)?INPUT_REQUIRED\b\s*:?\s*(.*)",
        outcome,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    prompt = match.group(1).strip()
    return re.sub(
        r"\s+\(answer with reply_to_employee.*\)$",
        "",
        prompt,
        flags=re.IGNORECASE | re.DOTALL,
    ) or outcome


def _meeting(meeting_id):
    return company.company_tools._meeting_for_id(meeting_id)


def _purge_expired_state():
    now = time.monotonic()
    with MOVE_JOBS_LOCK:
        for job_id, job in list(MOVE_JOBS.items()):
            with job.lock:
                if job.state == "complete" and now - job.updated_at > 600:
                    del MOVE_JOBS[job_id]
    with CHAT_SESSIONS_LOCK:
        for conversation_id, session in list(CHAT_SESSIONS.items()):
            if now - session.get("updated_at", 0) > 7200:
                del CHAT_SESSIONS[conversation_id]
    try:
        storage.check_and_expire_decision_timeouts()
    except Exception:
        pass


async def read_json_object(request: Request):
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError):
        return None, JSONResponse({"error": "Request body must be valid JSON"}, status_code=400)
    if not isinstance(data, dict):
        return None, JSONResponse(
            {"error": "The chat request must be a JSON object"}, status_code=400)
    return data, None


async def _employee_statuses():
    async def check(name, url):
        try:
            await get_agent_card(url)
            status = "online"
        except Exception:
            status = "offline"
        agent_registry.update_agent_status(name, status)
        agent_info = agent_registry.get_agent(name) or {}
        return {
            "id": name,
            "name": EMPLOYEE_NAMES.get(name, name.title()),
            "status": status,
            "url": url,
            "role": agent_info.get("role", "employee"),
            "skills": agent_info.get("skills", []),
            "registered_at": agent_info.get("registered_at"),
            "metadata": agent_info.get("metadata", {}),
        }

    return await asyncio.gather(*(
        check(name, url)
        for name, url in company.company_tools.EMPLOYEES.items()
    ))


async def index(_request):
    return FileResponse(INDEX)


async def employee_profile(request: Request):
    employee = request.path_params["employee"].lower()
    if employee not in company.company_tools.EMPLOYEES:
        return JSONResponse({"error": f"Unknown employee '{employee}'"}, status_code=404)
    user = _get_request_user(request)
    if user and user.get("role") == "employee" and user.get("username") != employee:
        return JSONResponse(
            {"error": f"Forbidden: You can only view your own dashboard ({user['username']})"},
            status_code=403,
        )
    return FileResponse(INDEX)


async def _load_calendar_events(name: str):
    if os.getenv("CALENDAR_BACKEND", "fake").strip().lower() == "google":
        try:
            return await run_in_threadpool(lambda: get_calendar(name).list_events()), None
        except Exception as error:
            return [], f"{type(error).__name__}: {error}"
    calendar_path = DATA / "calendars" / f"{name}.json"
    return (
        json.loads(calendar_path.read_text(encoding="utf-8"))
        if calendar_path.exists() else []
    ), None


async def get_state(request: Request):
    _purge_expired_state()
    user = _get_request_user(request)

    # When authenticated as an employee, isolate data to that employee only
    is_employee = user and user.get("role") == "employee"
    allowed_employees = [user["username"]] if is_employee else list(company.company_tools.EMPLOYEES.keys())

    calendars = {}
    calendar_errors = {}
    for name in allowed_employees:
        calendars[name], error = await _load_calendar_events(name)
        if error:
            calendar_errors[name] = error

    statuses = await _employee_statuses()
    if is_employee:
        statuses = [s for s in statuses if s["id"] == user["username"]]

    with company.company_tools.MEETINGS_LOCK:
        meetings = [dict(meeting) for meeting in company.company_tools.MEETINGS]

    if is_employee:
        emp = user["username"]
        meetings = [
            m for m in meetings
            if emp in m.get("invitees", [])
            or emp in m.get("pending_invitees", [])
            or emp in m.get("requested_invitees", [])
            or emp in m.get("declined_invitees", [])
        ]

    employee_requests = []
    for (meeting_id, employee), session in list(company.company_tools.sessions.items()):
        if (
            employee not in allowed_employees
            or session.get("state") != "INPUT_REQUIRED"
            or session.get("kind") != "invite"
        ):
            continue
        prompt = _input_required_prompt(session.get("outcome"))
        if not prompt:
            continue
        meeting = _meeting(meeting_id)
        if meeting is None:
            continue
        employee_requests.append({
            "meeting_id": meeting_id,
            "employee": employee,
            "prompt": prompt,
            "meeting_title": meeting.get("title", meeting_id),
            "start": meeting.get("start"),
            "end": meeting.get("end"),
            "kind": session.get("kind", "operation"),
        })

    feed = storage.get_audit_log(limit=50)
    pending_decisions = storage.get_pending_decisions(
        employee=user["username"] if is_employee else None
    )

    return JSONResponse({
        "meetings": meetings,
        "employees": statuses,
        "calendars": calendars,
        "calendar_errors": calendar_errors,
        "employee_requests": employee_requests,
        "pending_decisions": pending_decisions,
        "feed": feed,
        "user": user,
        "calendar_backend": os.getenv("CALENDAR_BACKEND", "fake").strip().lower(),
        "model": company.MODEL,
    })


async def chat(request: Request):
    _purge_expired_state()
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response

    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return JSONResponse({"error": "A non-empty message is required"}, status_code=400)
    conversation_id = data.get("conversation_id")
    if conversation_id is not None and (
        not isinstance(conversation_id, str) or not conversation_id.strip()
    ):
        return JSONResponse({"error": "conversation_id must be a non-empty string"}, status_code=400)
    conversation_id = conversation_id or str(uuid.uuid4())

    with CHAT_SESSIONS_LOCK:
        conversation = CHAT_SESSIONS.setdefault(
            conversation_id,
            {"messages": [], "lock": asyncio.Lock(), "updated_at": time.monotonic()},
        )
        conversation["updated_at"] = time.monotonic()

    def run_chat_turn():
        prompt = []
        messages = conversation["messages"]
        messages.append({"role": "user", "content": message.strip()})
        implementations = dict(company.IMPLS)

        def ask_user(question):
            prompt.append(question)
            return "Waiting for the user's next chat message before taking action."

        implementations["ask_user"] = ask_user
        today = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="minutes")
        system = (
            f"{company.SYSTEM}\n\n"
            f"Current date and time in Asia/Kolkata: {today}. "
            "Resolve relative dates from this timestamp. If the requested date, time, "
            "duration, or time zone is ambiguous, ask the user instead of guessing. "
            "When ask_user is called, stop and wait for the user's next chat message; "
            "that message answers the pending question. Do not repeat a completed tool "
            "action when continuing after an answer."
        )
        try:
            answer = company.run_tool_loop(
                system,
                messages,
                company.TOOLS,
                implementations,
                stop_when=lambda: bool(prompt),
                max_steps=20,
                persist_messages=True,
            )
        except Exception as error:
            err_type = type(error).__name__
            err_str = str(error)
            if "Connection" in err_type or "Timeout" in err_type or "Connection" in err_str:
                error_msg = (
                    "Could not connect to the cloud AI service (Groq). "
                    "This usually happens due to a momentary internet or cloud provider blip. "
                    "Please try your message again."
                )
            else:
                error_msg = f"{err_type}: {error}"
            return {
                "error": error_msg,
                "status_code": 502,
            }
        return {
            "assistant": prompt[-1] if prompt else answer,
            "needs_response": bool(prompt),
        }

    async with conversation["lock"]:
        result = await run_in_threadpool(run_chat_turn)
        conversation["updated_at"] = time.monotonic()
    if "error" in result:
        return JSONResponse(
            {"error": result["error"], "conversation_id": conversation_id},
            status_code=result["status_code"],
        )
    return JSONResponse({
        "conversation_id": conversation_id,
        "assistant": result["assistant"],
        "needs_response": result["needs_response"],
    })


async def create_meeting(request: Request):
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    try:
        result = await run_in_threadpool(
            company.create_meeting,
            data.get("title", ""),
            data.get("start", ""),
            data.get("end", ""),
            data.get("invitees", []),
            data.get("location", ""),
            data.get("notes", ""),
        )
        response = _json_result(result)
        if isinstance(response, JSONResponse):
            return response
        invite_results = response.get("invite_results", {})
        response["invite_prompts"] = {
            employee: prompt
            for employee, outcome in invite_results.items()
            if (prompt := _input_required_prompt(outcome)) is not None
        }
        return JSONResponse(response)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


async def add_invitee(request: Request):
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    try:
        result = await run_in_threadpool(
            company.add_invitee, data.get("employee", ""), request.path_params["meeting_id"])
        response = _json_result(result)
        if isinstance(response, JSONResponse):
            return response
        outcome = response.get("result", "")
        return JSONResponse({
            **response,
            "invite_prompt": _input_required_prompt(outcome),
        })
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


async def reply_to_invitee(request: Request):
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    meeting_id = request.path_params["meeting_id"]
    employee = request.path_params["employee"].lower()
    meeting = _meeting(meeting_id)
    if meeting is None:
        return JSONResponse({"error": f"No meeting with id '{meeting_id}'"}, status_code=404)
    if employee not in company.company_tools.EMPLOYEES:
        return JSONResponse({"error": f"Unknown employee '{employee}'"}, status_code=400)
    roster = set(
        meeting.get("invitees", [])
        + meeting.get("pending_invitees", [])
        + meeting.get("requested_invitees", [])
        + meeting.get("declined_invitees", [])
    )
    if employee not in roster:
        return JSONResponse({"error": f"{employee} is not invited to this meeting"}, status_code=400)
    try:
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            return JSONResponse({"error": "A non-empty response is required"}, status_code=400)

        # Resolve any matching pending decision in storage
        for d in storage.get_pending_decisions(employee=employee):
            if d.get("meeting_id") == meeting_id:
                action = "decline" if any(w in message.lower() for w in ("decline", "skip", "no", "can't")) else "approve_reschedule"
                storage.resolve_pending_decision(d["id"], action, "", message.strip())

        result = await run_in_threadpool(
            company.reply_to_employee, employee, message.strip(), meeting_id)
        return JSONResponse({
            "employee": employee,
            "outcome": result,
            "prompt": _input_required_prompt(result),
        })
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


async def auth_login(request: Request):
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    username = data.get("username", "")
    password = data.get("password", "")
    session = storage.authenticate_user(username, password)
    if not session:
        return JSONResponse({"error": "Invalid username or password"}, status_code=401)
    response = JSONResponse({"user": session, "token": session["token"]})
    response.set_cookie("session_token", session["token"], max_age=86400 * 7, httponly=True)
    return response


async def auth_logout(request: Request):
    user = _get_request_user(request)
    if user:
        storage.revoke_session(user["token"])
    response = JSONResponse({"ok": True})
    response.delete_cookie("session_token")
    return response


async def auth_me(request: Request):
    user = _get_request_user(request)
    return JSONResponse({"authenticated": bool(user), "user": user})


async def get_live_feed(request: Request):
    limit = int(request.query_params.get("limit", "50"))
    feed = storage.get_audit_log(limit=limit)
    return JSONResponse({"feed": feed})


async def get_decisions(request: Request):
    user = _get_request_user(request)
    emp = user["username"] if (user and user.get("role") == "employee") else request.query_params.get("employee")
    decisions = storage.get_pending_decisions(employee=emp)
    return JSONResponse({"decisions": decisions})


async def respond_to_decision(request: Request):
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    decision_id = request.path_params["decision_id"]
    decision = storage.get_pending_decision_by_id(decision_id)
    if not decision:
        return JSONResponse({"error": f"No decision with id '{decision_id}'"}, status_code=404)
    if decision.get("status") != "PENDING":
        return JSONResponse({"error": "Decision has already been answered or timed out"}, status_code=409)

    user = _get_request_user(request)
    if user and user.get("role") == "employee" and user.get("username") != decision["employee"]:
        return JSONResponse({"error": "Forbidden: Cannot answer another employee's decision"}, status_code=403)

    action = data.get("action", "")  # approve_reschedule, decline, suggest_time
    suggested_time = data.get("suggested_time", "")
    note = data.get("note", "").strip()

    if action not in ("approve_reschedule", "decline", "suggest_time"):
        return JSONResponse({"error": "Action must be approve_reschedule, decline, or suggest_time"}, status_code=400)

    emp_name = decision["employee"].title()
    if action == "approve_reschedule":
        msg = f"Approve reschedule. {note}".strip()
        feed_msg = f"{emp_name} approved rescheduling. Rescheduling the meeting."
        storage.log_event("DECISION_APPROVED", decision["employee"], feed_msg, meeting_id=decision["meeting_id"], details={"note": note})
    elif action == "decline":
        reason = note or "Client call is important. Find another alternative."
        msg = f"Decline. {reason}".strip()
        feed_msg = f"{emp_name} declined: {reason}. Find another alternative."
        storage.log_event("DECISION_DECLINED", decision["employee"], feed_msg, meeting_id=decision["meeting_id"], details={"reason": reason})
    else:
        msg = f"Suggest another time: {suggested_time}. {note}".strip()
        feed_msg = f"{emp_name} suggested another time: {suggested_time}. Find another alternative."
        storage.log_event("DECISION_SUGGESTED_TIME", decision["employee"], feed_msg, meeting_id=decision["meeting_id"], details={"suggested_time": suggested_time, "note": note})

    storage.resolve_pending_decision(decision_id, action, suggested_time, note)

    result = await run_in_threadpool(
        company.reply_to_employee,
        decision["employee"],
        msg,
        decision["meeting_id"],
    )

    return JSONResponse({
        "status": "COMPLETED" if result.startswith("COMPLETED") else ("REJECTED" if result.startswith("REJECTED") else "UPDATED"),
        "result": result,
        "decision_id": decision_id,
        "employee": decision["employee"],
        "meeting_id": decision["meeting_id"],
    })


async def get_employee_state(request: Request):
    employee = request.path_params["employee"].lower()
    if employee not in company.company_tools.EMPLOYEES:
        return JSONResponse({"error": f"Unknown employee '{employee}'"}, status_code=404)
    user = _get_request_user(request)
    if user and user.get("role") == "employee" and user.get("username") != employee:
        return JSONResponse(
            {"error": f"Forbidden: You can only view your own dashboard ({user['username']})"},
            status_code=403,
        )

    events, error = await _load_calendar_events(employee)
    with company.company_tools.MEETINGS_LOCK:
        meetings = [
            dict(m) for m in company.company_tools.MEETINGS
            if employee in m.get("invitees", [])
            or employee in m.get("pending_invitees", [])
            or employee in m.get("requested_invitees", [])
            or employee in m.get("declined_invitees", [])
        ]
    pending_decisions = storage.get_pending_decisions(employee=employee)
    return JSONResponse({
        "employee": {"id": employee, "name": EMPLOYEE_NAMES.get(employee, employee.title())},
        "calendar": events,
        "calendar_error": error,
        "meetings": meetings,
        "pending_decisions": pending_decisions,
    })


async def remove_people(request: Request):
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    try:
        result = await run_in_threadpool(
            company.remove_people,
            request.path_params["meeting_id"],
            data.get("employees", []),
        )
        response = _json_result(result)
        return response if isinstance(response, JSONResponse) else JSONResponse(response)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


async def remove_meeting(request: Request):
    meeting_id = request.path_params["meeting_id"]
    if not _meeting(meeting_id):
        return JSONResponse({"error": f"No meeting with id '{meeting_id}'"}, status_code=404)
    try:
        result = await run_in_threadpool(
            company.remove_meeting, meeting_id)
        response = _json_result(result)
        return response if isinstance(response, JSONResponse) else JSONResponse(response)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JSONResponse({"error": str(error)}, status_code=400)


def _run_move(job_id, meeting_id, start, end):
    job = MOVE_JOBS[job_id]

    def prompt_user(question):
        with job.lock:
            job.prompt = question
            job.answer = None
            job.state = "awaiting_confirmation"
            job.updated_at = time.monotonic()
            job.answer_ready.clear()
        if not job.answer_ready.wait(timeout=120):
            answer = "no"
        else:
            with job.lock:
                answer = job.answer
        with job.lock:
            job.prompt = None
            job.state = "running"
            job.updated_at = time.monotonic()
        return answer

    try:
        job.result = company.reschedule_meeting(meeting_id, start=start, end=end, ask_user_fn=prompt_user)
    except Exception as error:
        job.error = f"{type(error).__name__}: {error}"
    finally:
        with job.lock:
            job.state = "complete"
            job.updated_at = time.monotonic()


async def start_move(request: Request):
    _purge_expired_state()
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    meeting_id = request.path_params["meeting_id"]
    if not _meeting(meeting_id):
        return JSONResponse({"error": f"No meeting with id '{meeting_id}'"}, status_code=404)
    job_id = str(uuid.uuid4())
    with MOVE_JOBS_LOCK:
        MOVE_JOBS[job_id] = MoveJob()
    MOVE_EXECUTOR.submit(
        _run_move, job_id, meeting_id, data.get("start", ""), data.get("end", ""))
    return JSONResponse({"job_id": job_id}, status_code=202)


async def get_move_job(request: Request):
    _purge_expired_state()
    job = MOVE_JOBS.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "Move operation not found"}, status_code=404)
    with job.lock:
        if job.result is None:
            result = None
        elif isinstance(job.result, str) and job.result.startswith("ERROR:"):
            result = {"status": "ERROR", "error": job.result.removeprefix("ERROR:").strip()}
        else:
            try:
                result = json.loads(job.result) if isinstance(job.result, str) else job.result
            except json.JSONDecodeError:
                result = {"result": job.result}
        return JSONResponse({
            "state": job.state,
            "prompt": job.prompt,
            "result": result,
            "error": job.error,
        })


async def answer_move_prompt(request: Request):
    _purge_expired_state()
    job = MOVE_JOBS.get(request.path_params["job_id"])
    if job is None:
        return JSONResponse({"error": "Move operation not found"}, status_code=404)
    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    answer = data.get("answer")
    if answer not in ("yes", "no"):
        return JSONResponse({"error": "Answer must be yes or no"}, status_code=400)
    with job.lock:
        if job.state != "awaiting_confirmation":
            return JSONResponse({"error": "This move is not awaiting confirmation"}, status_code=409)
        job.answer = answer
        job.answer_ready.set()
        job.updated_at = time.monotonic()
    return JSONResponse({"accepted": True})


# ============================================================================
# Agent Registry API Endpoints
# ============================================================================
ACTIVE_AGENT_PROCESSES = {}
ACTIVE_AGENT_PROCESSES_LOCK = threading.Lock()


def _get_next_available_agent_port() -> int:
    agents = agent_registry.list_agents()
    used_ports = set()
    for a in agents:
        match = re.search(r":(\d+)", a.get("url", ""))
        if match:
            used_ports.add(int(match.group(1)))
    port = 9101
    while port in used_ports:
        port += 1
    return port


def _spawn_employee_agent_process(agent_id: str, port: int):
    root = Path(__file__).resolve().parent
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable
    cmd = [python_bin, str(root / "agents" / "employee_agent.py"), agent_id, str(port)]
    with ACTIVE_AGENT_PROCESSES_LOCK:
        if agent_id in ACTIVE_AGENT_PROCESSES:
            old_proc = ACTIVE_AGENT_PROCESSES[agent_id]
            if old_proc.poll() is None:
                return old_proc
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(root / "agents"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ACTIVE_AGENT_PROCESSES[agent_id] = proc
            return proc
        except Exception as e:
            print(f"[web_ui] Could not auto-spawn employee agent {agent_id}: {e}")
            return None


async def get_registered_agents_api(_request: Request):
    agents = agent_registry.list_agents()
    return JSONResponse({"agents": agents})


async def register_agent_api(request: Request):
    user = _get_request_user(request)
    if user and user.get("role") == "employee":
        return JSONResponse(
            {"error": "Forbidden: Only an administrator/organizer can register new agents."},
            status_code=403,
        )

    data, error_response = await read_json_object(request)
    if error_response:
        return error_response
    agent_id = data.get("id") or data.get("agent_id")
    url = data.get("url") or data.get("endpoint")
    name = data.get("name") or data.get("display_name")
    role = data.get("role", "employee")
    skills = data.get("skills")
    metadata = data.get("metadata") or {}
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]

    # If URL is omitted, automatically assign the next free port
    port = None
    if not url or not str(url).strip():
        port = _get_next_available_agent_port()
        url = f"http://127.0.0.1:{port}"
    else:
        match = re.search(r":(\d+)", url)
        if match:
            port = int(match.group(1))

    # Auto-provision calendar for this employee
    try:
        import agents.calendar_backend as cb
        cal_res = cb.create_calendar_for_employee(agent_id, name)
        if cal_res.get("backend") == "google" and cal_res.get("calendar_id"):
            metadata["google_calendar_id"] = cal_res["calendar_id"]
    except Exception as cal_err:
        print(f"[web_ui] Note: calendar auto-create warning for '{agent_id}': {cal_err}")

    try:
        agent = agent_registry.register_agent(
            agent_id=agent_id,
            url=url,
            name=name,
            role=role,
            skills=skills,
            metadata=metadata,
        )

        # Auto-spawn agent process in background if role is employee
        if role == "employee" and port:
            _spawn_employee_agent_process(agent_id, port)

        return JSONResponse({
            "success": True,
            "agent": agent,
            "message": f"Agent '{agent['name']}' registered and started at {url}.",
        })
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Failed to register agent: {e}"}, status_code=500)


async def get_agent_api(request: Request):
    agent_id = request.path_params["agent_id"]
    agent = agent_registry.get_agent(agent_id)
    if not agent:
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)
    return JSONResponse({"agent": agent})


async def unregister_agent_api(request: Request):
    user = _get_request_user(request)
    if user and user.get("role") == "employee":
        return JSONResponse(
            {"error": "Forbidden: Only an administrator/organizer can unregister agents."},
            status_code=403,
        )

    agent_id = request.path_params["agent_id"]
    if not agent_registry.get_agent(agent_id):
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)

    # Terminate spawned process if active
    with ACTIVE_AGENT_PROCESSES_LOCK:
        proc = ACTIVE_AGENT_PROCESSES.pop(agent_id, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    success = agent_registry.unregister_agent(agent_id)
    return JSONResponse({"success": success, "message": f"Agent '{agent_id}' unregistered."})


async def ping_agent_api(request: Request):
    agent_id = request.path_params["agent_id"]
    agent = agent_registry.get_agent(agent_id)
    if not agent:
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)
    try:
        card = await get_agent_card(agent["url"])
        status = "online"
        card_name = getattr(card, "name", str(card))
    except Exception as e:
        status = "offline"
        card_name = str(e)
    agent_registry.update_agent_status(agent_id, status)
    return JSONResponse({"id": agent_id, "status": status, "detail": card_name})


routes = [
    Route("/", index),
    Route("/calendars", index),
    Route("/registry", index),
    Route("/employees/{employee:str}", employee_profile),
    Route("/api/state", get_state),
    Route("/api/registry/agents", get_registered_agents_api, methods=["GET"]),
    Route("/api/registry/agents", register_agent_api, methods=["POST"]),
    Route("/api/registry/agents/{agent_id:str}", get_agent_api, methods=["GET"]),
    Route("/api/registry/agents/{agent_id:str}", unregister_agent_api, methods=["DELETE"]),
    Route("/api/registry/agents/{agent_id:str}/ping", ping_agent_api, methods=["POST"]),
    Route("/api/auth/login", auth_login, methods=["POST"]),
    Route("/api/auth/logout", auth_logout, methods=["POST"]),
    Route("/api/auth/me", auth_me, methods=["GET"]),
    Route("/api/feed", get_live_feed, methods=["GET"]),
    Route("/api/decisions", get_decisions, methods=["GET"]),
    Route("/api/decisions/{decision_id:str}/respond", respond_to_decision, methods=["POST"]),
    Route("/api/employees/{employee:str}/state", get_employee_state, methods=["GET"]),
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/meetings", create_meeting, methods=["POST"]),
    Route("/api/meetings/{meeting_id:str}/invitees", add_invitee, methods=["POST"]),
    Route(
        "/api/meetings/{meeting_id:str}/invitees/{employee:str}/reply",
        reply_to_invitee,
        methods=["POST"],
    ),
    Route("/api/meetings/{meeting_id:str}/remove-people", remove_people, methods=["POST"]),
    Route("/api/meetings/{meeting_id:str}/remove", remove_meeting, methods=["POST"]),
    Route("/api/meetings/{meeting_id:str}/cancel", remove_meeting, methods=["POST"]),
    Route("/api/meetings/{meeting_id:str}/move", start_move, methods=["POST"]),
    Route("/api/meetings/{meeting_id:str}/reschedule", start_move, methods=["POST"]),
    Route("/api/moves/{job_id:str}", get_move_job),
    Route("/api/moves/{job_id:str}/decision", answer_move_prompt, methods=["POST"]),
]
async def purge_state_on_request(request, call_next):
    try:
        _purge_expired_state()
    except Exception:
        pass
    return await call_next(request)


app = Starlette(debug=False, routes=routes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BaseHTTPMiddleware, dispatch=purge_state_on_request)


if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("  GATHER A2A MEETING SCHEDULER DASHBOARD")
    print("  URL: http://127.0.0.1:8080")
    print("  Keep this terminal window open while using the application.")
    print("  To stop the server, press Ctrl+C.")
    print("=" * 65 + "\n")
    while True:
        try:
            uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
            print("\n[Gather Server] Uvicorn exited normally.")
            break
        except KeyboardInterrupt:
            print("\n[Gather Server] Stopped by user (Ctrl+C). Exiting.")
            break
        except Exception as error:
            print(f"\n[Gather Server] Server error: {error}. Auto-restarting in 2 seconds...")
            time.sleep(2)

