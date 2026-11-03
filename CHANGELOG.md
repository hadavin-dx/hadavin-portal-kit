# portal-kit changelog

Runtime-visible changes a vendoring demo must know about (the schema string stays `hadavin.demo-export/1`; minor 1 is DRAFT).

## Sonar fixes (path safety, 32-bit wrap, linear-time regexes)
Vendored files that change (re-vendor to get them): `hadavin_kit/paths.py`, `strict_json.py`, `kitcheck.py`, `kitsum.py`, `evalcore.py`, `scripted_llm.py`, `console/build_console.py`, `console/chain.js`.
- New in `hadavin_kit/paths.py`: `UnsafePath` (a `ValueError`), `unsafe_reason`, `inside`, `safe_join`, `cli_path`, `read_text_in`, `read_bytes_in`. Every path typed on a command line or read from a file's content now goes through them: no `..`, no drive, no absolute path outside the base, no symlink or junction leaving the base.
- BEHAVIOUR CHANGE: the command lines of `kitcheck`, `kitsum`, `build_console.py`, `tools/vendor.py` and `tools/mutation.py` refuse a path outside the current directory (exit 2, "unsafe path"). An absolute path is still accepted when it lies inside the current directory. Run them from the folder that holds the demo.
- `kitcheck.run` now reads `index.html`, `_headers`, `report.json`, the deny-scanned files and the lock through the helper (a link leaving the directory is treated as missing or reported; a file name that is not a safe name is noted, never a traceback). `kitsum` with no directory argument still checks the kit root wherever it is run from; a directory you type must be inside the current directory. `vendor.py copy DEST` needs DEST inside the current directory: `cd <demo>; python ../portal-kit/tools/vendor.py copy . --commit <sha>`. Refused: names ending in a dot or space, Windows device names (CON, NUL, COM1...), a colon in the file name of an absolute path.
- `kitsum`: a `KIT-SHA256SUMS` naming an unsafe path is a problem; a link that leaves the folder is reported (never hashed); `lock_shape_problems` and `vendor_dir_of` use the shared check.
- `evalcore.verify_files` reports a lock entry with an unsafe name as changed; `evalcore.freeze` and `scripted_llm.recorded_turns_sha256` refuse unsafe names and links that leave the folder.
- `console/chain.js`: the 32-bit wrap in `sha256` is written `>>> 0` instead of `| 0` (same hashes; pinned by tests).
- Not vendored: `hadavin_kit.docgen.main()` returns `None` (it always returned 0; failures are exceptions); `tools/mutation.py` samples with a hash order instead of a random generator (a given `--seed` now picks different mutants than before) and uses linear-time parsing.

## Review round 4
- `kitsum` hashes raw bytes (no CRLF normalisation); demos add `vendor/portal-kit/** -text`. Re-vendor to get the new hashes.
- `kitsum` also reports never-vendored development files, files at the vendor root, a `vendor_dir` outside the demo and a malformed lock.
- `tools/vendor.py copy` is bound to the checkout's HEAD and a clean tree, wipes the destination and verifies the copy.
- Money-key words: plurals, digit suffixes, fused words and `amt` count; bare `value`, `account` and `cost` no longer do.
- The deny-list fold removes control and format characters and collapses whitespace; `ROLES_MARK` must end the summary.
- `kitcheck`: a shape-broken export no longer crashes the page check; a non-strict `report.json` is a problem under `--deploy`/`--require-report`.
