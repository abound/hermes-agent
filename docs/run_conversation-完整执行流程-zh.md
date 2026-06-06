# Hermes-Agent `run_conversation` 完整执行流程（中文详解）

## 1. 文档目的

本文档聚焦 `AIAgent.run_conversation(...)` 的真实执行链路，回答三个核心问题：

1. 这个方法到底做了哪些阶段性工作。
2. 工具调用、重试、上下文压缩分别在什么时机发生。
3. 一轮对话如何结束，以及最终返回给调用方的结果结构是什么。

适用对象：

1. 阅读 Hermes 主循环的新同学。
2. 需要改重试/压缩/工具执行逻辑的开发者。
3. 排查“为什么停在某一步”的问题定位人员。

---

## 2. 入口与调用方

核心方法定义：

- `run_agent.py:7360` `AIAgent.run_conversation(...)`

常见调用入口：

1. CLI：`cli.py:7947` 调用 `self.agent.run_conversation(...)`
2. Gateway：`gateway/run.py:9566` 调用 `agent.run_conversation(...)`
3. 批处理：`batch_runner.py:337` 调用 `agent.run_conversation(...)`

`chat()` 只是薄封装：

- `run_agent.py:9993` `chat(...)` 内部直接调用 `run_conversation(...)` 并返回 `final_response`。

---

## 3. 方法签名与输入输出

方法签名：

- `run_agent.py:7360`

关键入参：

1. `user_message`：本轮用户输入。
2. `system_message`：可选系统提示词覆盖。
3. `conversation_history`：会话历史消息。
4. `task_id`：任务隔离标识（工具环境、资源清理使用）。
5. `stream_callback`：流式文本回调（CLI/TTS）。
6. `persist_user_message`：当实际发送文本被注入前缀时，用于保存“原始用户语句”。

关键返回字段（`dict`）：

1. `final_response`：最终自然语言回复。
2. `messages`：本轮结束后的完整消息链。
3. `api_calls`：本轮发生的模型调用次数。
4. `completed` / `partial` / `failed` / `interrupted`：回合状态。
5. `model/provider/base_url`：本轮实际模型信息。
6. token 与成本字段：`input_tokens`、`output_tokens`、`estimated_cost_usd` 等。

---

## 4. 一轮执行总览

可把它理解为“回合级状态机”：

1. 轮前初始化与历史整理。
2. 系统提示词准备与预检压缩。
3. 进入主循环：构请求 -> 调模型 -> 解析响应。
4. 若有工具调用：执行工具并回注 `tool` 消息，再继续循环。
5. 若无工具调用：产出最终回复并退出循环。
6. 收尾：持久化、轨迹、清理、hook、返回结果。

---

## 5. 详细阶段拆解

## 5.1 轮前初始化（进入主循环前）

主要位置：

- `run_agent.py:7385-7465`

关键动作：

1. 安装安全 stdio 防护，避免守护进程断管导致崩溃。
2. 恢复 primary runtime（上一轮如果 fallback 过，这里先还原）。
3. 清洗输入中的无效 surrogate 字符。
4. 生成 `effective_task_id`（若未传入则自动 UUID）。
5. 重置本轮重试计数器与中间态。
6. 清理僵尸连接（网络抖动后遗留）。
7. 初始化迭代预算 `IterationBudget`。
8. 复制历史消息并去掉“预算压力提示”残留。
9. 必要时从历史回填 todo store。

## 5.2 用户消息入链与 nudge 计数

主要位置：

- `run_agent.py:7460-7482`

关键动作：

1. 记录用户轮次 `_user_turn_count`。
2. 保存 `original_user_message`（用于持久化与 memory provider 查询）。
3. 判断 memory nudge 是否到触发间隔。
4. 追加当前 user 消息到 `messages`。

## 5.3 系统提示词与缓存策略

主要位置：

- `run_agent.py:7486-7527`
- `run_agent.py:2898` `_build_system_prompt(...)`

关键设计：

1. 首轮构建 system prompt，后续复用 `_cached_system_prompt`。
2. 续聊优先复用 SQLite 里上轮快照，减少前缀变动。
3. 只有在压缩后或必要时才重建，尽量维持 prompt cache 命中。

