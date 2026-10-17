MOCK STOCK RESET WARNING / LOCALIZED BUTTONS / 2026-10-17
PATCH-ONLY INCREMENTAL RECOVERY. NOT A STANDALONE EXECUTABLE TREE.

Applies AFTER task_only.patch from the recovery base checkpoint at commit 89e7891f79f5fa3aa927f40211ab3d754f0ab721.
The diff is normalized to UTF-8/LF for review and safe patch storage.
Do not apply directly to clean project HEAD or erase existing user WIP.

Scope: Only Mock-registered Stock Reset QMessageBox warning severity and Korean Yes/No display;
regression test verifies content, localized button text, No as default and escape, no deletion on No.
User-visible captions are NE and ANIYO, written in Python using Unicode escapes
U+B124 (NE) and U+C544 U+B2C8 U+C624 (ANIYO), to survive Windows shell encoding.
The warning mentions all current/past Mock data, central stock record reset, irreversible
deletion and non-recoverability. No Production routine/market data/order logic changed.
Native Python311-32 offscreen regression: 4 focused Mock stock reset tests PASS.
Live operator GUI, broker subscription and real stock deletion NOT executed.
Patch SHA-256: 4cdb87395f9cee63a1e2141d25be369bf41c4b3a5d06f3d93c77620ea7c78fba
