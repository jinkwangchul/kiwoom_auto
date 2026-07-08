# AI Operating System

This directory defines the shared operating rules for Ollama, OpenCode, Codex, and ChatGPT when working on this project.

## Core Authority

- `MASTER_SPEC` is the highest project source of truth.
- When a `CURRENT` section or current-state document exists, prefer it over older historical notes.
- Before analysis or implementation, check relevant `UPDATE`, `CHANGELOG`, audit, or continuation notes when they exist.
- If project documents conflict, report the conflict instead of guessing.

## Operating Principles

- Automatic trading safety is the top priority.
- Prefer preview-only, dry-run, and explicit gate layers before any real write or external call.
- Do not modify, delete, move, or restructure files without explicit user approval.
- Do not connect GUI, Queue Commit, Runtime Commit, SendOrder, Broker, Kiwoom, Chejan, or lifecycle behavior unless the user explicitly asks for that exact connection.
- Keep changes scoped to the user-approved files and purpose.
- Preserve existing behavior unless the user explicitly requests a behavior change.
- Treat unknown runtime state as unsafe until verified.

## Required Work Habits

- Read the relevant files before making claims.
- Identify the requested scope and forbidden scope.
- If implementation is approved, make the smallest safe change.
- After code changes, run `py_compile` or relevant tests. Run broader tests when risk or scope justifies it.
- Never declare completion without reporting verification results.

