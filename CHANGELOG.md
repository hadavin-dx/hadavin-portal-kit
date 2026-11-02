# portal-kit changelog

Runtime-visible changes a vendoring demo must know about (the schema string stays `hadavin.demo-export/1`; minor 1 is DRAFT).

## Review round 4
- `kitsum` hashes raw bytes (no CRLF normalisation); demos add `vendor/portal-kit/** -text`. Re-vendor to get the new hashes.
- `kitsum` also reports never-vendored development files, files at the vendor root, a `vendor_dir` outside the demo and a malformed lock.
- `tools/vendor.py copy` is bound to the checkout's HEAD and a clean tree, wipes the destination and verifies the copy.
- Money-key words: plurals, digit suffixes, fused words and `amt` count; bare `value`, `account` and `cost` no longer do.
- The deny-list fold removes control and format characters and collapses whitespace; `ROLES_MARK` must end the summary.
- `kitcheck`: a shape-broken export no longer crashes the page check; a non-strict `report.json` is a problem under `--deploy`/`--require-report`.
