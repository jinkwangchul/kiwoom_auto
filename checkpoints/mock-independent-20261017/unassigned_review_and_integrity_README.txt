WIP PATCH ARCHIVE | 2026-10-17 | UNASSIGNED REVIEW, INTEGRITY, FORCE RESET
Non-executable incremental task-only patch. Do not cherry-pick onto clean HEAD.
Apply after previously recovered Mock work and all protected user WIP.
The two pre-existing source changes in gui_stock_register_window.py were preserved using
the original HEAD + previous three confirmed archived patches as the pre-task baseline.

Scope: 1) unassign operation-state residuals -> guarded Review quarantine;
2) startup read-only mismatch detection and block (NO auto-write without live participant owner);
3) missing 0162Z0 state/config/orders gets accurate diagnostic; no automatic file creation;
4) Stock Management valid Review writer bound to existing Market/Operation host;
5) Review forced reset purges same-stock Mock before central project removal;
6) regression for STARTED/AUTO/TRADING plus RUNNING and STOPPED.
Native isolated regression on 4 test modules: 124 OK, exit 0; AST 9 OK, diff check OK.
Real stocks 063440,130500,520099 remain RUNNING; Review transition still BLOCKED,
must verify actual GUI/process participant + account/Broker/Queue/single-writer evidence.
Old 0162Z0 unassigned/missing files is explicitly NOT auto-entered in Review
because existing collector contract excludes it; no files fabricated or removed.
Real GUI not restarted, no broker session or order action, no actual stock deleted.
Patch SHA256: f55cf08e1adc472b40bc4f048a3348710d707ce543375d2cd1c6ee5dd8e19b69
