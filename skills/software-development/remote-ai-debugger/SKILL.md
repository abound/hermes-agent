---
name: remote-ai-debugger
description: Remote SSH debugging agent — classify internal logic vs external deps, reproduce with pure execute_code then dual clarify gates, MCP/RPC probes, root-cause report. NO fixes before root cause.
version: 1.2.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [debugging, remote, ssh, mcp, root-cause, reproduction, clarify]
    related_skills: [systematic-debugging]
---

# Remote AI Debugger

## Overview

You debug **on the SSH remote host** (terminal backend), not on the user's Windows/local machine unless explicitly configured otherwise.

**Goal:** Given **expected** vs **actual** behavior, determine **why** they differ using evidence — not guesses.

**Iron law (from systematic-debugging):**

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

Do not patch production code until Phase E report is complete and the user asks for a fix.

## Iron Rules — Dual Clarify Gates

These rules are **mandatory**. Violating them breaks the workflow.

| Rule | Meaning |
|------|---------|
| **No entry clarify** | Phase A parses the user message only — do **not** call `clarify` at session start |
| **Clarify ① after pure repro** | After every **pure** `execute_code` run (no `hermes_tools`), you **must** call `clarify` before Phase E or further investigation |
| **Clarify ② before external writes** | Before **mutating** DB/API/MCP/terminal/file ops — **NOT** before read-only probes |
| **Phase B read-only** | No `write_file`, `patch`, or restarts in Phase B unless required to **read** logs |
| **Pure before external** | For mixed paths: run pure `execute_code` + Clarify ① **before** external probes (read or write) |

**Clarify ② decision table:**

| Operation | Clarify ②? |
|-----------|------------|
| `mcp_*` / `psql` / `curl` with `SELECT` or `GET` | **No** — proceed after Clarify ① |
| `mcp_*` / `psql` / `curl` with `INSERT`/`UPDATE`/`DELETE` or `POST`/`PUT`/`PATCH` | **Yes** |
| `read_file` / `search_files` / `terminal` cat/tail/journalctl | **No** |
| `execute_code` + `hermes_tools` with `read_file` only | **No** |
| `execute_code` + `hermes_tools` with `write_file`/`patch`/mutating `terminal` | **Yes** |
| Phase E fix via `patch` / `write_file` | **Yes** (after user asks for fix) |
| `terminal` restart/rm/deploy or Redis `SET`/`DEL` | **Yes** |

**Self-check before each tool call:**

- Have I run pure `execute_code` yet? → If no, do not Clarify ①
- Did Clarify ① return `user_response`? → If no, do not write Phase E or skip to external investigation
- Is the **next** step a **write/mutate** on an external system or production file? → If yes and Clarify ② not done, call `clarify` first; **read-only** steps need no Clarify ②

## When to Use

Use when the user invokes `/remote-ai-debugger` or describes:

- Remote service/API/job returns wrong result
- Expected outcome vs what actually happened on a server
- Need to trace why behavior diverges from a stated goal

**Prerequisites:**

- Profile `remote-debugger` with `terminal.backend: ssh` (or `TERMINAL_ENV=ssh`)
- SSH host/user configured in profile `.env`
- MCP servers configured for external dependencies (see MCP mapping below)
- `platform_toolsets.cli` includes `clarify` and `code_execution`

## Invocation Format

Parse the user message into these fields. **Missing fields are filled at Clarify ①**, not at entry.

| Field | Meaning |
|-------|---------|
| `expected` | What should happen |
| `actual` | What happened instead |
| `repro` | Steps to trigger (optional) |
| `scope` | Remote path, service name, branch, log file, or endpoint |

Example user message:

```
预期: API 返回 200 且 body.status=ok
实际: 返回 500，日志有 NullPointerException
路径: /opt/myapp  服务: myapp.service
```

---

## Phase A — Parse the Goal (no clarify)

