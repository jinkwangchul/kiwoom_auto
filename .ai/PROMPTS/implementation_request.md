# Implementation Request Prompt

Implementation requires explicit user approval.

Read-only planning task. Do not implement, modify, create, delete, move, commit, or push files. Report the approved scope, forbidden scope, minimal implementation plan, and required tests only.

Before implementation:

- Confirm approved files
- Confirm forbidden files
- Confirm runtime, queue, rules, GUI, SendOrder, Broker, and Kiwoom boundaries
- Inspect relevant code
- State the smallest safe change

During implementation:

- Modify only approved scope
- Avoid unrelated refactors
- Preserve preview-only safety flags when applicable
- Do not change runtime or queue files unless explicitly approved

After implementation:

- Run `py_compile` or relevant tests
- Report changed files
- Report test results
- Report protected file status
