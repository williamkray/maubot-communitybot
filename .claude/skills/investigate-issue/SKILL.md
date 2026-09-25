---
name: investigate-issue
description: Investigate a GitHub issue against the codebase — fetch the issue, read relevant code, fetch protocol specs where needed, and form a validated RCA before planning a fix.
allowed-tools: Bash, Read, Grep, Glob, Agent, WebFetch
user-invokable: true
---

# Investigate Issue

Investigate an open GitHub issue, produce a validated root cause analysis, and prepare for a fix.

## Process

### Step 1 — Fetch the issue

```bash
gh issue view <N>
```

Note: the reporter's description and log output. Identify:
- The command/event that fails
- The room version(s) involved
- The bot's role (creator vs non-creator, PL)
- The expected vs actual behavior

### Step 1b — Triage the failure mode

Before reading code, determine *how* it fails:

- **Silent failure** (command runs, nothing happens, no error shown): check if exceptions are being swallowed. Look for broad `except Exception` blocks in the handler and decorators. Ask the user to check bot logs.
- **Visible error to user**: read the exact error text; it usually maps directly to a code path.
- **Matrix server rejection** (403, M_FORBIDDEN, M_UNKNOWN): the homeserver is refusing the state event — this is a protocol/permission issue, not a code logic bug.
- **Command not dispatched**: the handler never runs — check permission decorators and command registration.

For silent failures, always ask: *"Can you check the bot's server logs for any error or warning around the time you ran the command?"*

### Step 2 — Read the code before delegating

Read the relevant handler directly before spawning an Explore agent. For a command bug, `grep` for the command name and read the implementation (~50–100 lines around the handler). Form a hypothesis first.

### Step 3 — Fetch protocol specs if the bug touches Matrix protocol behavior

For any bug involving power levels, room versions, state events, or auth rules — fetch the relevant spec section **before** planning:

- Room v12 power levels: `https://spec.matrix.org/v1.18/rooms/v12/`
- Current room version index: `https://spec.matrix.org/v1.18/`
- Auth rules for a specific version: `https://spec.matrix.org/v1.18/rooms/v<N>/`

Do not assert Matrix protocol behavior from memory. The spec is the authority.

### Step 4 — Map all cases explicitly

For any logic that branches on room version or creator status, enumerate **all combinations** before writing code:

| Parent version | Child version | Bot is creator in parent | Bot is creator in child | Expected behavior |
|---|---|---|---|---|
| v12+ | v12+ | yes | yes | … |
| v12+ | v12+ | yes | no | … |
| v12+ | legacy | yes | — | … |
| v12+ | legacy | no | — | … |
| legacy | v12+ | — | yes | … |
| legacy | v12+ | — | no | … |
| legacy | legacy | — | — | … |

Walk through each row with a concrete example (e.g., "parent bot PL=100, child bot PL=1000") before writing any fix.

### Step 5 — Clarify intended behavior before planning

Ask the user about any case where intended behavior is ambiguous. Key questions for power level bugs:

- Should the bot's PL in child rooms be capped at its actual PL there, or should it attempt to match the parent?
- Is a demotion in a child room acceptable if the parent has a lower PL?
- Are there rooms where the bot intentionally has elevated PL that should be preserved?

### Step 6 — Validate the fix logic with examples

Before writing code, run through the proposed fix with concrete numbers for each case in the table above. Verify no case produces an illegal state (e.g., PL > current PL, creator in users dict).

### Step 7 — Run tests

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim sh -c \
  "pip install --quiet mautrix maubot asyncpg pytest pytest-asyncio && pytest tests/ -v"
```

Expected pre-existing failures (not regressions): see CLAUDE.md "Known pre-existing test failures".
