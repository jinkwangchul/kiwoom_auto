# Non-Negotiable Rules

These rules are absolute unless the user explicitly overrides them for a specific task.

- Do not delete files without explicit user approval.
- Do not move files without explicit user approval.
- Do not restructure directories without explicit user approval.
- Do not rewrite architecture based on assumptions.
- Do not implement guessed behavior.
- Do not modify `runtime/order_queue.json` unless the task explicitly authorizes queue write.
- Do not create or modify `runtime/order_executions.json` unless the task explicitly authorizes runtime execution write.
- Do not create or modify `runtime/order_locks.json` unless the task explicitly authorizes runtime lock write.
- Do not modify any `rules.json` unless the task explicitly authorizes rules updates.
- Do not call Queue Commit unless explicitly requested.
- Do not call Runtime Commit unless explicitly requested.
- Do not call SendOrder unless explicitly requested.
- Do not call Broker or Kiwoom APIs unless explicitly requested.
- Do not connect GUI flows unless explicitly requested.
- Do not introduce Chejan, lifecycle, position, balance, retry, or broker-dispatch behavior unless explicitly requested.
- Do not claim completion without tests or a clear explanation of why tests were not run.

