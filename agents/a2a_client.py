"""Client-side A2A helpers: discover an agent via its card, send a message, read the result."""
import uuid

import httpx

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.client.errors import AgentCardResolutionError
from a2a.helpers import get_artifact_text, get_message_text
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState
from storage import AGENT_AUTH_TOKEN


class AgentError(Exception):
    pass


def user_message(text, task_id=None, context_id=None) -> Message:
    return Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()),
                   parts=[Part(text=text)], task_id=task_id, context_id=context_id)


async def get_agent_card(url: str):
    headers = {"Authorization": f"Bearer {AGENT_AUTH_TOKEN}"} if AGENT_AUTH_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=5, headers=headers) as http:
            return await A2ACardResolver(http, url).get_agent_card()
    except (httpx.HTTPError, AgentCardResolutionError) as e:
        raise AgentError(f"Cannot reach agent at {url}: {e!r}") from e


async def connect(url: str):
    card = await get_agent_card(url)
    headers = {"Authorization": f"Bearer {AGENT_AUTH_TOKEN}"} if AGENT_AUTH_TOKEN else {}
    http = httpx.AsyncClient(timeout=30, headers=headers)
    return await create_client(card, client_config=ClientConfig(streaming=True, httpx_client=http))


async def call(client, text, task_id=None, context_id=None, verbose=False):
    """Send one message. Returns (state, text, task_id, context_id).
    text = the artifact (final result) if there is one, else the last status message."""
    state = artifact = note = None
    async for ev in client.send_message(SendMessageRequest(
            message=user_message(text, task_id, context_id))):
        if ev.HasField("task"):
            task_id, context_id = ev.task.id, ev.task.context_id
            state = TaskState.Name(ev.task.status.state)
            for a in ev.task.artifacts:            # non-streaming servers put results here
                artifact = get_artifact_text(a)
        elif ev.HasField("status_update"):
            st = ev.status_update.status
            state = TaskState.Name(st.state)
            note = get_message_text(st.message) if st.HasField("message") else ""
            if verbose:
                print(f"    [{state.replace('TASK_STATE_', '')}] {note}")
        elif ev.HasField("artifact_update"):
            artifact = get_artifact_text(ev.artifact_update.artifact)
    return state, (artifact or note), task_id, context_id
