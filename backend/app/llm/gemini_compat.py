"""Adapt DocuSight's Anthropic-shaped requests to Google's OpenAI-compatible Gemini API."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from openai import OpenAI

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        return str(block.get("text", ""))
    return ""


def _as_openai_messages(messages: list[dict[str, Any]], system: Any = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        sys_text = "\n\n".join(_text(part) for part in system) if isinstance(system, list) else str(system)
        if sys_text.strip():
            out.append({"role": "system", "content": sys_text})

    for message in messages:
        role, content = message.get("role", "user"), message.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            out.append({"role": role, "content": str(content)})
            continue

        text_parts: list[str] = []
        image_parts: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text_parts.append(str(block.get("text", "")))
            elif kind == "image":
                source = block.get("source", {})
                if source.get("type") == "base64" and source.get("data"):
                    mime = source.get("media_type", "image/jpeg")
                    image_parts.append({"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64,{source['data']}"}})
            elif kind == "tool_use":
                tool_calls.append({"id": block.get("id", ""), "type": "function", "function": {
                    "name": block.get("name", ""), "arguments": json.dumps(block.get("input", {}))}})
            elif kind == "tool_result":
                result = block.get("content", "")
                if isinstance(result, list):
                    result = "\n".join(_text(item) for item in result)
                tool_results.append({"role": "tool", "tool_call_id": block.get("tool_use_id", ""),
                                     "content": str(result)})

        if role == "assistant" and tool_calls:
            item: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
            if text_parts:
                item["content"] = "\n".join(text_parts)
            else:
                item["content"] = None
            out.append(item)
        elif role == "user" and tool_results:
            if text_parts or image_parts:
                part: list[dict[str, Any]] = ([{"type": "text", "text": "\n".join(text_parts)}]
                                               if text_parts else []) + image_parts
                out.append({"role": "user", "content": part})
            out.extend(tool_results)
        else:
            parts: list[dict[str, Any]] = ([{"type": "text", "text": "\n".join(text_parts)}]
                                           if text_parts else []) + image_parts
            out.append({"role": role, "content": parts or ""})
    return out


def _tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [{"type": "function", "function": {
        "name": tool["name"], "description": tool.get("description", ""),
        "parameters": tool.get("input_schema", {"type": "object", "properties": {}})}} for tool in tools]


def _request_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    params = dict(kwargs)
    system = params.pop("system", None)
    messages = params.pop("messages", [])
    params["messages"] = _as_openai_messages(messages, system)
    if "tools" in params:
        params["tools"] = _tools(params["tools"])
        if params["tools"] is None:
            params.pop("tools")
    tool_choice = params.get("tool_choice")
    if isinstance(tool_choice, dict):
        params["tool_choice"] = tool_choice.get("type", "auto")

    output = params.pop("output_config", None) or {}
    effort = output.get("effort")
    if effort in {"low", "medium", "high"}:
        params["reasoning_effort"] = effort
    fmt = output.get("format")
    if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
        params["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "extraction_result", "strict": True, "schema": fmt.get("schema", {})}}

    # Anthropic-only controls; the supported Gemini compatibility surface has equivalents
    # for reasoning effort, tool calls, images and structured JSON, but not these controls.
    params.pop("thinking", None)
    params.pop("cache_control", None)
    params.pop("betas", None)
    params.pop("fallbacks", None)
    params.pop("stream", None)
    params["stream"] = True
    return params


def _adapt_message(choice: Any) -> Any:
    source = choice.message
    blocks: list[Any] = []
    if getattr(source, "content", None):
        blocks.append(SimpleNamespace(type="text", text=source.content))
    for tool in getattr(source, "tool_calls", None) or []:
        try:
            args = json.loads(tool.function.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Gemini returned malformed tool input") from exc
        if not isinstance(args, dict):
            raise ValueError("Gemini tool input must be a JSON object")
        blocks.append(SimpleNamespace(type="tool_use", id=tool.id, name=tool.function.name, input=args))
    stop = getattr(choice, "finish_reason", None)
    stop_reason = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
                   "content_filter": "refusal"}.get(stop, stop or "end_turn")
    if getattr(source, "refusal", None):
        blocks.append(SimpleNamespace(type="refusal", refusal=source.refusal))
        stop_reason = "refusal"
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class GeminiMessages:
    def __init__(self, client: OpenAI):
        self.client = client

    def create(self, **kwargs: Any) -> Any:
        params = _request_kwargs(kwargs)
        params["stream"] = False
        response = self.client.chat.completions.create(**params)
        return _adapt_message(response.choices[0])

    def stream(self, **kwargs: Any) -> "GeminiStream":
        return GeminiStream(self.client, _request_kwargs(kwargs))


class GeminiStream:
    def __init__(self, client: OpenAI, params: dict[str, Any]):
        self.client, self.params = client, params
        self.raw: Any = None
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.text = ""
        self.reason = None
        self.complete = False

    def __enter__(self) -> "GeminiStream":
        self.raw = self.client.chat.completions.create(**self.params)
        return self

    def __exit__(self, *_: Any) -> None:
        close = getattr(self.raw, "close", None)
        if close:
            close()

    def __iter__(self):
        if self.complete:
            return
        for chunk in self.raw:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if choice.finish_reason:
                self.reason = choice.finish_reason
            text = getattr(delta, "content", None)
            if text:
                self.text += text
                yield SimpleNamespace(type="text", text=text)
            thought = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if thought:
                yield SimpleNamespace(type="thinking", thinking=thought)
            for call in getattr(delta, "tool_calls", None) or []:
                item = self.tool_calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                if call.id:
                    item["id"] = call.id
                if call.function:
                    item["name"] += call.function.name or ""
                    item["arguments"] += call.function.arguments or ""
        self.complete = True

    def get_final_message(self) -> Any:
        if not self.complete:
            for _ in self:
                pass
        blocks: list[Any] = []
        if self.text:
            blocks.append(SimpleNamespace(type="text", text=self.text))
        for tool in self.tool_calls.values():
            try:
                args = json.loads(tool["arguments"] or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("Gemini returned malformed tool input") from exc
            if not isinstance(args, dict):
                raise ValueError("Gemini tool input must be a JSON object")
            blocks.append(SimpleNamespace(type="tool_use", id=tool["id"], name=tool["name"], input=args))
        stop_reason = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
                       "content_filter": "refusal"}.get(self.reason, self.reason or "end_turn")
        return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def make_client(api_key: str) -> GeminiMessages:
    return GeminiMessages(OpenAI(api_key=api_key, base_url=GEMINI_BASE_URL, max_retries=2))
