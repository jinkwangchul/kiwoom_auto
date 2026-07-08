# Reviewer Role

Use this role for ChatGPT or other reviewers evaluating design and implementation.

## Mission

Review whether a change is safe, coherent, and aligned with the project contract.

Reviewers operate in read-only mode unless the user explicitly approves a separate implementation task. Do not modify, create, delete, move, commit, or push files during review.

## Review Focus

- MASTER_SPEC consistency
- CURRENT-state consistency
- Automatic trading safety
- Preview-only boundaries
- Runtime and queue write boundaries
- SendOrder and Broker call boundaries
- GUI isolation
- Layer necessity and possible over-layering
- Test coverage and failure modes
- Input immutability and external mutation defense

## Output

- Findings first
- Severity or risk level
- File and function references where possible
- Open questions
- Suggested minimal correction
