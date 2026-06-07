#!/usr/bin/env python3
"""
Memory 工具模块 — 有界、可持久化的精选记忆

跨会话持久化，文件落盘。两个存储：
  - MEMORY.md：agent 个人笔记（环境事实、项目约定、工具坑、学到的经验）
  - USER.md：关于用户的信息（偏好、沟通风格、期望、工作习惯）

二者在会话启动时以冻结快照注入 system prompt。
会话中途写入会立即落盘，但**不会**改变当前会话的 system prompt —— 为保持 prefix cache。
下次会话启动（或 _invalidate_system_prompt + load_from_disk）时快照刷新。

条目分隔符：§（section sign），条目可多行。
上限按字符计（非 token），与具体模型无关。

设计要点：
- 单一 `memory` 工具，action：add / replace / remove
- replace/remove 用短唯一子串匹配（非全文、非 ID）
- 行为指引写在 tool schema description（英文，给模型看）
- 冻结快照：system prompt 稳定；工具返回反映 live 状态
"""

import fcntl
import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# 记忆文件目录 — 动态解析，尊重 HERMES_HOME / profile 切换
# 旧版模块级常量在 import 时缓存，profile 切换后可能过期
def get_memory_dir() -> Path:
    """返回当前 profile 下的 memories 目录。"""
    return get_hermes_home() / "memories"

# 向后兼容别名 — gateway/run.py 在函数体内运行时 import，能拿到正确路径
MEMORY_DIR = get_memory_dir()

ENTRY_DELIMITER = "\n§\n"


# ---------------------------------------------------------------------------
# 记忆内容扫描 — 写入 system prompt 前的轻量注入/窃密检测
# ---------------------------------------------------------------------------

_MEMORY_THREAT_PATTERNS = [
    # Prompt injection
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    # Exfiltration via curl/wget with secrets
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    # Persistence via shell rc
    (r'authorized_keys', "ssh_backdoor"),
    (r'\$HOME/\.ssh|\~/\.ssh', "ssh_access"),
    (r'\$HOME/\.hermes/\.env|\~/\.hermes/\.env', "hermes_env"),
]

# 用于注入检测的不可见字符子集
_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}


def _scan_memory_content(content: str) -> Optional[str]:
    """扫描记忆内容中的注入/窃密模式。命中则返回错误字符串。"""
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."

    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"Blocked: content matches threat pattern '{pid}'. Memory entries are injected into the system prompt and must not contain injection or exfiltration payloads."

    return None


