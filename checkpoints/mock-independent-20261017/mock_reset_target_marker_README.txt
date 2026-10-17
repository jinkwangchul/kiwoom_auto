MOCK STOCK RESET TARGET MARKER (2026-10-17)
RECOVERY TYPE: incremental PATCH ONLY, NOT A STANDALONE EXECUTABLE SOURCE TREE.
Apply after a6aa67114b340b8f0cfeb5523da00b28db697875 and other protected user WIP.
Normal and Mock stock reset FIRST confirmation now share the identical canonical warning,
and only the target line differs: normal = stock_code stock_name, Mock = MockValidation/stock_code stock_name.
The word MockValidation here denotes the rendered Korean prefix from Python Unicode escapes
U+BAA8 U+C758 U+AC80 U+C99D followed by '/'.
Other warning content, registration flags, Yes/No Korean buttons, cancellation defaults, and
both-stage stock reset execution gates are unchanged.
Native Python311-32 isolated tests: 4 PASS. Live GUI not verified. No actual orders or stock reset.
Patch sha256: 2e0b36a244d721316254c7980a7594d212632726ea29c2b7d854d4b71c08cacd
