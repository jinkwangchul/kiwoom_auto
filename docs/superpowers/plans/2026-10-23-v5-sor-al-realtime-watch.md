# V5 native broker `_AL` realtime watch

Root cause: `KiwoomApi._on_receive_real_data` sends every `_AL` execution callback only to the Mock registration and returns. V5 currently receives broker `_AL` M1 TR snapshots on its observation lease; it has no native `_AL` realtime registration or independent parity evidence.

1. Give V5 an isolated screen lease for canonical six-digit codes mapped to broker `code_AL`, with session-bound, duplicate-free multicode registration and cleanup. Preserve the Mock callback independently.
2. Quarantine native callbacks in `MarketDataHost`. Compare a fully observed closed minute's native OHLC against the independently received broker `_AL` M1 TR for the same code, session, and minute. Reject gaps, malformed identities, stale epochs, and mismatch. Volume parity remains unverified until `_AL` FID 15 semantics are proved with live broker captures.
3. Only after parity pass, expose a V5-only forming M1 to the existing calculation and signal evaluation path. Keep TR polling and its fallback, historical bars, and marker behavior. Never feed production execution or Mock trading.
4. RED focused tests first, then implementation, focused tests, compilation, and adjacent V5/SOR tests. Offline tests establish control flow, not live broker parity.

Implementation note: FID 15 is signed per-trade quantity in the local Kiwoom realtime convention, but broker `_AL` volume equivalence to opt10080 has not been established by live captures. The gate therefore compares OHLC only. The admitted forming candle carries the last independently received `_AL` TR volume with `volume_provenance=LAST_BROKER_AL_TR_SNAPSHOT`; V5 updates are capped at one per ten seconds to bound the existing evaluator's work. A live broker session must verify callback delivery, `_AL` FID semantics, and TR/native parity before this path can be treated as proven in production observation.