class MemoryStore:
    """
    有界精选记忆 + 文件持久化。每个 AIAgent 一个实例。

    维护两套并行状态：
      - _system_prompt_snapshot：load_from_disk 时冻结，用于 system prompt 注入；
        会话中途不更新，保持 prefix cache 稳定。
      - memory_entries / user_entries：live 状态，工具调用会改并落盘；
        工具返回始终反映 live 状态。
    """

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # system prompt 用的冻结快照 — 仅在 load_from_disk() 时更新
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}

    def load_from_disk(self):
        """从 MEMORY.md / USER.md 加载条目，并捕获 system prompt 快照。"""
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")

        # 去重（保序，保留首次出现）
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        # 冻结快照供 system prompt 注入
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """独占文件锁，保证读-改-写安全。

        使用独立 .lock 文件，记忆本体仍可用 os.replace() 原子替换。
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = get_memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str):
        """在持锁前提下从磁盘重读条目到内存。

        变更前先拉最新状态，避免多会话/多进程写冲突。
        """
        fresh = self._read_file(self._path_for(target))
        fresh = list(dict.fromkeys(fresh))  # 去重
        self._set_entries(target, fresh)

    def save_to_disk(self, target: str):
        """每次变更后持久化到对应文件。"""
        get_memory_dir().mkdir(parents=True, exist_ok=True)
        self._write_file(self._path_for(target), self._entries_for(target))

    def _entries_for(self, target: str) -> List[str]:
        if target == "user":
            return self.user_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: List[str]):
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        return self.memory_char_limit

    def add(self, target: str, content: str) -> Dict[str, Any]:
        """追加新条目。超字符上限则返回错误。"""
        content = content.strip()
        if not content:
            return {"success": False, "error": "内容不能为空。"}

        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            # 持锁后重读，合并其它会话的写入
            self._reload_target(target)

            entries = self._entries_for(target)
            limit = self._char_limit(target)

            # 【何时】内容与已有条目完全相同
            # 【为何】避免重复占用配额
            if content in entries:
                return self._success_response(target, "条目已存在（未重复添加）。")

            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))

            # 【何时】追加后总字符数超 limit
            # 【为何】硬上限，引导模型 replace/remove 后再 add
            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"记忆已用 {current:,}/{limit:,} 字符。"
                        f"添加本条（{len(content)} 字符）将超出上限。"
                        f"请先 replace 或 remove 现有条目。"
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(content)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "已添加条目。")

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """用子串 old_text 定位条目，整段替换为 new_content。"""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text 不能为空。"}
        if not new_content:
            return {"success": False, "error": "new_content 不能为空。删除请用 remove。"}

        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"没有条目匹配「{old_text}」。"}

            # 【何时】多条目都含 old_text 子串
            if len(matches) > 1:
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"多条条目匹配「{old_text}」，请提供更具体的子串。",
                        "matches": previews,
                    }
                # 多条但内容完全相同 — 只改第一条即可

            idx = matches[0][0]
            limit = self._char_limit(target)

            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"替换后将达 {new_total:,}/{limit:,} 字符，超出上限。"
                        f"请缩短新内容或先删除其它条目。"
                    ),
                }

            entries[idx] = new_content
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "已替换条目。")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """删除包含 old_text 子串的那一条目。"""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text 不能为空。"}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"没有条目匹配「{old_text}」。"}

            if len(matches) > 1:
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"多条条目匹配「{old_text}」，请提供更具体的子串。",
                        "matches": previews,
                    }
                # 多条完全相同 — 只删第一条

            idx = matches[0][0]
            entries.pop(idx)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "已删除条目。")

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        """
        返回供 system prompt 注入的冻结快照。

        是 load_from_disk() 时的状态，非 live 状态。
        会话中途写入不影响此返回值，以保持 prefix cache。

        加载时无条目则返回 None。
        """
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # -- 内部辅助 --

    def _success_response(self, target: str, message: str = None) -> Dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        resp = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} 字符",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: List[str]) -> str:
        """渲染带标题与占用率指示的 system prompt 块（注入给模型）。"""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = f"用户画像 [{pct}% — {current:,}/{limit:,} 字符]"
        else:
            header = f"记忆笔记 [{pct}% — {current:,}/{limit:,} 字符]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """读取记忆文件并按条目拆分。

        读操作无需加锁：_write_file 用原子 rename，读者总能看到完整旧文件或完整新文件。
        """
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []

        if not raw.strip():
            return []

        # 必须用 ENTRY_DELIMITER 拆分；单按 "§" 拆会把条目内容里的 § 误切开
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    @staticmethod
    def _write_file(path: Path, entries: List[str]):
        """临时文件写入 + 原子 rename 落盘。

        旧实现 open("w")+flock 会在拿到锁之前 truncate，并发读者可能看到空文件。
        原子 rename 保证读者只看到完整的旧版或新版。
        """
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(path))  # 同文件系统上原子
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


def memory_tool(
    action: str,
    target: str = "memory",
    content: str = None,
    old_text: str = None,
    store: Optional[MemoryStore] = None,
) -> str:
    """
    memory 工具统一入口，分发到 MemoryStore 的 add/replace/remove。

    Returns:
        JSON 字符串（success、entries、usage 等）
    """
    if store is None:
        return tool_error("记忆不可用。可能已在配置中关闭或当前环境未启用。", success=False)

    if target not in ("memory", "user"):
        return tool_error(f"无效的 target「{target}」。请使用 memory 或 user。", success=False)

    if action == "add":
        if not content:
            return tool_error("add 操作需要 content。", success=False)
        result = store.add(target, content)

    elif action == "replace":
        if not old_text:
            return tool_error("replace 操作需要 old_text。", success=False)
        if not content:
            return tool_error("replace 操作需要 content。", success=False)
        result = store.replace(target, old_text, content)

    elif action == "remove":
        if not old_text:
            return tool_error("remove 操作需要 old_text。", success=False)
        result = store.remove(target, old_text)

    else:
        return tool_error(f"未知的 action「{action}」。请使用：add、replace、remove。", success=False)

    return json.dumps(result, ensure_ascii=False)


def check_memory_requirements() -> bool:
    """无外部依赖，memory 工具始终可用。"""
    return True


# =============================================================================
# OpenAI Function-Calling Schema（中文 — 直接发给模型）
# =============================================================================

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "将跨会话仍有价值的信息写入持久记忆。"
        "记忆会注入后续对话，请保持精简，只记以后仍有用的事实。\n\n"
        "何时保存（主动执行，不要等用户开口）：\n"
        "- 用户纠正你，或说「记住」「别再那样做」\n"
        "- 用户分享偏好、习惯或个人细节（姓名、角色、时区、编码风格等）\n"
        "- 你发现环境信息（操作系统、已装工具、项目结构）\n"
        "- 你学到该用户环境下的约定、API 怪癖或工作流\n"
        "- 你识别出未来会话仍会用的稳定事实\n\n"
        "优先级：用户偏好与纠正 > 环境事实 > 流程性知识。"
        "最有价值的记忆能减少用户反复提醒。\n\n"
        "不要保存任务进度、会话结果、完工日志或临时 TODO；"
        "这类内容用 session_search 从历史 transcript 召回。\n"
        "若发现可复用的做法或解法，用 skill 工具存为技能。\n\n"
        "两个 target：\n"
        "- user：用户是谁 — 姓名、角色、偏好、沟通风格、忌讳\n"
        "- memory：你的笔记 — 环境事实、项目约定、工具坑、经验教训\n\n"
        "操作：add（新增）、replace（更新 — 用 old_text 定位）、"
        "remove（删除 — 用 old_text 定位）。\n\n"
        "跳过：琐碎/显而易见、易重新查到的、原始数据 dump、临时任务状态。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "要执行的操作：add / replace / remove。"
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "存储目标：memory=个人笔记，user=用户画像。"
            },
            "content": {
                "type": "string",
                "description": "条目正文。add 和 replace 必填。"
            },
            "old_text": {
                "type": "string",
                "description": "用于 replace/remove 的短唯一子串，定位要改或删的条目。"
            },
        },
        "required": ["action", "target"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)


