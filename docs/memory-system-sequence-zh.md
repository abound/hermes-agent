# Hermes 三层记忆系统时序图（中文）

本文展示 Hermes Agent 在一次用户对话中，三层记忆如何参与：

- 第一层：内置持久记忆（`MEMORY.md` / `USER.md`）
- 第二层：外部记忆 Provider（可选，如 Honcho/Mem0）
- 第三层：会话检索记忆（`session_search` + SQLite FTS5）

## 1. 一次对话的主时序

```mermaid
sequenceDiagram
    autonumber
    participant U as "用户"
    participant CLI as "CLI/Gateway"
    participant AG as "AIAgent(run_agent.py)"
    participant SYS as "系统提示词构建(_build_system_prompt)"
    participant MEM as "MemoryStore(memory_tool.py)"
    participant MGR as "MemoryManager(memory_manager.py)"
    participant EXT as "外部Provider(memory_provider)"
    participant LLM as "大模型API"
    participant SS as "session_search_tool.py"
    participant DB as "SessionDB(SQLite+FTS5)"

    U->>CLI: 输入消息
    CLI->>AG: run_conversation(user_message)

    Note over AG,SYS: 会话启动阶段（或新会话）
    AG->>MEM: load_from_disk() 读取 MEMORY.md/USER.md
    AG->>SYS: _build_system_prompt()
    SYS->>MEM: format_for_system_prompt("memory/user")
    MEM-->>SYS: 冻结快照块（本会话不变）
    AG->>MGR: 初始化外部记忆管理器（若配置）
    MGR->>EXT: initialize(...)
    EXT-->>MGR: provider ready
    MGR-->>SYS: build_system_prompt() 附加外部记忆说明（可选）

    Note over AG,LLM: 每轮API调用前
    AG->>MGR: prefetch_all(original_user_message)
    MGR->>EXT: prefetch(query)
    EXT-->>MGR: recalled context
    MGR-->>AG: 合并 recall 文本
    AG->>AG: build_memory_context_block() 包裹为 <memory-context>
    AG->>LLM: 发送消息（用户消息 + recall注入）

    LLM-->>AG: 返回普通回复 或 tool_call

    alt 模型调用 memory 工具
        AG->>MEM: memory_tool(action,target,content,old_text)
        MEM-->>AG: 写入/替换结果(JSON)
        AG->>MGR: on_memory_write(...) 同步给外部Provider（可选）
        MGR->>EXT: on_memory_write(...)
    else 模型调用 session_search 工具
        AG->>SS: session_search(query, ...)
        SS->>DB: search_messages() / list_sessions_rich()
        DB-->>SS: 命中历史会话
        SS->>LLM: 辅助模型总结历史会话
        LLM-->>SS: 历史总结
        SS-->>AG: 返回结构化检索结果(JSON)
    else 无工具调用
        AG-->>CLI: 直接回复
    end

    Note over AG,MGR: 当前轮结束后
    AG->>MGR: sync_all(user, assistant)
    MGR->>EXT: sync_turn(...)
    AG->>MGR: queue_prefetch_all(user_message)
    MGR->>EXT: queue_prefetch(...)

    AG-->>CLI: 输出最终回复
    CLI-->>U: 展示结果
```

## 1.1 方法中文对照（方法名保留英文，中文解释语义）

- `run_conversation()`：执行一次完整对话回合
- `_build_system_prompt()`：组装系统提示词
- `load_from_disk()`：从磁盘加载记忆文件
- `format_for_system_prompt()`：把记忆格式化为系统提示词片段
- `prefetch_all()`：聚合所有记忆提供器的召回上下文
- `build_memory_context_block()`：把召回内容包装成记忆上下文块
- `memory_tool()`：执行记忆写入/替换/删除动作
- `on_memory_write()`：把内置记忆写入同步通知给外部提供器
- `session_search()`：检索历史会话并生成摘要
- `search_messages()`：在 SQLite FTS5 中检索命中消息
- `list_sessions_rich()`：列出会话元信息（供检索筛选）
- `sync_all()`：把当前轮对话同步到外部记忆提供器
- `queue_prefetch_all()`：为下一轮预取召回任务排队

## 2. 关键机制说明（学习时优先看）

1. 冻结快照机制（内置记忆）
- `MEMORY.md` / `USER.md` 在会话开始时注入系统提示词，之后本会话不热更新。
- 工具写入会立刻落盘，但要到下一会话才反映到系统提示词。

2. API调用时注入机制（外部记忆）
- 外部 Provider 的 recall 不是改系统提示词，而是在每轮 API 调用前临时注入当前用户消息。
- 这样兼顾“可回忆”与“提示词缓存稳定”。

3. 检索记忆与持久记忆分工
- 持久记忆：少量高价值事实，常驻上下文。
- session_search：按需检索全历史（SQLite FTS5 + 摘要），用于“我们之前聊过什么”的召回。

## 3. 对应源码入口（便于跳读）

- 会话初始化内置记忆加载：`run_agent.py`（约 1105 行）
- 系统提示词组装：`run_agent.py`（约 2904 行）
- memory / session_search 工具分发：`run_agent.py`（约 6503 行）
- 内置记忆实现：`tools/memory_tool.py`
- 外部记忆编排：`agent/memory_manager.py`
- Provider 抽象：`agent/memory_provider.py`
- 会话检索工具：`tools/session_search_tool.py`
- SQLite + FTS5：`hermes_state.py`
