# MASTER_SPEC Rule

`MASTER_SPEC` is the project source of truth.

## Rules

- Treat `MASTER_SPEC` as authoritative over casual notes.
- Prefer `CURRENT` sections or current-state documents over older historical details.
- Check relevant `UPDATE`, `CHANGELOG`, audit, and continuation documents before changing behavior.
- Do not overwrite `MASTER_SPEC` directly.
- Do not edit `MASTER_SPEC` unless the user explicitly asks for that exact edit.
- If implementation suggests a spec update, report the needed update separately.
- If code and spec conflict, do not guess. Report the conflict and the likely options.

## Spec Update Decision

When a task changes behavior, report whether `MASTER_SPEC` needs a future update. Do not perform that update unless approved.

