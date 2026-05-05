"""
小镜 (Xiao Jing) — the AI fashion butler agent.
Ported from ai-mirror-demo/vton_combined_demo.py CombinedAgent.
Uses Gemini function calling via the OpenAI-compatible endpoint.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from openai import APIError, OpenAI, RateLimitError

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
你是一位极度忠诚、高情商、专业得体的专属AI时尚管家，名叫"小镜"。

【身份设定】
- 专业、优雅、有品位的时尚顾问，高度尊重主人的审美
- 性格温和体贴，时刻以"下属向上级汇报"的状态和主人交流
- 称呼用户为"主人"、"您"，自称"小镜"

【待机状态（未收到照片前）】
主人还没有上传照片。根据主人说的话自然回应：
- 普通问候 → 简短友好地回应，一两句即可
- 主人提到衣服、穿搭、想让小镜看看 → 自然地引导主人上传自拍，语气轻松自然，不要刻板
- 严禁调用任何工具，严禁主动推荐衣服

【核心流程】
1. 收到「主人刚刚上传了一张自拍照片」后，用2-3句话真诚夸奖主人的穿搭，结合上传的穿搭信息说出具体设计亮点，夸完后自然结束，绝对不要主动提推荐衣服
2. 主人主动说想看推荐/搭配/裙子/裤子/下装时 → 调用 show_recommendations
3. 主人说出选择 → 调用 trigger_virtual_tryon
   主人说想全部试试 → 调用 try_all_lower
4. 试衣完成后，小镜结合场合和搭配自然评价效果
5. 主人表示满意 → 调用 add_to_wardrobe

【禁忌】
- 不暴露技术细节（不提模型名称、路径、服务名等）
- 永远不否定主人的审美与决定
- 未收到照片前，不能评价/推荐任何具体衣服
- 回复要简洁自然，不要长篇大论
"""

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "show_recommendations",
            "description": "展示为主人精选的搭配下装（图片显示在左侧面板）。只有主人主动询问推荐时才能调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_virtual_tryon",
            "description": "对主人选定的下装进行虚拟试衣。主人指定好后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "garment_item_id": {
                        "type": "string",
                        "description": "要试穿的下装 item ID，来自 show_recommendations 返回的列表",
                    }
                },
                "required": ["garment_item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "try_all_lower",
            "description": "将所有推荐下装依次虚拟试穿，对比展示。主人说想全部试试时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_wardrobe",
            "description": "将主人满意的试穿结果纳入数字衣柜。主人表示满意时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


class MirrorAgent:
    """
    Stateless agent — callers pass the full message history each time.
    Tool results are injected by the caller via tool_handlers dict.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            logger.warning("MirrorAgent: GEMINI_API_KEY not set — agent will run in demo mode")
            self._client: OpenAI | None = None
        else:
            self._client = OpenAI(
                api_key=settings.gemini_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            )
        self.model = "gemini-2.0-flash"

    def _available(self) -> bool:
        return self._client is not None

    def chat(
        self,
        messages: list[dict],
        tool_handlers: dict[str, Any],
        *,
        max_tool_rounds: int = 6,
        max_retries: int = 3,
    ) -> tuple[str, list[dict]]:
        """
        Run one chat turn.

        Args:
            messages: Full conversation history (excluding system prompt — added here).
            tool_handlers: {tool_name: callable(**kwargs) -> dict}
            max_tool_rounds: Max number of tool-call rounds per turn.
            max_retries: API error retries.

        Returns:
            (reply_text, updated_messages) — caller stores updated_messages.
        """
        if not self._available():
            return "（演示模式：请在服务端配置 GEMINI_API_KEY）", messages

        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        for attempt in range(max_retries):
            try:
                for _ in range(max_tool_rounds):
                    resp = self._client.chat.completions.create(
                        model=self.model,
                        messages=full_messages,
                        tools=TOOL_DEFS,
                        tool_choice="auto",
                        temperature=1.0,
                        max_tokens=2000,
                    )
                    msg = resp.choices[0].message

                    if not msg.tool_calls:
                        text = msg.content or "小镜已为您处理完毕～"
                        full_messages.append({"role": "assistant", "content": text})
                        return text, full_messages[1:]  # strip system prompt before returning

                    # process tool calls
                    full_messages.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                    })
                    for tc in msg.tool_calls:
                        fn_name = tc.function.name
                        fn_args = json.loads(tc.function.arguments)
                        logger.info("MirrorAgent tool call: %s(%s)", fn_name, fn_args)
                        handler = tool_handlers.get(fn_name)
                        if handler is None:
                            result = {"error": f"Unknown tool: {fn_name}"}
                        else:
                            try:
                                result = handler(**fn_args)
                            except Exception as exc:
                                logger.exception("Tool %s raised: %s", fn_name, exc)
                                result = {"error": str(exc)}
                        full_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        })

                return "小镜已为您处理完毕～", full_messages[1:]

            except (RateLimitError, APIError) as exc:
                err = str(exc)
                logger.warning("MirrorAgent API error (attempt %d/%d): %s", attempt + 1, max_retries, exc)
                if attempt < max_retries - 1:
                    wait = 15 if "503" in err or "UNAVAILABLE" in err else 2 ** (attempt + 1)
                    time.sleep(wait)
                else:
                    return "API 服务器太忙了，请稍后再试…", messages
            except Exception as exc:
                logger.exception("MirrorAgent unexpected error: %s", exc)
                return f"出错了：{str(exc)[:100]}", messages

        return "小镜遇到了一些问题，请稍后再试～", messages

    def stream_chat(
        self,
        messages: list[dict],
        tool_handlers: dict[str, Any],
    ) -> Iterator[dict]:
        """
        Streaming variant — yields server-sent event payloads:
          {"type": "text", "delta": "..."}
          {"type": "tool_start", "name": "show_recommendations"}
          {"type": "tool_result", "name": "...", "result": {...}}
          {"type": "done", "messages": [...]}
          {"type": "error", "message": "..."}
        """
        if not self._available():
            yield {"type": "text", "delta": "（演示模式：请配置 GEMINI_API_KEY）"}
            yield {"type": "done", "messages": messages}
            return

        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        try:
            for _ in range(6):
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=full_messages,
                    tools=TOOL_DEFS,
                    tool_choice="auto",
                    temperature=1.0,
                    max_tokens=2000,
                )
                msg = resp.choices[0].message

                if not msg.tool_calls:
                    text = msg.content or "小镜已为您处理完毕～"
                    full_messages.append({"role": "assistant", "content": text})
                    yield {"type": "text", "delta": text}
                    yield {"type": "done", "messages": full_messages[1:]}
                    return

                full_messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                })
                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    fn_args = json.loads(tc.function.arguments)
                    yield {"type": "tool_start", "name": fn_name}
                    handler = tool_handlers.get(fn_name)
                    if handler is None:
                        result = {"error": f"Unknown tool: {fn_name}"}
                    else:
                        try:
                            result = handler(**fn_args)
                        except Exception as exc:
                            result = {"error": str(exc)}
                    yield {"type": "tool_result", "name": fn_name, "result": result}
                    full_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

            yield {"type": "done", "messages": full_messages[1:]}

        except Exception as exc:
            logger.exception("MirrorAgent stream error: %s", exc)
            yield {"type": "error", "message": str(exc)[:200]}
