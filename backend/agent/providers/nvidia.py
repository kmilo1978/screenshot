"""NVIDIA NIM provider using the OpenAI-compatible Chat Completions API.

NVIDIA NIM endpoints at https://integrate.api.nvidia.com/v1 support
the OpenAI Chat Completions format, but NOT the newer Responses API.
This provider uses AsyncOpenAI with a custom base_url pointed at NIM.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from agent.providers.base import (
    EventSink,
    ExecutedToolCall,
    ProviderSession,
    ProviderTurn,
    StreamEvent,
)
from agent.state import ensure_str
from agent.tools import CanonicalToolDefinition, ToolCall, parse_json_arguments
from fs_logging.agent_runs import AgentRunRecorder
from llm import Llm, get_nvidia_api_name

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


def serialize_nvidia_tools(
    tools: List[CanonicalToolDefinition],
) -> List[Dict[str, Any]]:
    """Serialize canonical tools to OpenAI Chat Completions function format."""
    serialized: List[Dict[str, Any]] = []
    for tool in tools:
        serialized.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return serialized


@dataclass
class NvidiaParseState:
    assistant_text: str = ""
    tool_calls: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tool_call_index_to_id: Dict[int, str] = field(default_factory=dict)


@dataclass
class NvidiaProviderSession:
    """NVIDIA NIM provider using OpenAI-compatible Chat Completions API."""

    client: AsyncOpenAI
    model: Llm
    prompt_messages: List[ChatCompletionMessageParam]
    tools: List[Dict[str, Any]]
    recorder: Optional[AgentRunRecorder] = None

    # Conversation history for multi-turn tool use
    _messages: List[Dict[str, Any]] = field(default_factory=list)
    _initialized: bool = False

    def __post_init__(self) -> None:
        # Convert prompt messages to plain dicts for the conversation
        self._messages = []
        for msg in self.prompt_messages:
            converted = self._convert_message(msg)
            if converted:
                self._messages.append(converted)
        self._initialized = True

    def _convert_message(self, message: ChatCompletionMessageParam) -> Dict[str, Any]:
        """Convert a ChatCompletionMessageParam to a plain dict for the API."""
        role = message.get("role", "user")
        content = message.get("content", "")

        if isinstance(content, str):
            return {"role": role, "content": content}

        # Handle multimodal content (images + text)
        if isinstance(content, list):
            parts: List[Dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url", {})
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_url.get("url", ""),
                            },
                        }
                    )
            return {"role": role, "content": parts}

        return {"role": role, "content": str(content) if content else ""}

    async def stream_turn(self, on_event: EventSink) -> ProviderTurn:
        """Stream a single turn from NVIDIA NIM via Chat Completions."""
        model_name = get_nvidia_api_name(self.model)
        state = NvidiaParseState()

        # Build request kwargs
        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": self._messages,
            "stream": True,
            "max_tokens": 16384,
        }

        # Only include tools if we have them
        if self.tools:
            kwargs["tools"] = self.tools
            kwargs["tool_choice"] = "auto"

        stream = await self.client.chat.completions.create(**kwargs)

        async for chunk in stream:  # type: ignore[union-attr]
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Text content
            if delta and delta.content:
                state.assistant_text += delta.content
                await on_event(
                    StreamEvent(type="assistant_delta", text=delta.content)
                )

            # Tool calls
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index

                    # First chunk for this tool call — assign an ID
                    if idx not in state.tool_call_index_to_id:
                        call_id = tc_delta.id or f"call_{uuid.uuid4().hex[:8]}"
                        state.tool_call_index_to_id[idx] = call_id
                        state.tool_calls[call_id] = {
                            "id": call_id,
                            "name": tc_delta.function.name if tc_delta.function else "",
                            "arguments": "",
                        }

                    call_id = state.tool_call_index_to_id[idx]
                    entry = state.tool_calls[call_id]

                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

                    await on_event(
                        StreamEvent(
                            type="tool_call_delta",
                            tool_call_id=call_id,
                            tool_name=entry["name"],
                            tool_arguments=entry["arguments"],
                        )
                    )

        # Build final tool calls
        tool_calls: List[ToolCall] = []
        for entry in state.tool_calls.values():
            args, error = parse_json_arguments(entry["arguments"])
            if error:
                args = {"INVALID_JSON": entry["arguments"]}
            tool_calls.append(
                ToolCall(id=entry["id"], name=entry["name"], arguments=args)
            )

        # Build assistant message for conversation history
        assistant_msg: Dict[str, Any] = {"role": "assistant"}
        if state.assistant_text:
            assistant_msg["content"] = state.assistant_text
        if state.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": entry["id"],
                    "type": "function",
                    "function": {
                        "name": entry["name"],
                        "arguments": entry["arguments"],
                    },
                }
                for entry in state.tool_calls.values()
            ]
        self._messages.append(assistant_msg)

        return ProviderTurn(
            assistant_text=state.assistant_text,
            tool_calls=tool_calls,
            assistant_turn=assistant_msg,
        )

    async def append_tool_results(
        self,
        turn: ProviderTurn,
        executed_tool_calls: list[ExecutedToolCall],
    ) -> None:
        """Append tool results to conversation for the next turn."""
        for executed in executed_tool_calls:
            result_content = json.dumps(executed.result.result, ensure_ascii=False)
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": executed.tool_call.id,
                    "content": result_content,
                }
            )

    def total_cost_usd(self) -> Optional[float]:
        """NVIDIA NIM pricing is not tracked locally."""
        return None

    async def close(self) -> None:
        """No cleanup needed for NVIDIA NIM."""
        pass
