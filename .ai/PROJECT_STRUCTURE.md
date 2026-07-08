# Project Structure Summary

This project is an automatic trading system with layered preview, runtime, queue, and broker boundaries.

## High-Level Flow

GUI -> Runtime -> Routine -> Execution -> Queue -> Broker/API

## Main Areas

- `gui*`: User interface windows, dialogs, and display flows. GUI should not be connected to real execution unless explicitly approved.
- `routines/`: Strategy and routine definitions. Routine rules are sensitive; `rules.json` must not be modified without explicit approval.
- `stocks/`: Stock-related data, metadata, and candidate handling where present.
- `runtime/`: Runtime state files such as queue, execution records, and locks. These are protected write targets.
- `logs/`: Runtime or diagnostic logs where present.
- `docs/`, audit files, and spec files: Project knowledge, design records, and safety boundary notes.
- Execution modules: Preview, readiness, approval, queue commit, dispatch, SendOrder, recorder, Chejan, and lifecycle boundary layers.
- Broker/API modules: Kiwoom and broker-facing adapters. Direct external calls require explicit gates and user approval.

## Safety Direction

Most execution layers should remain preview-only until a policy, approval gate, and explicit user command opens the next boundary.

This file is descriptive only. Do not use it as approval to modify project structure, runtime files, rules, GUI, queue, or broker/API connections.
