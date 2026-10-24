# Direct M3 Signal Evaluation Implementation Plan

**Goal:** Compare bounded calculation-only V5, production, and mock signals from verified direct M3 rows.

**Architecture:** A standalone read-only CLI verifies the cache sidecar and canonical rules, selects closed M3 bars with the rules-derived warmup, then calls each consumer independently. Its JSON reports computational differences and leaves lifecycle and live parity blocked.

**Constraints:** No M1 reads or conversion, runtime writes, orders, GUI restart, or normal-index changes. The existing M1 audit remains unchanged.

- [x] Add failing tests for direct M3 provenance, cutoff, cap, independent calls, and malformed source.
- [x] Implement the minimal standalone harness and run the focused tests, then the bounded Samsung sample.
- [x] Run adjacent direct SOR V5 and core regression suites in separate processes.
- [ ] Verify exact changed paths, make an isolated-index child commit of `dfa4cfae92cc3e9be8e19568e97634cee7d92be2`, and push the unique recovery branch if Git metadata allows it.
