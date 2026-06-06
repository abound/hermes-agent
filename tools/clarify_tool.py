#!/usr/bin/env python3
"""
需求澄清工具模块。

该模块允许智能体向用户发起结构化多选问题，或开放式补充提问。
在 CLI 模式下，用户可以用方向键选择选项；在消息平台中，
选项通常会以编号列表的形式展示。

真正的用户交互逻辑位于平台层：
- CLI 由 `cli.py` 负责
- 消息平台由 `gateway/run.py` 负责

本模块只负责定义工具 schema、参数校验，以及把问题转交给
平台层 callback 的轻量分发逻辑。
"""

import json
from typing import List, Optional, Callable


# 智能体最多可提供的预设选项数量。
# UI 会自动追加第 5 个“其他（手动输入）”选项。
MAX_CHOICES = 4


def clarify_tool(
    question: str,
    choices: Optional[List[str]] = None,
    callback: Optional[Callable] = None,
) -> str:
    """
    向用户提问，可选提供多选项。

    参数：
        question：要展示给用户的问题文本。
        choices：最多 4 个预设答案选项；若省略，则表示纯开放式提问。
        callback：由平台层提供的实际交互函数。
                  其签名应为 `callback(question, choices) -> str`。
                  该回调由 agent 运行层（CLI / gateway）注入。

    返回：
        包含用户回答的 JSON 字符串。
    """
    if not question or not question.strip():
        return tool_error("必须提供问题文本。")

    question = question.strip()

    # 校验并裁剪选项列表。
    if choices is not None:
        if not isinstance(choices, list):
            return tool_error("choices 必须是字符串列表。")
        choices = [str(c).strip() for c in choices if str(c).strip()]
        if len(choices) > MAX_CHOICES:
            choices = choices[:MAX_CHOICES]
        if not choices:
            choices = None  # 空列表视为开放式提问

    if callback is None:
        return json.dumps(
            {"error": "当前执行环境不支持 clarify 工具。"},
            ensure_ascii=False,
        )

    try:
        user_response = callback(question, choices)
    except Exception as exc:
        return json.dumps(
            {"error": f"获取用户输入失败：{exc}"},
            ensure_ascii=False,
        )

    return json.dumps({
        "question": question,
        "choices_offered": choices,
        "user_response": str(user_response).strip(),
    }, ensure_ascii=False)


def check_clarify_requirements() -> bool:
    """clarify 工具没有额外外部依赖，默认始终可用。"""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

CLARIFY_SCHEMA = {
    "name": "clarify",
    "description": (
        "当你在继续执行前需要向用户澄清需求、收集反馈，或让用户做出决策时，"
        "使用这个工具。支持两种模式：\n\n"
        "1. **多选模式**：最多提供 4 个选项。用户可直接选择其中一个，"
        "或通过第 5 个“其他”选项输入自己的答案。\n"
        "2. **开放式模式**：完全不提供 choices，用户自由输入文本回答。\n\n"
        "适用场景：\n"
        "- 任务存在歧义，需要用户明确选择执行方案\n"
        "- 任务完成后需要追问反馈（例如“效果怎么样？”）\n"
        "- 想询问是否保存 skill 或更新 memory\n"
        "- 某个决策存在明显权衡，应该让用户参与判断\n\n"
        "不适用场景：\n"
        "- 危险命令的简单是/否确认，这类确认由 terminal 工具处理\n"
        "- 低风险小决策，此时更推荐你先做合理默认选择，而不是频繁打断用户"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要展示给用户的问题文本。",
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_CHOICES,
                "description": (
                    "最多 4 个答案选项。若省略该参数，则表示开放式提问。"
                    "当提供选项时，UI 会自动追加一个“其他（手动输入）”选项。"
                ),
            },
        },
        "required": ["question"],
    },
}


# --- 注册到工具注册表 ---
from tools.registry import registry, tool_error

registry.register(
    name="clarify",
    toolset="clarify",
    schema=CLARIFY_SCHEMA,
    handler=lambda args, **kw: clarify_tool(
        question=args.get("question", ""),
        choices=args.get("choices"),
        callback=kw.get("callback")),
    check_fn=check_clarify_requirements,
    emoji="❓",
)
