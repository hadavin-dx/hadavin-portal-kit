# Kit modules that duplicate or depend on core code (promotion table)

Rule: generic code lives in the core; a copy in a demo or in the kit is temporary and needs a listed core change.

| Kit module | Origin | What it is | Core change to propose | Status |
|---|---|---|---|---|
| `hadavin_kit/evalcore.py` (about four hundred lines: lock, freeze, sealed gate, verdict, compare, report) | copied from the ask-your-data harness (`evalrun.py`, `evalset.py`, `compare_reports`); imports `hadavin_sdk.stats.upper_error_bound` already | domain-free evaluation gate behind a `Scorer` protocol | a generic `eval` module in `hadavin_sdk` (lock/freeze/sealed_gate/verdict/compare) with this kit's tests and the boundary tests in `tests/test_evalcore_gates.py` | core PR number: NOT YET OPENED (the maintainers open it from the drafted proposal; the number goes here). Until a number exists this copy is a temporary exception |
| `hadavin_kit/textnorm.py` (`fold`, `alnum`, `tokens`) | kit-local; replaces two `_fold` copies and `_norm` | fold = NFKC + casefold + control/format characters removed + whitespace collapsed; `alnum`; word tokens for names | NOT a drop-in swap for the core `guard/textnorm.py`: the core `fold(raw, *, casefold=False, collapse_ws=False)` has another signature and does not casefold by default, `squeeze` is not `alnum`, and `tokens` (camelCase word split, used for money keys and mirrored in `chain.js`) has no core equivalent. Core PR 49 and PR 50 both add `guard/textnorm.py` with different content, so which one lands is open. When one lands: the kit's `fold` becomes `core.fold(x, casefold=True, collapse_ws=True)` behind a test that compares them on the deny-list cases, `alnum` maps to `squeeze` if equal, and `tokens` stays in the kit. | not planned until PR 49 and PR 50 are resolved; does not block this PR |
| `hadavin_kit/chain.py` (hash-chain/1 and /2) | kit-only | reproducible-in-JavaScript hash chain over the exported audit events | none today: the core audit chain is a different object (signed checkpoints and anchors); the export chain is a demo-side copy. If the two should share a recipe, that is a core design change | diff against the core chain recorded here; no duplication today |
| `hadavin_kit/strict_json.py` | kit-only | strict loader (no NaN, no duplicate keys) | candidate for the core's shared loader if one exists | to check when the core guard lands |

Third-party imports: `jsonschema` only in `validate.py`; `hadavin_sdk` only in `evalcore.py`. A second backend for the
schema check (for example a hand-written validator) is not built; the trigger is a consumer that cannot install
jsonschema.
