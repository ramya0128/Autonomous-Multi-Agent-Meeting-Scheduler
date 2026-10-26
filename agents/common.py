"""Shared helpers so each agent file stays focused on its own logic."""
import uvicorn
from starlette.applications import Starlette

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (AgentCapabilities, AgentCard, AgentInterface,
                       AgentSkill, Task, TaskState, TaskStatus)


def make_card(name, description, port, skill: AgentSkill | list[AgentSkill]) -> AgentCard:
    skills_list = skill if isinstance(skill, list) else [skill]
    return AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        supported_interfaces=[AgentInterface(
            protocol_binding="JSONRPC", protocol_version="1.0",
            url=f"http://127.0.0.1:{port}/")],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=skills_list,
    )


async def open_task(context: RequestContext, event_queue: EventQueue) -> None:
    """A task-mode execute() must emit the Task object first on a new task."""
    if context.current_task is None:
        await event_queue.enqueue_event(Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            history=[context.message],
        ))


from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

try:
    from storage import verify_agent_token
except ImportError:
    from agents.storage import verify_agent_token


class AgentAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/.well-known/") or request.url.path == "/favicon.ico":
            return await call_next(request)
        auth = request.headers.get("authorization")
        if not verify_agent_token(auth):
            return JSONResponse({"error": "Unauthorized: invalid agent token"}, status_code=401)
        return await call_next(request)


def serve(card: AgentCard, executor, port: int) -> None:
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    card_routes = list(create_agent_card_routes(card))
    alias_routes = [Route("/.well-known/agent.json", r.endpoint, methods=["GET"]) for r in card_routes]
    app = Starlette(
        routes=[
            *card_routes,
            *alias_routes,
            *create_jsonrpc_routes(handler, rpc_url="/"),
        ],
        middleware=[Middleware(AgentAuthMiddleware)],
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