1. Restate `expected`, `actual`, `repro`, `scope` in a short bullet list (use placeholders like `(TBD at Clarify ①)` for missing fields).
2. **Do not call `clarify` in this phase.**
3. State one **testable hypothesis** to investigate first (the most likely divergence point).
4. Proceed immediately to Phase B.

---

## Phase B — Remote Reconnaissance

Use **only** `terminal`, `read_file`, and `search_files` (all execute on SSH remote when profile is configured).

### Terminal checklist

```bash
pwd
hostname
git status 2>/dev/null || true
git log --oneline -5 2>/dev/null || true
# Service / process (adapt to scope)
systemctl status SERVICE_NAME 2>/dev/null || ps aux | head -20
# Logs (adapt paths)
tail -n 100 /var/log/APP/error.log 2>/dev/null || journalctl -u SERVICE_NAME -n 50 --no-pager 2>/dev/null
```

### Code / stack trace

- Map stack traces to files with `read_file` and `search_files`
- Identify the **suspect function** and its **inputs/outputs**

**Rules:**

- Read-only in Phase B — no `write_file`, `patch`, or restarts unless needed to **read** logs
- Prefer evidence over assumptions

---

## Phase C1 — Call Classification

For each suspect code path, classify every significant step:

| Class | Signals | Action |
|-------|---------|--------|
| **Pure internal** | Local computation, parsing, branching — no network, DB, subprocess, external SDK | Pure `execute_code` → **Clarify ①** → Phase D/E |
| **External dependency** | HTTP/RPC, SQL, Redis, MQ, cloud API, external CLI | Pure repro if internal segment exists → **Clarify ①** → read-only MCP/terminal **or** Clarify ② then writes |
| **Mixed** | Internal logic after external IO | Pure Python repro + Clarify ① → read-only external probe → Clarify ② only if mutating |

---

## Phase C2 — Pure execute_code + Clarify ① (mandatory)

### Step 1: Write pure repro script

1. `read_file` the suspect function/block
2. Call `execute_code` with a **minimal script**:
   - Inline mock inputs as constants (no external calls)
   - **Forbidden:** `from hermes_tools import ...` or `import hermes_tools`
   - `print()` values that should match `expected`
   - Keep script under ~80 lines
3. **Fallback:** `terminal` → `python3 /tmp/repro_XXXX.py` (still requires Clarify ① after output)

Pure repro skeleton:

```python
# Minimal repro — NO hermes_tools, NO external I/O
def suspect_logic(x, y):
    return x + y  # replace with extracted logic

inputs = {"x": 1, "y": 2}  # from logs or user repro
result = suspect_logic(**inputs)
print("result:", result)
print("expected:", "DESCRIBE_EXPECTED")  # from user message or TBD
print("match:", result == "DESCRIBE_EXPECTED")
```

**Success indicator:** `execute_code` returns `tool_calls_made: 0` (no RPC inside script).

### Step 2: Clarify ① — after repro output

**Always call `clarify` immediately after pure repro.** Summarize stdout in the question.

**Template (multiple choice):**

```
clarify(
  question="Pure repro script output:\n{stdout_summary}\n\nDoes this match the actual behavior you described? What is the expected result?",
  choices=["Match — continue root cause", "No match — I will add details", "Enough — write report"]
)
```

If `expected` is still missing after the user picks "continue", call open-ended clarify:

```
clarify(question="In one sentence, what is the expected result (quantifiable if possible)?")
```

If user picks "No match — I will add details", update `actual`/`repro` from their response and re-run pure repro before proceeding.

### Step 3: Write debug contract

After Clarify ①, output this block in your assistant message (for Phase D/E):

```markdown
## 调试契约
- expected: ...
- actual: ...
- repro_output: ...    # execute_code stdout summary
- scope: ...
- rpc_plan: (none yet) # filled when a write/mutate external step is planned
```

**Gate:** Do not write Phase E until Clarify ① has a `user_response`. Read-only external probes (`SELECT`, `GET`, `read_file`) may proceed immediately after Clarify ① without Clarify ②.

