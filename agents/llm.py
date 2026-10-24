"""One small tool-calling loop on top of Groq, shared by both agents."""
import json
import os

MODEL = "openai/gpt-oss-120b"


def get_client():
    if os.getenv("LLM_MODE") == "fake":          # offline testing, no API key needed
        from fake_llm import FakeClient
        return FakeClient()
    from groq import Groq
    # reads GROQ_API_KEY from environment, with robust timeout and retries
    return Groq(timeout=45.0, max_retries=3)


def run_tool_loop(system: str, messages: list, tools: list, impls: dict,
                  stop_when=lambda: False, max_steps: int = 8,
                  persist_messages: bool = False) -> str:
    """Ask the model; if it calls tools, run them and ask again. Returns the final text.
    stop_when() is checked after each round of tool calls (used for terminal tools
    like 'ask the organizer' where we must pause instead of letting the model continue)."""
    import time
    client = get_client()
    msgs = [{"role": "system", "content": system}] + messages
    for _ in range(max_steps):
        resp = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=MODEL, messages=msgs, tools=tools, tool_choice="auto", temperature=0)
                break
            except Exception as e:
                err_type = type(e).__name__
                if attempt < 2 and ("Connection" in err_type or "Timeout" in err_type or "RateLimit" in err_type):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        m = resp.choices[0].message
        if not m.tool_calls:
            answer = m.content or ""
            if persist_messages:
                messages.append({"role": "assistant", "content": answer})
            return answer
        assistant_message = {
            "role": "assistant",
            "content": m.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in m.tool_calls
            ],
        }
        msgs.append(assistant_message)
        if persist_messages:
            messages.append(assistant_message)
        for tc in m.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = impls[tc.function.name](**args)
            except Exception as e:                 # bad tool name/args: tell the model
                result = f"ERROR: {type(e).__name__}: {e}"
            if os.getenv("SHOW_TOOLS", "1") == "1":
                print(f"  [tool] {tc.function.name}({tc.function.arguments}) -> {str(result)[:150]}")
            tool_message = {
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.function.name,
                "content": str(result),
            }
            msgs.append(tool_message)
            if persist_messages:
                messages.append(tool_message)
        if stop_when():
            return ""
    raise RuntimeError(f"Model did not finish within {max_steps} steps")