## 5.4 预检上下文压缩

主要位置：

- `run_agent.py:7528-7581`
- `run_agent.py:6351` `_compress_context(...)`

关键动作：

1. 在真正调模型前先估算 token（含 tools schema）。
2. 超阈值时先压缩，最多尝试多轮。
3. 压缩后可能切新 session，会清空 `conversation_history` 引用，确保后续落库正确写入新会话。

## 5.5 插件与外部记忆预取

主要位置：

- `run_agent.py:7582-7654`

关键动作：

1. 执行 `pre_llm_call` hook，拿到临时上下文片段。
2. 外部记忆管理器 `prefetch_all(...)` 只做一次并缓存，避免每轮重复拉取。
3. 注入策略是“注入到当前 user 消息”，不是改 system prompt。

---

## 6. 主循环（核心）

循环条件：

- `run_agent.py:7656`
  `while api_call_count < max_iterations and iteration_budget.remaining > 0`

## 6.1 每轮开始：中断与预算检查

主要位置：

- `run_agent.py:7657-7705`

关键动作：

1. 如果收到中断请求，设置 `interrupted` 并退出循环。
2. 递增 API 调用计数并消耗迭代预算。
3. 调 `step_callback`，让 gateway/上层 UI 感知“第几步”。

## 6.2 组装本轮 API 消息

主要位置：

- `run_agent.py:7710-7798`

关键动作：

1. 复制 `messages` 为 `api_messages`，避免直接污染内部消息链。
2. 在当前 user 消息追加 memory prefetch + plugin context（临时注入）。
3. assistant 消息补 `reasoning_content`，并移除内部字段。
4. 严格 provider 下清理 `call_id/response_item_id` 等兼容字段。
5. 叠加 `effective_system`、`prefill_messages`、prompt caching。
6. 发请求前执行 `_sanitize_api_messages(...)`，修复孤儿 tool 消息。

## 6.3 发模型请求与内层重试

主要位置：

- `run_agent.py:7848-9092`
- `run_agent.py:5701` `_build_api_kwargs(...)`
- `run_agent.py:4633` `_interruptible_streaming_api_call(...)`

关键行为：

1. 构建 provider 相关参数（reasoning/max_tokens/extra_body 等）。
2. 默认优先走流式请求，便于健康探测与中断响应。
3. 响应 shape 校验，异常则按分类恢复。
4. 处理 `length` 截断、空响应、401/429/413、上下文溢出、签名失效等。
5. 必要时触发：
   1. 压缩并重试。
   2. 降低 context tier。
   3. 调整 max output token。
   4. 凭证池轮换。
   5. fallback 模型切换（`_try_activate_fallback`）。

## 6.4 响应标准化与后处理

主要位置：

- `run_agent.py:9093-9264`
- `run_agent.py:6031` `_build_assistant_message(...)`

关键动作：

1. 按 `api_mode` 规范化成统一 `assistant_message`。
2. 清洗 `content`（兼容 list/dict 形态返回）。
3. 抽取/保存 reasoning、reasoning_details、codex reasoning items。
4. 处理不完整 scratchpad/incomplete 响应的续写或回退。

## 6.5 有工具调用时

主要位置：

- `run_agent.py:9264-9543`
- `run_agent.py:6466` `_execute_tool_calls(...)`
- `model_tools.py:459` `handle_function_call(...)`

流程：

1. 校验工具名并尝试自动修复错误工具名。
2. 校验参数 JSON，必要时注入错误 tool 结果让模型自纠。
3. 执行 guardrails（限流、去重、delegate 限制）。
4. 追加 assistant 消息（带 tool_calls）到消息链。
5. 执行工具：
   1. 并发或串行调度。
   2. agent-level 工具直接处理（`todo/memory/session_search/...`）。
   3. 其他工具走 `handle_function_call -> registry.dispatch`。
6. 每个工具结果都写回 `role="tool"` 消息。
7. 可触发工具结果持久化、预算注入、上下文压力告警。
8. 处理完成后 `continue` 回主循环，发下一次模型请求。

