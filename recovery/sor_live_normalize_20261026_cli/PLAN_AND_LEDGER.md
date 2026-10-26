# SOR/NXT live candle normalization — CLI task ledger

Goal: repair the common receiver path offline, and hand off V5 integration as an isolated candidate. Current working tree is authoritative. No live GUI, broker, order, runtime, stock, or canonical-document mutation.

Initial guards (2026-10-26, before mutation; SHA256):
- `gui_market_data_host.py`: `2D93328D92B9F8F651F1FE20F18285D9B0F572E88937A49EEEC2B5B2CD4EB7FB`
- `kiwoom_api.py`: `60EB9EB2C585254A1924C9EA8E423DD249ED7EC719226A15CB73589EE76B1495`
- `auto_candle_refresh.py`: `D05DF6E105864300AC11809DC52B898F23F9819E211177079D5025272026475E`
- original V5, read-only: `B73ABA2DE8FA5A27BB64C00510E607E91E4916A2B9B2FDC7A0D523CDDD013001`
- HEAD: `75296031aec709ef032e329db5c5af27a5d928e8`; branch: `feat/restore-auto-trade-setting-ui`; normal index and unrelated WIP protected.

Plan:
1. Copy byte-exact preimages into `preimage/`; inspect callers, broker conventions, tests, and source/session transitions. Recheck same-file hashes before every original-file edit.
2. In a narrow offline snapshot, write focused failing behavioral tests for NX native notification, selected-timeframe observation/M1 policy, and registration rollback. Write separate V5 candidate tests for target-bar freshness and frozen-plan transition.
3. Make minimum changes in allowed common files; keep direct native timeframe bars authoritative, bounded current-bar refreshes only, and no tick-created completed bars. Run focused tests, compilation, diff review, and hash guards.
4. Save exact patch and postimage hashes. Create scoped recovery pre/post commits with an isolated Git index and a separate branch; push without force only if safe and verify remote SHA. Leave original branch and normal index unchanged.
5. Hand off isolated V5 patch and concise policy replacement text. Mark real-server and live-GUI behavior `Must Verify`.

Ledger:
- [x] Initial HEAD, branch, dirty-tree, and four SHA256 guards recorded.
- [ ] Byte-exact preimages and current-source test snapshot created.
- [ ] RED offline reproductions captured.
- [ ] Common receiver fix GREEN and compiled; original V5 untouched.
- [ ] Isolated V5 candidate and tests, with handoff patch.
- [ ] Scoped pre/post recovery commits pushed and remote SHA verified.
- [ ] Final evidence and limitations reported.