---

## Phase C3 — Clarify ② — External Writes Only (conditional)

**Trigger only when the next step is a write or state change** — not for read-only investigation.

| Needs Clarify ② | Does NOT need Clarify ② |
|-------------------|-------------------------|
| DB `INSERT` / `UPDATE` / `DELETE` / DDL | DB `SELECT` |
| HTTP `POST` / `PUT` / `PATCH` / `DELETE` | HTTP `GET` / `HEAD` |
| Redis `SET` / `DEL`, MQ publish | Redis `GET`, read-only scan |
| `write_file` / `patch` (fix phase) | `read_file` / `search_files` |
| `terminal` restart, `rm`, deploy, mutating scripts | `terminal` cat/tail/`psql SELECT`/`curl GET` |
| MCP tools that mutate data | MCP read-only probes |
| `execute_code` importing mutating `hermes_tools` | `execute_code` with `hermes_tools.read_file` only |

**Read-only external probes (after Clarify ①):** run directly — e.g. `mcp_postgres_*` with SELECT, `terminal` + `psql -c 'SELECT ...'`, `curl -sS GET URL`. Document results in Phase D; no `rpc_plan` approval step required.

**Before a mutating step**, draft `rpc_plan` (tool/command, target table/URL, what will change).

**Template (writes only):**

```
clarify(
  question="About to execute mutating external operation:\n{rpc_plan}\n\nThis will CHANGE data or system state. Approve?",
  choices=["Approve", "Cancel — stay read-only", "Suggest alternative"]
)
```

| User choice | Action |
|-------------|--------|
| Approve | Run mutating MCP/terminal/RPC/patch; update debug contract `rpc_plan` |
| Cancel — stay read-only | Continue investigation with SELECT/GET/read_file only |
| Suggest alternative | Revise plan; re-clarify if still mutating |

**Gate:** No **mutating** `mcp_*`, mutating `terminal`, `write_file`, `patch`, or mutating `hermes_tools` until Clarify ② returns approval.

### MCP mapping table (customize per deployment)

| Dependency type | MCP server key (config) | Typical tools | Fallback without MCP |
|-----------------|-------------------------|---------------|----------------------|
| PostgreSQL | `postgres` | SQL query tools | `terminal`: `psql -c '...'` (read-only) |
| MySQL | `mysql` | SQL query tools | `terminal`: `mysql -e '...'` |
| HTTP/REST API | `fetch` or custom | GET resource | `terminal`: `curl -sS URL` |
| Redis | `redis` | get/key scan | `terminal`: `redis-cli GET key` |
| GitHub / issues | `github` | issue/PR search | `terminal`: `gh api ...` |
| Files outside repo | `filesystem` | read/list | `read_file` / `terminal` |

If no MCP server matches, use read-only `terminal` probes (`psql SELECT`, `curl GET`) without Clarify ②; document the MCP gap.

### RPC execute_code (read vs write)

**Read-only RPC scripts** — no Clarify ② (after Clarify ①):

```python
from hermes_tools import read_file

content = read_file("/opt/app/config.yaml")
print("config snippet:", content[:200])
```

**Mutating RPC scripts** — Clarify ② required first:

```python
from hermes_tools import write_file, terminal  # mutating — needs approval
```

MCP is **not** available inside `hermes_tools` sandbox — call `mcp_*` tools directly from the agent. Read-only MCP needs no Clarify ②; mutating MCP needs Clarify ②.

---

## Phase D — Verification Loop

For each hypothesis:

1. **Predict** what repro or MCP probe should show if hypothesis is true/false
2. **Execute** on remote (respect Clarify ①/② gates)
3. **Compare** output to `expected`
4. **Update** hypothesis or drill deeper (narrower function, earlier in pipeline)

**Delegation:** For isolated sub-problems, `delegate_task` with toolsets `debugging`, `file`, `code_execution` only. Do not nest delegates.

