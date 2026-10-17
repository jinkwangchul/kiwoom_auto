PATCH-ONLY RECOVERY CHECKPOINT -- NOT AN EXECUTABLE SOURCE TREE

Base commit: 4579864b40243310a383b2f7503dcda57c7961d7
Source working branch: feat/restore-auto-trade-setting-ui
This checkpoint stores EXACT task-only unified diff hunks for 15 files, not the existing protected user WIP.
Mixed five files are dependent on pre-existing local WIP. A source-only cherry-pick onto base HEAD was attempted
with a temporary index but yielded Python syntax errors in gui_windows.py, kiwoom_api.py,
mock_validation_host.py and mock_validation_repository.py. DO NOT use this as runnable release code,
or blindly apply the zero-context patch to HEAD. To recover, first reconstruct the matching pre-task WIP,
apply task-only sections with manual anchored review, then verify target file SHA-256 values in manifest.json.
Existing user WIP not copied into this checkpoint. No Production/runtime/order data was modified.
Original feature branch and index are not changed by this recovery branch.
Live GUI and Broker stream behavior still require operator validation.
Offline tests on working tree: 69 Mock pure PASS; 82 Production separation PASS; 4 stock-reset PASS;
3 Mock detail-tooltip PASS. This patch-only tree was not independently executed.
Patch SHA256: e3301d1469150687b8ac8ab1ac524f519c734f08b8f2c537a69c9ce1551beeef
