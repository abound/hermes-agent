# agent/ 包说明（中文）

`agent/` 是从 `run_agent.py` 拆出的 **Agent 内部模块**，供 `AIAgent` 编排类调用。  
各文件多为**无状态工具函数**或**自包含类**，不直接作为 CLI 入口。

```
run_agent.py (AIAgent 编排)
    ├── prompt_builder / prompt_caching     → 系统提示词
    ├── memory_manager / memory_provider  → 记忆
    ├── context_compressor / context_engine → 上下文压缩
    ├── auxiliary_client                  → 辅助 LLM（摘要、视觉等）
    ├── anthropic_adapter                 → Anthropic API 适配
    ├── error_classifier / credential_pool → 错误恢复与凭证池
    ├── usage_pricing / model_metadata    → 定价与模型元数据
    └── display / skill_*                 → CLI 展示与技能
```

---

## 一、提示词与上下文

| 文件 | 作用 |
|------|------|
| **prompt_builder.py** | 组装系统提示词：身份、平台提示、技能索引、SOUL.md / AGENTS.md / .cursorrules 等上下文文件。无状态函数，由 `AIAgent._build_system_prompt()` 调用。 |
| **prompt_caching.py** | Anthropic **提示词缓存**（`system_and_3` 策略）：在 system + 最近 3 条消息上打 `cache_control` 断点，多轮对话输入 token 成本约降 75%。 |
| **subdirectory_hints.py** | **渐进式子目录提示**：Agent 通过 read_file / terminal 等进入子目录时，懒加载该目录下的 AGENTS.md、CLAUDE.md 等，追加到工具结果（不改 system prompt，避免破坏缓存）。 |
| **context_engine.py** | 可插拔**上下文引擎**抽象基类。默认实现是 `ContextCompressor`；也可通过 `config.yaml` 的 `context.engine` 或插件替换（如 LCM）。 |
| **context_compressor.py** | **自动上下文压缩**：对话接近模型窗口上限时，用辅助模型摘要中间轮次，保留首尾。含结构化摘要模板、迭代更新、工具输出裁剪等。 |
| **context_references.py** | 解析用户消息中的 `@file:`、`@folder:`、`@git:`、`@url:` 等引用，展开为可读上下文（含敏感路径拦截）。 |
| **manual_compression_feedback.py** | 用户手动执行 `/compress` 等命令时，生成可读的压缩结果摘要。 |

---

## 二、记忆系统

| 文件 | 作用 |
|------|------|
| **memory_provider.py** | 可插拔**记忆提供者**抽象基类。内置 MEMORY.md / USER.md 始终启用；外部插件（Honcho、Mem0 等）最多同时 1 个。 |
| **memory_manager.py** | **记忆编排器**：统一管理内置 + 至多一个外部 provider。提供 `build_system_prompt()`、`prefetch_all()`、`sync_all()` 等，`run_conversation` 在回合前后调用。 |

---

## 三、模型、定价与路由

| 文件 | 作用 |
|------|------|
| **model_metadata.py** | 模型**上下文长度**、token 粗估、OpenRouter/models.dev 查询等纯工具函数。 |
| **models_dev.py** | 集成 [models.dev](https://models.dev)  registry：4000+ 模型、109+ 提供商的元数据（价格、能力、上下文等）。 |
| **usage_pricing.py** | **用量与费用估算**：`CanonicalUsage`、`estimate_usage_cost()`、官方定价快照、OpenRouter 定价、/usage 命令数据源。 |
| **smart_model_routing.py** | 可选的**便宜模型 vs 强模型**路由辅助（按配置/env 切换）。 |
| **rate_limit_tracker.py** | 从 API 响应头解析 `x-ratelimit-*`，供 `/usage` 显示 RPM/TPM 余量。 |

---

## 四、API 客户端与适配

| 文件 | 作用 |
|------|------|
| **auxiliary_client.py** | **辅助 LLM 路由器**：压缩、会话搜索、网页摘要、视觉分析等 side task 共用一套 provider 解析链（OpenRouter → Nous → Codex → Anthropic → 各直连 provider）。 |
| **anthropic_adapter.py** | **Anthropic Messages API 适配器**：OpenAI 格式消息 ↔ Anthropic API；支持 API Key、OAuth token、Claude Code 凭证。 |
| **copilot_acp_client.py** | 通过 `copilot --acp` 把 GitHub Copilot ACP 包装成 OpenAI 兼容客户端。 |
| **credential_pool.py** | **多凭证池**：同一 provider 下多 Key/OAuth 轮换，失败时自动切换，持久化状态。 |

---

## 五、错误处理与重试

| 文件 | 作用 |
|------|------|
| **error_classifier.py** | **API 错误分类器**：将异常映射为 FailoverReason（限流、认证、上下文过长等），决定重试 / 换凭证 / fallback / 压缩 / 中止。 |
| **retry_utils.py** | **带抖动的退避**（jittered backoff），避免多会话同时重试造成惊群。 |

---

## 六、CLI 展示与技能

| 文件 | 作用 |
|------|------|
| **display.py** | CLI **展示层**：KawaiiSpinner、工具调用预览、diff 高亮等；`AIAgent._execute_tool_calls` 用于终端反馈。 |
| **skill_utils.py** | 轻量**技能元数据**解析（frontmatter、平台过滤），避免导入 tool registry 重链。 |
| **skill_commands.py** | `/skill-name`、`/plan` 等**斜杠命令**共享逻辑，CLI 与 gateway 共用。 |

---

## 七、会话、轨迹与洞察

| 文件 | 作用 |
|------|------|
| **trajectory.py** | **轨迹保存**辅助函数与静态工具（JSONL 写入等）；格式转换仍部分在 `AIAgent._convert_to_trajectory_format`。 |
| **title_generator.py** | 根据首轮对话**异步生成会话标题**（不阻塞用户回复）。 |
| **insights.py** | **会话洞察引擎**：从 SQLite 分析 token、费用、工具使用、模型/平台分布（类似 Claude Code `/insights`）。 |

---

## 八、安全与杂项

| 文件 | 作用 |
|------|------|
| **redact.py** | 日志与工具输出中的**密钥脱敏**（正则匹配 API Key、token 等）。 |
| **__init__.py** | 包说明：本目录模块从原 `run_agent.py` 抽出，使主文件聚焦 `AIAgent` 编排。 |

---

## 与 run_conversation 的调用关系（简图）

```
run_conversation()
  ├─ _build_system_prompt()     → prompt_builder, memory_manager
  ├─ _compress_context()        → context_compressor / context_engine
  ├─ prefetch_all()             → memory_manager → memory_provider
  ├─ apply_anthropic_cache_control → prompt_caching
  ├─ API 失败                   → error_classifier → credential_pool / fallback
  ├─ _execute_tool_calls()      → display, subdirectory_hints
  └─ estimate_usage_cost()      → usage_pricing, model_metadata
```

---

## 阅读建议

| 你想了解… | 先看 |
|-----------|------|
| 系统提示词怎么拼 | `prompt_builder.py` |
| 对话太长怎么压缩 | `context_compressor.py` |
| DeepSeek/Anthropic 怎么适配 | `anthropic_adapter.py` 或 `run_agent.py` 主循环 |
| 记忆怎么注入 | `memory_manager.py` |
| 费用怎么算 | `usage_pricing.py` |
| 辅助模型（摘要/视觉）走哪 | `auxiliary_client.py` |