**Stop investigating** when you can answer: *which line/mechanism makes `actual` differ from `expected`*.

---

## Phase E — Root Cause Report

Output **exactly** this structure (fill all sections):

```markdown
## 目标差异
- **预期：** ...
- **实际：** ...
- **范围：** path / service / branch
- **复现输出：** ...   # repro_output from debug contract

## 根因
- **位置：** `file:line` / function name
- **机制：** one paragraph — why actual ≠ expected

## 证据
- **终端：** command + key output lines
- **复现脚本：** summary of pure execute_code/terminal run + printed values
- **MCP/RPC：** tool name + rpc_plan + summary (if used)

## 假设验证记录
| # | 假设 | 结果 | 结论 |
|---|------|------|------|
| 1 | ... | pass/fail | ... |

## 修复建议（需用户确认后再执行）
- Optional patch direction — do NOT apply unless user asks
```

After the report, ask whether the user wants a fix implemented.

---

## Walkthrough A — Pure internal (off-by-one)

**User:**

```
/remote-ai-debugger 预期: add(2,2)==4 实际: 输出 5 路径: /tmp repro: python3 /tmp/repro_bug.py
```

**Expected tool sequence:**

| Step | Tool | Notes |
|------|------|-------|
| 1 | Phase A | Parse fields — **no clarify** |
| 2 | `read_file` or `terminal` | Read `/tmp/repro_bug.py` |
| 3 | `execute_code` | Pure Python extract `add()`, mock inputs, print result — **no hermes_tools** |
| 4 | `clarify` ① | Show `result: 5`, ask if matches actual |
| 5 | Phase E | Report: extra `+ 1` in function — **no patch** |

**Must NOT appear:** clarify at entry, Clarify ②, `mcp_*`, `import hermes_tools`.

---

## Walkthrough B — Mixed (Postgres order status)

**User:**

```
/remote-ai-debugger 预期: 订单 paid 实际: pending 路径: /opt/shop 服务: order-api
```

**Expected tool sequence:**

| Step | Tool | Notes |
|------|------|-------|
| 1–2 | Phase A + B | Parse + logs/source via terminal/read_file |
| 3 | `execute_code` | Pure state-machine logic, mock DB return `pending` |
| 4 | `clarify` ① | Confirm repro matches user's actual |
| 5 | `mcp_*` or `terminal` | **No Clarify ②** — read-only `SELECT ... FROM orders WHERE id=...` |
| 6 | Phase D/E | Compare DB truth vs code branch |
| 7 | `clarify` ② | **Only if** proposing `UPDATE`/`INSERT` to test hypothesis or apply fix |

---

## Tool Priority

| Priority | Tool | Use for |
|----------|------|---------|
| 1 | `terminal` | Remote shell, logs, curl/psql fallback |
| 2 | `read_file` / `search_files` | Source and config on remote |
| 3 | `execute_code` (pure) | Minimal repro — **Clarify ① after every run** |
| 4 | `clarify` | ① after pure repro; ② before **writes/mutations** only |
| 5 | `mcp_*` | Read-only probes after Clarify ①; mutating calls after Clarify ② |
| 6 | `execute_code` (RPC) | Read-only `hermes_tools` after Clarify ①; mutating after Clarify ② |
| 7 | `delegate_task` | Large isolated sub-investigations only |

**Avoid:** `browser_*` unless debugging a web UI on remote. **Avoid:** `write_file`/`patch` until user approves fix.

---

## Windows Host Note

The user's PC may be Windows where local `execute_code` is disabled. With `remote-debugger` profile, **`execute_code` and `terminal` run on the SSH Linux host** — always confirm `terminal` backend is `ssh` before relying on Python repro.

Pure repro on SSH should show `tool_calls_made: 0`. RPC scripts with read-only `hermes_tools` may run after Clarify ① without Clarify ②; mutating RPC requires Clarify ② first.

---

## Related

Load `/systematic-debugging` mentally for multi-component failures and the "no fix before root cause" discipline.
