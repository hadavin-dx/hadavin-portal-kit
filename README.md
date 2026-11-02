# hadavin-portal-kit

The export schema every Hadavin demo writes, and everything that reads or checks it. Contract, vendoring, minors and migration rules: `CONTRACT.md`; modules that duplicate or depend on core code: `CORE-COPIES.md` (a table kept by the builder; the counts and ask tables in `CONTRACT.md` are generated from code).

- `schema/demo-export.v1.json`: `hadavin.demo-export/1` (JSON Schema 2020-12, unknown keys rejected), additive minors via `schema_minor`. `schema/asks.v1.json`: every demo's schema ask with its justification, conflict and resolution. `demo_only/fixtures/compat-v1.0/`: the frozen exports and schema of minor zero (checksummed; they must keep validating).
- `demo_only/examples/*.json`: synthetic fixture exports of the five demos, written by this kit's own builder `demo_only/examples/build_examples.py` (rendering fixtures, not results; they show the kit is consistent with itself, not that a demo's real exporter fits: that is each demo's own `kitcheck` gate, see CONTRACT.md).
- `hadavin_kit/validate.py`: the strict validator with typed errors (unknown keys, wrong types, missing keys, confusable ids, oversize fields, bad characters, minor and chain problems). `export_rules.py` and `rules_v11.py` hold the cross-field rules; `versioning.py` the minors and `additive_diff`; `honest.py` what the console may claim.
- `hadavin_kit/kitcheck.py`: the conformance kit a demo runs on its export directory (`python -m hadavin_kit.kitcheck <dir> --demo <slug>`): file hygiene, schema, chain, page and CSP hashes, report hash, deny-list. `testing.py`: helpers for a demo's own conformance tests.
- `hadavin_kit/scripted_llm.py` (replay `recorded/<case_id>/turn-N.json`, raises on a missing script, `model: scripted`, prompt hash drift check), `evalcore.py` (verdict, sealed gate, lock, compare, report; `Scorer` protocol), `chain.py` (hash-chain writer and verifier, formats 1 and 2), `pytest_plugin.py` (a socket opening fails the test; enable with `pytest_plugins = ["hadavin_kit.pytest_plugin"]` in the root conftest).
- `console/`: static console. `python console/build_console.py <export.json> -o index.html` validates the export and writes one self-contained page plus `_headers` (works from file://, no network, strict hash-based CSP, text nodes only). Open `index.html#/scorecard?theme=dark` to pick a page and theme; pages: approvals, run, scorecard, registry, controls.
- Vendoring: `tools/vendor.py` (manifest, copy into a demo with a `KIT.lock`, verify) and `hadavin_kit/kitsum.py` (`python -m hadavin_kit.kitsum --check <dir>`); `KIT-SHA256SUMS` is the kit's own manifest, kept current by a test. `hadavin_kit/paths.py` locates the schema and console relative to the package, so the runtime does not depend on this repository's layout; `textnorm.py` holds the shared text helpers. `tools/freeze.py` freezes a minor as `demo_only/fixtures/compat-v1.N/`; `tools/check_fixture.py` is what the CI matrix runs per demo.
- `tools/mutation.py`: the mutation harness (asserts the unmutated baseline is green and that the harness itself can see a break before it trusts a result). `hadavin_kit/docgen.py` regenerates the tables in `CONTRACT.md`.

Runtime dependencies: `jsonschema` and `hadavin-sdk` (only `evalcore.py` imports it); the pins are in the repository's pyproject and recorded in `KIT.lock`.

Everything is static-hostable and client-self-contained: no fetch from anywhere, no analytics, no real client data.

## Repository layout
- `hadavin_kit/`, `schema/`, `console/`: the product kit (the runtime files a demo vendors; `tools/vendor.py copy` writes them and a `KIT.lock` into the demo).
- `demo_only/`: sample exports and their builder, the frozen minor-zero fixtures and the green-state test double. Product code never reads this folder; only the test helper `hadavin_kit/testing.py`, the tools and the tests do. The runtime files never name it, and a test lists the few documentation, tooling and CI files that may.
- CI has a `cut` job that deletes `demo_only/` and still lints, tests and checks the kit; tests that need sample data skip with a marked reason.
- `approvals/`: the schema approval file that the tests compare the live schema with.
- Licence: all rights reserved, Hadavin.
