# Direct M3 operational evidence

The next authorized V5 GUI session can append accepted Samsung `005930_AL` M3
normal replay and marking decisions to
`runtime/diagnostics/v5_sor_m3_operational.jsonl`. Their workers check the
direct M3 pool against its cache sidecar and encode at most 24 evaluations.
The GUI thread queues those bytes only after the matching request is accepted
and published. Stale, superseded, and failed publications write no record.
The base-only indicator view has no evaluated replay decision, so it supplies
no operational selection record. The file is capped at 4 MiB; a dropped or
missing record remains unavailable. The sidecar establishes cache consistency,
not independent broker merge proof.

`routine_signal_probe` automatically appends sanitized direct M3 calculation-only
results to `runtime/diagnostics/sor_m3_production_outputs.jsonl`. The bounded
background sink retains source hashes, receipt, cycle status and hash, a hashed
routine identity, side, and observation time, without account, position, or
order queue data. It drops evidence if the
queue is full, the file reaches 4 MiB, or I/O fails. An older manually captured
JSON array is also accepted. Newly appended, source-bound Mock Validation
`ROUTINE_EVALUATED` decisions are captured in
`runtime/diagnostics/sor_m3_mock_events.jsonl` by the same bounded background
writer. A missing direct M3 receipt, duplicate event, or failed write provides
no operational proof. The existing `mock_validation_event_v1` session journal
remains accepted for read-only comparison.
The Production result has `SKIP / SOR_CALCULATION_ONLY_NO_ORDER`; Mock uses its
existing `ROUTINE_EVALUATED` events, including a direct-M3 `NONE` decision after
a successful no-signal cycle. Both carry the direct source session receipt.
Production exposes only a hash of its cycle context.
Neither path enables a queue, order, broker registration, or scheduler.

Run the bounded, read-only comparison with three explicit files:

```powershell
python sor_m3_operational_evidence.py --v5 runtime/diagnostics/v5_sor_m3_operational.jsonl --production runtime/diagnostics/sor_m3_production_outputs.jsonl --mock runtime/diagnostics/sor_m3_mock_events.jsonl --cutoff 20261024090000
```

The collector requires the same M3 bar receipt, prefix hash, rules hash, and
cutoff in every origin. It reports the first differing BUY/SELL/NONE decision.
`MATCH` describes only the recorded selections; order/cycle status stays `UNKNOWN`
and overall status stays `BLOCKED`
until a V5 operational cycle link exists. Missing, stale, duplicate, or mixed
records also yield `BLOCKED`. The CLI reads at most 4 MiB and inspects the
latest 256 lines from each JSONL file. It
prints hashes and status only, without candle or account contents. Historical
M3 is accepted only from direct broker `_AL` rows; no completed M3 is made from
past M1.

The current runtime has no independently observed accepted V5 M3, Production
M3, and Mock M3 records. A missing V5 accepted file is reported as `BLOCKED`.
Offline tests and cache sidecars do not establish live parity. No GUI or broker
session should be started merely to fill this evidence gap.

## Direct-source chart frames

The same bounded collector accepts `--timeframe M5` (or M10, M15, M30,
M60, M120, M240) with the matching frame's V5, Production, and Mock files.
The default remains M3 with the original file names and schema. New direct
minute records use `v5_sor_direct_operational.jsonl`,
`sor_direct_production_outputs.jsonl`, and `sor_direct_mock_events.jsonl` in
`runtime/diagnostics`. A frame must be selected exactly; records for other
known minute frames are excluded, while a missing selected route blocks
comparison. The Production and Mock markers require that frame's
direct completed `_AL` rows, their source content identity, session/epoch
receipt, and canonical rule and row hashes. The V5 sidecar verifies cache
content and accepted replay only; it does not independently attest broker
receipt or establish live order/cycle parity. A matching selection therefore
continues to report overall `BLOCKED` and order/cycle status `UNKNOWN`.

M1 remains `BLOCKED`: its existing calculation marker has no direct completed
M1 session/epoch receipt and per-bar receipt hash required by this collector.
The forming M1/tick path is unchanged. D1, W1, MO1, and Y1 remain `BLOCKED`:
there is no proven direct SOR period-row receipt, period candle schema, or
participating routine marker for those frames. No higher-period decision is
inferred from minute bars. The other minute frames are code-eligible only;
accepted records from all three independent routes have not been observed in
a live authorized session.
