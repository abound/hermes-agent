# Remote AI Debugger — 需求对齐结论

对齐日期：2026-06-07（按对话默认决策执行，未单独签字时可再改）

## Clarify 门禁

| 项 | 决策 |
|----|------|
| **Clarify ①** | **保留** — 每次纯 `execute_code`（无 `hermes_tools`）后必做，用于对照复现结果、补齐 expected/actual |
| **Clarify ②** | **仅写/变更** — SELECT/GET/read_file/日志 tail 不需要；INSERT/UPDATE/DELETE、POST/PUT、patch、restart、MCP 写操作需要 |
| **入口 clarify** | **不做** — Phase A 只解析用户消息 |
| **Phase E patch** | **Clarify ②** — 用户要求 fix 后，patch/write 前批准 |
| **混合路径** | **是** — 先纯复现 + Clarify ①，再只读查 DB/API；只读无 Clarify ② |

## 工作流

Phase A（解析）→ B（只读侦察）→ C2（纯 execute_code + Clarify ①）→ D（验证；只读直接探测，写操作 Clarify ②）→ E（报告）

## 交付物

| 项 | 决策 |
|----|------|
| **examples/remote-debugger/** | **纳入 git** — `.gitignore` 对子目录例外 |
| **terminal.cwd 示例** | 示例 config 用 `/tmp`（冒烟）；生产 Profile 改为真实项目路径 |
| **MCP** | 默认空；Postgres 场景可用 `terminal` + `psql SELECT` fallback |

## 验收

| 层级 | 标准 |
|------|------|
| **自动化** | `pytest tests/skills/test_remote_ai_debugger_skill.py` 通过 |
| **环境** | `hermes -p remote-debugger doctor` SSH 检查（需本机配置 `TERMINAL_SSH_*`） |
| **端到端 LLM** | 可选 — 场景 A/B 由人工在 CLI 验证 |
