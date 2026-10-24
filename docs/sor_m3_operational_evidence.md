# Direct M3 operational evidence

The running GUI does not load edits made after it started. Its next authorized
session can append accepted Samsung `005930_AL` M3 marking decisions to
`runtime/diagnostics/v5_sor_m3_operational.jsonl`. The marking worker checks the
selected pool against its cache sidecar before building at most 24 decisions;
the GUI thread only queues the already encoded records after an accepted publish.
The file is capped at 4 MiB. A dropped or missing record remains unavailable.
The sidecar establishes cache consistency, not independent broker merge proof.

Capture `routine_signal_probe` calculation-only result objects as a JSON list
and a Mock Validation `mock_validation_event_v1` session journal as JSON.
The Production result has `SKIP / SOR_CALCULATION_ONLY_NO_ORDER`; Mock uses its
existing `ROUTINE_EVALUATED` events, including a direct-M3 `NONE` decision after
a successful no-signal cycle. Both carry the direct source session receipt.
Production exposes only a hash of its cycle context.
Neither path enables a queue, order, broker registration, or scheduler.

Run the bounded, read-only comparison with three explicit files:

```powershell
python sor_m3_operational_evidence.py --v5 runtime/diagnostics/v5_sor_m3_operational.jsonl --production captured_probe_outputs.json --mock captured_mock_session.json --cutoff 20261024090000
```

The collector requires the same M3 bar receipt, prefix hash, rules hash, and
cutoff in every origin. It reports the first differing BUY/SELL/NONE decision.
`MATCH` describes only the recorded selections; overall status stays `BLOCKED`
until a V5 operational cycle link exists. Missing, stale, duplicate, or mixed
records also yield `BLOCKED`. The CLI reads at most 4 MiB and inspects the
latest 256 V5 lines. It
prints hashes and status only, without candle or account contents. Historical
M3 is accepted only from direct broker `_AL` rows; no completed M3 is made from
past M1.