## 6.6 无工具调用时（最终回复路径）

主要位置：

- `run_agent.py:9545-9756`

关键动作：

1. 取 `assistant_message.content` 作为候选 `final_response`。
2. 若“仅思维无可见文本”，尝试：
   1. 用上一个“有内容+工具”的回合作为兜底。
   2. thinking-prefill 继续追一轮。
   3. 空响应重试与 fallback。
3. 清理 `<think>` 后得到面向用户的最终文本。
4. 追加 final assistant 消息并退出主循环。

---

## 7. 回合结束与收尾

主要位置：

- `run_agent.py:9809-9991`

关键动作：

1. 若触达最大迭代，走 `_handle_max_iterations(...)` 给出终止响应。
2. 计算 `completed` 状态。
3. 保存 trajectory（可选）。
4. 清理 task 级资源（浏览器/环境等）。
5. 持久化 session（JSON + SQLite）。
6. 记录回合退出诊断日志（`_turn_exit_reason`）。
7. 执行 `post_llm_call` / `on_session_end` plugin hook。
8. 触发后台 memory/skill review（异步，不阻塞主回复）。
9. 返回最终 result dict。

---

## 8. 关键函数职责地图

1. `run_conversation`：单轮总控状态机。
2. `_build_system_prompt`：系统提示构建与缓存稳定性保证。
3. `_compress_context`：上下文压缩与会话切分。
4. `_build_api_kwargs`：provider 适配层。
5. `_interruptible_streaming_api_call`：可中断的流式网络调用。
6. `_build_assistant_message`：统一响应消息格式。
7. `_execute_tool_calls`：工具批调度（并发/串行）。
8. `handle_function_call`：工具注册中心分发入口。
9. `_persist_session`：会话持久化。

---

## 9. 异常恢复矩阵（摘要）

1. 空响应或 malformed：重试，必要时切 fallback。
2. `finish_reason=length`：续写重试，超限则 partial 返回。
3. 429/计费限制：退避重试或凭证轮换，必要时 fallback。
4. 413 payload 太大：触发压缩后重试。
5. context overflow：下调 context tier 或压缩后重试。
6. thinking signature 失效：剥离 reasoning_details 后重试。
7. 非重试型客户端错误：记录并终止。

---

## 10. 时序图（简化版）

```mermaid
sequenceDiagram
    autonumber
    participant Caller as CLI/Gateway
    participant Agent as run_conversation
    participant LLM as Model API
    participant Tools as Tool Dispatcher
    participant Store as SessionDB/Logs

    Caller->>Agent: run_conversation(user_message, history, ...)
    Agent->>Agent: 初始化 + system prompt + 预检压缩
    loop 主循环
        Agent->>LLM: 发送 api_messages + tools
        LLM-->>Agent: assistant response (text or tool_calls)
        alt 有 tool_calls
            Agent->>Tools: 执行工具
            Tools-->>Agent: tool results
            Agent->>Agent: 追加 role=tool 消息并继续下一轮
        else 纯文本回复
            Agent->>Agent: 生成 final_response
            break 结束主循环
        end
    end
    Agent->>Store: 持久化 + 轨迹 + 统计
    Agent-->>Caller: result{final_response,messages,api_calls,...}
```

---

## 11. 调试建议（实战）

1. 先看 `_turn_exit_reason`，判断是正常结束还是中断/预算耗尽。
2. 看最后一条消息角色是否是 `tool`，若是说明停在“工具后续处理”阶段。
3. 看 `api_calls` 与 `retry_count` 相关日志，确认是否陷入连续重试。
4. 看 `context_compressor.last_prompt_tokens` 与压缩日志，确认是否被上下文压力卡住。
5. 看 tool 结果是否被正确写回 `messages`，缺失时模型会“看不到工具输出”。

---

## 12. 一句话总结

`run_conversation` 不是“单次 LLM 调用函数”，而是 Hermes 的“单轮 Agent 执行内核”：
它把模型推理、工具调用、异常恢复、上下文管理、持久化与观测性全部收敛在一个回合状态机里。
