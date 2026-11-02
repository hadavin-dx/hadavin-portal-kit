# hadavin.demo-export/1: contract, minors and migration

Every demo exports into this one schema; the **kit console** and `kitcheck` read nothing else.
The schema string is `hadavin.demo-export/1`. Changes arrive as additive **minors** (`schema_minor`, optional, absent
means 0). Code: `hadavin_kit/versioning.py`. Tests: `tests/test_versioning.py`.

**The kit console is the demo console, not the client portal.** The client portal is a different product (a React app
in the core repository that reads core tables and an API, with step-up sign-in, per client). The portal reuses this
kit's vocabulary (the export schema's names for tiers, outcomes, terminal states, hard zeros) and the label rules of
`hadavin_kit/honest.py` (a scripted run is never green, a fixture says so, an assumed profile says so); it does not
reuse the console's page code. The proposed wording for the portal specification is kept by the maintainers
(kept by the maintainers).

## Status: minor 1 is DRAFT

What is proven: the schema, validator, console and `kitcheck` agree on **fixtures of five demos** that this kit's own
builder (`demo_only/examples/build_examples.py`) wrote. That is a consistency result, not evidence that the schema fits the demos'
real exporters (two of the example fields are computed with the same function the validator uses). Minor 1 stays draft,
and may change incompatibly (announced in the pull request), until a real demo exporter passes `kitcheck` on its own
output. A change that must break readers gets a new schema string (`/2`).

**Per-demo gate.** Each demo's pull request must run
`python -m hadavin_kit.kitcheck <its export dir> --demo <slug> --kit-lock vendor/KIT.lock --expect-head <pinned chain head>`
on the output of its real exporter in its CI and be green (without `--kit-lock` a tampered vendored kit passes with a
note; without `--expect-head` a truncated chain does). Until a demo does, nothing here says the schema serves it.

Features with no producer yet are **reserved** (optional; they can change while minor 1 is draft); the list is generated
below.

## Vendoring: how a demo (or the portal) uses the kit

The kit is a core-style module that each consumer **vendors** with a hash file, never an unpinned copy.

1. Vendor only from a **merged** kit commit on main (a SHA from a branch vanishes on squash-merge):
   `python tools/vendor.py copy <demo repository> --commit <40-hex merged commit>`. It copies the runtime files into `vendor/portal-kit/`
   (`hadavin_kit/*.py` except `docgen.py` and `testing.py`, `schema/demo-export.v1.json`, `console/`), keeps the layout
   (the tree is one unit: the package, `schema/` and `console/` side by side; a copy of the package alone fails with
   "the vendored kit is incomplete"), and writes `vendor/KIT.lock` (JSON: `kit_commit`, `vendor_dir`, `files`, `tree_sha256` = sha256 of the sorted `<sha>  <path>` lines; plus `schema_minor_max` and `requires`), the file format the studio's `tools/core_sync_check.py` reads (`KIT-FORMAT.md`). The lock holds: the commit, a SHA-256 per file over the
   RAW bytes, a tree hash, the maximum schema minor and the minimum pins. `tests/` and `tools/` are not vendored.
   `copy` is bound to the bytes it copies: it refuses unless the checkout's HEAD is `--commit`, the kit directory has no
   uncommitted or untracked change and the commit is an ancestor of `origin/main` (outside a git checkout it refuses);
   it hashes the SOURCE files, wipes the destination first so no stale file survives a re-vendor, and runs the check on
   the result. There is no command-line flag to skip this; tests set `HADAVIN_VENDOR_ALLOW_UNMERGED=1`.
2. `python -m hadavin_kit.kitsum --check <dir>` fails on a changed, missing or extra runtime file. The kit's own
   repository keeps `KIT-SHA256SUMS` (sha256sum lines, the same hashes without a commit) and a test fails when it is stale: regenerate with
   `python tools/vendor.py manifest` after any runtime change.
   **Line endings.** Hashes are over the raw bytes, with no newline normalisation (the vendoring format of the demos' sync check). A demo repository therefore needs `vendor/portal-kit/** -text` in its
   `.gitattributes`; without it a CRLF checkout changes the bytes and the check reports `changed since it was vendored`.
   In this repository the runtime files are pinned to LF by `.gitattributes`.
   **Covered set (a recorded deviation).** The vendoring format lists `demo_only/fixtures/compat-*` and the kit's `pyproject.toml` as
   covered; the kit's sums and lock cover only the runtime files (the list above) because the frozen fixtures and the
   pyproject are not vendored. Both sides use the same set, so the checker reports nothing; the format text should say "the runtime set".
   **What `kitsum --check` proves.** That the vendored tree equals the lock: no changed, missing or unlisted file anywhere
   under the vendor directory (the never-vendored `docgen.py`, `testing.py` and `asks.v1.json` included), `vendor_dir`
   inside the demo and not a link, and a lock that is well formed. It does NOT prove the lock is the kit's: a demo that
   edits vendored files and regenerates its own lock passes `kitsum` and `kitcheck --kit-lock`. Only
   `core_sync_check --kit <kit checkout at kit_commit>` compares the lock with the kit's `KIT-SHA256SUMS`. `kitcheck
   --kit-lock` also reports a set `HADAVIN_KIT_ROOT` (the data tree can then differ from the imported code).
3. The demo's export names the vendored commit in `demo.kit_commit`; `kitcheck --kit-lock KIT.lock` fails when it is
   different, `uncommitted`, or shorter than 40 hex (the schema still accepts those spellings so fixtures validate; the
   check is what a demo's CI and a deploy enforce). `kitcheck --deploy` additionally needs `--expect-head` and the raw
   report.
4. Minimum pins the runtime imports: `hadavin-sdk` (for `evalcore`: `hadavin_sdk.stats.upper_error_bound`) at the commit
   in `portal-kit`'s pyproject, and `jsonschema`. The lock records them under `requires`; a demo pins a core at or above
   the kit's, and when the kit's pin moves the demo re-pins its core in the same pull request.
5. Imports of third-party code are confined to `validate.py` (jsonschema) and `evalcore.py` (hadavin_sdk); no other
   runtime module imports a vendor library.

## Bumping the kit minor (checklist, per demo)

1. Re-vendor from the merged commit that carries the new minor (`vendor.py copy`), commit `KIT.lock` with the files.
2. Re-pin the core if `requires` moved.
3. Rebuild the console page: `kitcheck` compares the page with what the kit builds, and the script hash changes with
   every kit commit that touches `console/`.
4. Re-export: set `schema_minor`, `demo.kit_commit`, and the new fields; the audit chain head does not change because of
   the kit commit (it is outside the hash).
5. Run `python tools/check_fixture.py <slug>` for the demo's fixture and
   `kitcheck --demo <slug> --kit-lock vendor/KIT.lock --expect-head <head>` on the real export in the demo's CI. Also run
   `python tools/core_sync_check.py --kit <kit checkout>` in the demo's CI: check out the kit repository at the lock's
   `kit_commit` (`actions/checkout` with `repository: hadavin-dx/hadavin-demos`, `ref: <kit_commit>`, `path: kit-src`, a
   read-only token for the private repository) and pass `--kit kit-src/portal-kit`; without `--kit` only the lock's own
   consistency is checked. A shipped example export has `demo.kit_commit` "uncommitted" and fails `--kit-lock` by design.
6. A consumer vendored at an older minor refuses an export of a newer minor (`UNSUPPORTED_MINOR`): re-vendor the consumer
   first, then the demos, in that order.
The kit's own CI runs `kitcheck` on every fixture, one job per demo (a matrix), plus the frozen minor-zero copies.

## Freezing a minor

When minor N leaves draft (the first real demo exporter passes `kitcheck` at that minor), run
`python tools/freeze.py N`: it writes `demo_only/fixtures/compat-v1.N/` (the schema, the five example exports, the
green test double and SHA256SUMS), which is never edited afterwards. Every later schema is diffed (additive_diff)
against EVERY frozen minor, including minor 0, and every frozen export must keep validating; a test enforces both. The
exceptions listed in `ACCEPTED_TIGHTENINGS` belong to the unreleased minor 0 and must be empty once minor 1 is frozen.
**The minor-1 freeze comes before any minor-2 pull request is merged**: minor 2 is built on a frozen minor 1, or it is
declared draft too. Until then minor 1 may still change, announced in the pull request.

## Freeze-time checklist for the reserved features

At the draft-to-frozen step, for each reserved feature (`ledger`, `load_profile`, `read_log`, `provider_terms`,
`segregation`, `sig_scheme`): (a) is there a real exporter that produces it? If not, remove it from the schema before the
freeze (a frozen unused feature is permanent); (b) are its validation rules, rendering and mutation results still wanted;
(c) does its wording claim only what the data proves (`sig_scheme` is a label; `load_profile` measured needs the raw
report). Record the decision per feature in the pull request that freezes the minor.

## Migration rules

1. A reader that supports minor N reads every export of minor 0 to N. An export declaring a higher minor is refused with
   `UNSUPPORTED_MINOR`; it is never guessed at. (An export of minor one is unreadable by a reader of minor zero: the compatibility
   is for readers upward, and every demo exporter must change its chain writer.)
2. A minor adds: optional keys, enum values, a new chain format. It does not remove or retype a key, make an old key
   required or narrow an enum. `versioning.additive_diff` checks this against the frozen minor-0 schema and also flags
   raised bounds, changed patterns, tightened `additionalProperties` and combinators it cannot prove only loosen. Eight
   reviewed exceptions are kept with reasons in `ACCEPTED_TIGHTENINGS` (five unprovable loosenings, three tightenings of
   the unreleased minor-0 shape); a test fails on anything else.
3. A key or enum value introduced in minor N may appear only in an export that declares `schema_minor >= N`
   (`MINOR_TOO_LOW` otherwise).
4. Rules that need a new field (a tenant on every record, `honesty.kind`, `hash-chain/2`) apply from the minor that
   introduced them. The general limits (document size, string length, nesting depth, unique hard-zero keys, the
   de-duplication of `confident_on_abstain`) apply to every minor; the minor-0 shape existed only on the unmerged branch.
5. The exports and schema of minor zero are frozen in `demo_only/fixtures/compat-v1.0/` with a checksum list; they must keep
   validating and their audit chains must keep verifying under the old recipe.
6. `migrate` never invents data. An export of minor zero has no tenant and a chain that does not cover one, so it stays at
   minor zero and the demo re-exports.

## The two audit chain formats, and what they do not prove

- `hash-chain/1` (minor 0): SHA-256 over the compact ASCII-escaped JSON array
  `[prev, seq, event_id, ts, type, actor, case_id, summary]`.
- `hash-chain/2` (minor 1): the same array with `tenant_id` appended. Required from minor 1.
Both are tamper-evident, not tamper-proof, and they cover **only the audit events**. **The export chain is the demo-side
copy** of the trail. The core audit chain (signed checkpoints and anchors) is a different, stronger object, and the export
does not carry a pointer to it, so a client page must never say "tamper-evident" without "export copy"; the console says
"Export chain only" on the run page. A minor-2 candidate is an optional `audit.core_anchor` (`checkpoint_id`,
`head_sha256`, `signer_kind`) that names the core checkpoint the export was cut from; until it exists, tamper-evidence
claims about the audit rest on the core chain and on a head pinned elsewhere (`kitcheck --expect-head`), not on this file.
 Cutting events off the end, or
re-sealing the whole chain, still verifies "N of N events match"; pin the head hash somewhere else and pass it to
`kitcheck --expect-head`. Cases, approvals, adapter calls, the scorecard and the ledger are not covered, so a `tenant_id`
on those records is an unauthenticated claim (only the tenant on the audit events is hashed). `audit.verify` in the
export is recomputed by every reader and adds no trust. The first `prev` is 64 zeros.

## What the console may say (honest status)

`hadavin_kit/honest.py` is the reference; `console/app.js` computes the same ids into `data-honest` and a browser test
compares them for every example.

- A scripted run is never green and never "promising": the gate result is shown as "amber (plumbing only)", the heading
  says plumbing check, every statistic block carries the caveat, and the sentence "scripted; gate vs gold agreement; not
  model accuracy" is in the banner of every page.
- Anything not marked `honesty.kind: run` is a fixture and says so on every page.
- An assumed load profile, a missing provider no-training record and a canary test that did not run are shown as such,
  never as a pass. A ledger gap, duplicated effect or foreign canary is shown in red on every page.
- `sig_scheme` is a label, not verification: the export carries no key id, public key or signed payload hash. HMAC
  signatures are labelled as forgeable by a key holder.
- `approvers[]` is outside the audit hash. `roles_at_decision` is therefore tied to the hashed approval events by a strict
  grammar: the approval event whose actor is `human:<approver role>` ends its summary with `[roles=a,b]`; the check runs
  per case, per approver and in both directions (a declared set nobody states, or a stated set nobody declares, is
  refused; a longer word or another approver's event cannot satisfy it; the mark must end the summary exactly). Scope: a
  declared set is satisfied by ANY approval event of that role in the case that states it, not by one event tied to that
  approval id, so two approval rows of one role with the same set can both be satisfied by one event. The check shows
  that the hashed trail says what the approver rows say; it does not show who sat behind the actor.
- A name is a **money key** when a word of it is a money word (plural and digit suffixes dropped, a long money word
  contained in a fused word, a short one fused with a currency code): `amounts`, `fee_2`, `totalusd`, `refundamount`,
  `amt`, `feeusd`. Bare `value`, `account` and `cost` are not money words (`account_id` is not money). It is a backstop;
  registry risk `money` is the primary control.
- `not_demonstrated` is shown in a neutral tone (it is a statement about evidence, not an alarm); red is kept for
  breaches, a failing chain and a ledger gap.
- A scripted run carries `run.recorded_turns_sha256` (from `scripted_llm.recorded_turns_sha256`), required from minor 1
  and forbidden for live runs: a scripted model cannot be passed off as a live one by renaming it. `kitcheck` cannot
  recompute the hash without the recorded files; the demo's own test must.
- `n_answered` counts items expected to get a definite response (an answer, a no-action or a refusal);
  `false_abstain.n_should_answer` counts items that should have been acted on (act or no_action).
- `hard_zero_exposure`: exposed = cases where the opportunity for that failure occurred or was attempted; of = cases at
  risk; `hard_zeros` counts the breaches. breaches <= exposed <= of <= n. It is internally consistent, NOT independently
  derived: without `of_cases`, `of` must equal the number of cases and `exposed` is the demo's own assertion (only bounded
  by the counts); with `of_cases` and `exposed_cases` the validator counts the listed cases and every breached case must
  be among the exposed ones. An inflated `exposed` lowers the apparent breach rate, so a reader weighs it as a claim. `scorecard.steered.exposure_key` names the exposure the steered replays probe.

## Validation and typed errors

`hadavin_kit/validate.py` returns problems with a code: `UNKNOWN_KEY`, `WRONG_TYPE`, `MISSING_KEY`, `BAD_VALUE`,
`OVERSIZE`, `CONFUSABLE_ID` (non-ASCII or case-colliding identifiers, and non-ASCII keys in free-form objects such as
`args`), `BAD_CHARACTER` (control, bidi, zero-width), `UNSUPPORTED_MINOR`, `MINOR_TOO_LOW`, `CHAIN`, `RULE`, plus the
`kitcheck` codes (`UNEXPECTED_FILE`, `MISSING_FILE`, `PAGE_MISMATCH`, `REPORT_MISMATCH`, `DENYLIST_HIT`, `KIT_LOCK`). Strings over the field limit and documents over the size limit are `OVERSIZE`. Cross-field rules run
only after the shape passed, so a document with shape errors reports those first. Paths in messages are escaped with
`ascii()`; they never echo raw control characters.

## Conformance kit

    python -m hadavin_kit.kitcheck <export-dir> --demo <slug> [--deny-list terms.txt] [--require-report] [--expect-head HEX] [--kit-lock KIT.lock] [--deploy] [--json]

The directory holds `export.json`, optionally the built console (`index.html` and `_headers`) and the raw load report
(`report.json`); nothing else is allowed. It checks file hygiene (no symlinks, sub-directories, weights, indexes,
databases, archives or notebooks); the export; the chain (and its head against `--expect-head`); the **whole page**
against the page the kit builds from `export.json` (skeleton, banner, navigation and text included, so a hand edit is
found), plus the exact CSP, inline script and style, `_headers` and the embedded data type for type; remote references
are looked for in the skeleton, scripts and styles only (a URL in a case summary is data); the report hash; the kit lock
(`--kit-lock`: `demo.kit_commit` must be the full vendored commit, and the running kit must match the lock); and a
deny-list of other clients' names and canaries, matched (NFKC, case folded, control and format characters such as zero-width space and soft hyphen removed, whitespace collapsed) against the decoded strings and keys of
every `.json` file (export and report) and of the page's embedded data, as well as the raw text, with a UTF-8 BOM
tolerated and a note when the list has no effective term. A `report.json` that is not strict JSON (duplicate key, NaN, too
deep, not UTF-8) is a problem under `--deploy` or `--require-report` and a note otherwise. The green-state test double fixture is refused: that is a guard
against accident (the check is a string match and can be renamed), not a security control. A report is matched by sha256
only; its contents are not compared with the ledger counts. What it could not check is printed as a note, not a pass;
for example, when the export is too malformed to read, the honest status and chain head are reported as "not computed"
in a note and the problems say why. `--deploy` requires `--expect-head`, `--kit-lock` and the raw report. Exit codes: 0
conforms, 1 problems, 2 unreadable or a kit copy that is incomplete. A demo's own tests can use `hadavin_kit.testing`
(development helper, not vendored).

## Mutation harness

    python tools/mutation.py [--all | --limit N] [--seed S] [--workers W]

First it runs the test subset on the unmutated source and stops if that is not green (the baseline assertion). Then it
proves the harness can see: a deliberate break must be killed and a no-op must survive. Then each mutant of the
validator, rules, chain, strict loader, problem codes, evalcore, text helpers, vendoring check, `tools/vendor.py` (the
copy tool), `console/build_console.py` and the JSON schema must be killed. A mutant counts as killed only when pytest exits with code 1; any other exit code
(collection error, internal error, timeout) is inconclusive and fails the run. Survivors fail the run unless listed in
`mutation_equivalents.json` with a reason.

Operators: comparison, boundary literal, and/or, `not`, returned booleans, deleted rule lines, one element dropped from a
literal tuple/list/set of constants, one alternative dropped from a regular expression, one directive dropped from a
Content-Security-Policy string; for the schema: closed objects opened, a required key dropped, an enum value dropped, a
pattern dropped (and every limit, through the approval file `approvals/schema-facts.json`).

**Not mutated, and why:** `console/chain.js` and `console/app.js` (no JavaScript mutation tool is installed; they are
covered by the node parity tests and the browser tests, whose gaps are the page text and layout), `docgen.py` and
`testing.py` (development helpers, not vendored), `scripted_llm.py` and `pytest_plugin.py` (helpers for the demos' own
suites). The result is "every mutant of these targets with these operators killed by these tests", not "mutation-clean".
CI runs a sample with a seed that changes every run; **the release gate is `--all`** (manual dispatch or run locally).

## Minor-2 candidates (from the incident demo and the portal; not built, owner: the incident-demo maintainers)

`audit.core_anchor` (above); an event type `opened` with an ordering rule (sent <= opened <= decided) and anchored
grammars for the outcome words in `clear` summaries (the same class as the roles grammar: anchored per event type, with
hostile-summary tests); `scorecard.floor`; a risk class `ops_write`; typed links from `facts` to a case and a source
(agent or person); an incident identity that is stable across exports with a recurrence key and an export index; a
verify-evidence reference for a fix marked verified (`lifecycle` stays a free slug; the incident vocabulary is verified,
not_recovered, rolled_back, rollback_failed); an "as of" time and a staleness marker on the export; portal-side items
that are not export fields (filter state in the URL, paging, CSV export, a "too few runs to judge" floor). Accessibility:
the kit makes no claim; the checks a portal would need (an automated axe run, contrast, a narrow-width test, light and
dark screenshots) do not exist yet.

<!-- generated:begin -->
<!-- Do not edit by hand: python -m hadavin_kit.docgen -->

## Counts (generated)

- Asks collected: 55 (29 added in minor 1, 16 already in 1.0, 4 fixed in the kit, no field, 4 demo side, no field, 2 core issue, no field).
- Features added in minor 1: 45; current maximum minor: 1.
- Minor 1 status: draft. Reserved (no real producer yet): `root.ledger`, `root.load_profile`, `root.read_log`, `root.provider_terms`, `root.segregation`, `approver.sig_scheme`.
- Reviewed schema changes kept despite `additive_diff` flags: 8.
- Typed problem codes: 18 (NOT_JSON, UNKNOWN_KEY, WRONG_TYPE, MISSING_KEY, BAD_VALUE, OVERSIZE, CONFUSABLE_ID, BAD_CHARACTER, UNSUPPORTED_MINOR, MINOR_TOO_LOW, CHAIN, RULE, UNEXPECTED_FILE, MISSING_FILE, PAGE_MISMATCH, REPORT_MISMATCH, KIT_LOCK, DENYLIST_HIT).
- Example exports (all minor 1):
  - refunds: 16 cases, 12 approvals, 56 audit events, verdict not_demonstrated, honesty.kind fixture.
  - apexc: 11 cases, 9 approvals, 40 audit events, verdict not_demonstrated, honesty.kind fixture.
  - intake: 25 cases, 20 approvals, 71 audit events, verdict amber, honesty.kind fixture.
  - dealdesk: 6 cases, 3 approvals, 14 audit events, verdict not_demonstrated, honesty.kind fixture.
  - askdata: 32 cases, 0 approvals, 64 audit events, verdict amber, honesty.kind fixture.

## All schema asks (generated)

| Id | Demo | Ask | Status | Resolution |
|---|---|---|---|---|
| RF-1 | refunds | scorecard.steered {x, n, label}: steered recorded turns the real pre-gate denied | added in minor 1 | Added; label is a constant string so it can never read as model behaviour. |
| RF-2 | refunds | Idempotency key excludes the model-chosen reason id | demo side, no field | No schema change: the key stays a hex64; the formula is the demo's (maintainer decision 2026-11-01). |
| RF-3 | refunds | approvals[].lifecycle: the demo's real state (step1_approved, executed, void) | added in minor 1 | Added as a slug; status stays the four-value export enum, void maps to expired. |
| RF-4 | refunds | Scripted runs are capped at amber and print 'scripted; gate vs gold agreement; not model accuracy' | already in 1.0 | Validator already forbids green on scripted; console now prints the sentence (scorecard page). |
| RF-5 | refunds | case.tier null for refused and abstained cases | already in 1.0 | case.tier already allows null/none; rule: none/null with an approvals row is rejected. |
| RF-6 | refunds | Reservation view needs no new field | already in 1.0 | Confirmed: no field. |
| RF-7 | refunds | cases[].queue: the hand-back queue of the 14 HandBackReason values | added in minor 1 | Added as a slug. |
| RF-8 | refunds | HZ-ABSTAIN maps to confident_on_abstain, no double count | fixed in the kit, no field | Class fix: derived_flags (validator) and run_flags (evalcore) dedupe. Hard-zero keys may now be upper case (HZ-04) and are case-insensitively unique. |
| RF-9 | refunds | scorecard.scripted and run.mode must agree; kit validates it | already in 1.0 | Already validated (scripted truth rules). |
| IN-1 | intake | case.outcome no_action for a matched invoice; counts as answered | already in 1.0 | Outcome exists in 1.0. Gold side: expected.outcome no_action is added so the gold can say it (minor 1). |
| IN-2 | intake | expected.outcome no_action | added in minor 1 | False abstain is owed on act and no_action only: abstaining where a definite answer was owed. A refuse case cannot be falsely abstained from. n_answered = act + no_action + refuse (unchanged for demos with no no_action gold). Intake's proposal is amended accordingly. |
| IN-3 | intake | Refused instruction-only message: outcome blocked, one deny gate step, no approvals row | already in 1.0 | Expressible in 1.0 (blocked, steps, deny event). expected.denied_calls added to carry the tool names the gold expects to be denied. |
| IN-4 | intake | Nullable case tier for the gold tier; export never contains auto | already in 1.0 | case.tier and expected.tier exist; auto-on-money is rejected. |
| IN-5 | intake | cases[].exceptions [{code, rule_id}]; expected.exceptions, actions, tier, verdict, denied_calls | added in minor 1 | exceptions, expected.exceptions/actions/tier exist in 1.0; verdict and denied_calls added. |
| IN-6 | intake | Four-eyes as ONE approval with an ordered approvers array | already in 1.0 | Already the contract. |
| IN-7 | intake | approvals[].rule_id is the single tier-deciding rule; others as evidence | already in 1.0 | Already the contract. |
| IN-8 | intake | approvals[].executed nullable; port draft_store; never an ERP port | already in 1.0 | Nullable in 1.0; the port rule is the demo's check_export. |
| IN-9 | intake | adapter_calls[].outcome conflict (same key, different content); read_ok | added in minor 1 | conflict added; read_ok already exists. Rule: at most one applied per idempotency key (minor 1). |
| IN-10 | intake | n_answered / n_refused / false_abstain.n_should_answer same set | added in minor 1 | Resolved in IN-2: n_answered = act + no_action + refuse; n_should_answer = act + no_action. |
| IN-11 | intake | step.source_value / model_value | already in 1.0 | Exist in 1.0. |
| IN-12 | intake | hard_zeros as {count, exposure} | added in minor 1 | hard_zeros stays integer counts (breaches); the new hard_zero_exposure map {key: {exposed, of}} is additive. exposed = cases where the opportunity occurred or was attempted, of = cases at risk. Rule: breaches <= exposed <= of <= n, so '1 exposed, 0 breached' (the gate stopped it) is reportable. The earlier rule that exposed is zero exactly when the count is zero was wrong and is removed. Internally consistent, not independently derived: without of_cases, `of` must equal n and `exposed` is the demo's own assertion; with of_cases and exposed_cases the validator counts them, and every breached case must be among the exposed ones. scorecard.steered may name the exposure it probes (exposure_key). |
| IN-13 | intake | Input envelope not exported; the model_saw text after redaction instead | already in 1.0 | step.model_saw exists in 1.0. |
| IN-14 | intake | Injectable clock; timestamps = as_of + audit sequence seconds | demo side, no field | Demo-side; the kit only checks that timestamps are real calendar times. |
| IN-15 | intake | New codes X27, X28, X23 | already in 1.0 | Exceptions are free slugs; no schema change. |
| IN-16 | intake | cases[].terminal_state: processed / abstained / rejected (and dead_lettered) | added in minor 1 | Added as {state, reason}; reason required unless processed. dead_lettered needs outcome failed; abstained needs outcome abstained or held. |
| IN-17 | intake | tenant_id per case | added in minor 1 | Added on every record (see KT-1). |
| AP-1 | apexc | approvals[].tier human_nonmoney and controller_override | added in minor 1 | Added to the one tier enum. Rules: human_nonmoney on a money action is rejected; controller_override needs one eligible approver (no two-person rule). |
| AP-2 | apexc | case.outcome held | added in minor 1 | held counts as not answered (abstention precision/recall) but is excluded from false_abstain; scorecard.false_hold {x, n} reports it beside. Rule: false_hold must match the cases; a held case without false_hold is rejected. |
| AP-3 | apexc | approvals[].plan_step and excluded_user_ids | added in minor 1 | plan_step is a slug (the kit does not own the vocabulary). Rule: an excluded user may not appear among the approvers. |
| AP-4 | apexc | adapter_calls[].ticket_id for callbacks | added in minor 1 | Added as a slug. |
| AP-5 | apexc | audit event types hold, veto, attest, clear, handback; rows carry roles_at_decision | added in minor 1 | Event types added (chain recipe unchanged except tenant, see KT-1). roles_at_decision lives on approvers[], with the rule that the decision role is one of them. Because approvers[] is unauthenticated, the hashed approval event of each approver (actor human:<role>) must end its summary with [roles=a,b]; the rule is checked per case, per approver and in both directions (a declared set that no event states, or an event that states a set nobody declares, is refused). Fixtures include a one-person two-role approver and a two-approver case. |
| AP-6 | apexc | scorecard.false_hold; hard-zero keys HZ-01..HZ-17; verdict_text free; scripted capped at amber | added in minor 1 | Hard-zero keys use the new key pattern (ASCII, upper case allowed, case-insensitively unique). verdict_text stays free text. |
| AP-7 | apexc | honesty.notes pointer for HZ-15 (DOM test result is a kit artefact) | already in 1.0 | honesty.notes exists; no schema change. |
| DD-1 | dealdesk | Approver eligibility predicate | already in 1.0 | approvals[].eligibility exists in 1.0 and blocks approval/execution when not ok. |
| DD-2 | dealdesk | Amount-aware / tiered policy hook in authorize | core issue, no field | Core issue (hadavin-sdk); no export field. Filed in the core follow-ups. |
| DD-3 | dealdesk | Quorum / ordered multi-step chains with distinct approvers per step | added in minor 1 | Expressed with plan_step plus the ordered approvers array. |
| DD-4 | dealdesk | Business-scoped idempotency key with an epoch | demo side, no field | Key stays a hex64; the formula is the demo's. |
| DD-5 | dealdesk | data_class confidential raises request_approval to sign_off | core issue, no field | Core issue; the demo pins data_class internal (synthetic) and states it in honesty.notes. |
| DD-6 | dealdesk | Tenant isolation statement; Clock kind | added in minor 1 | tenant_id and segregation block added (KT-1, KT-8). Clock kind is core. |
| DD-7 | dealdesk | Asymmetric decision signatures (HMAC lets a verifier mint) | added in minor 1 | sig_scheme {hmac_shared_secret, ed25519, none} is a LABEL, not verification (no key id, public key or signed payload hash is carried); the console states the HMAC limit; sig_ok true with scheme none is rejected. ed25519 is reserved. |
| DD-8 | dealdesk | Approvals bind the terms and snapshot shown; second decision refused | added in minor 1 | approval.bound {plan_sha256, snapshot_sha256} added. First-decision-wins stays demo-side. |
| KT-1 | kit | tenant_id on every record | added in minor 1 | New chain format hash-chain/2 hashes tenant_id as a ninth element; hash-chain/1 is untouched and still verified for minor 0. From minor 1 every record carries the export's one tenant id and hash-chain/2 is required. |
| KT-2 | kit | Message ledger and receipt ids | added in minor 1 | ledger block {scope, received, by_state, non_terminal, missing, duplicated_effects, reconciliation_gap, report_sha256}; invariant received = sum(by_state) + non_terminal; scope export_cases is recounted from the cases; scope load_run names the raw report hash. The console shows a breach in red and never as a pass. |
| KT-3 | kit | Per-currency amounts | added in minor 1 | amounts [{kind, currency, minor (integer string), n}], one row per (kind, currency). |
| KT-4 | kit | Load profile label assumed/measured | added in minor 1 | load_profile {name, label, source, report_sha256}; measured needs the raw report hash and is rejected on a fixture. |
| KT-5 | kit | Read-log summary | added in minor 1 | read_log {n_reads, rows_total, by_purpose, log_sha256, client_reviewable}; totals must equal the per-purpose sums. |
| KT-6 | kit | Provider no-training setting and date, null until recorded | added in minor 1 | provider_terms {provider, no_training_setting, checked_on}; both null or both set; date not after the run; console shows NOT RECORDED when null. |
| KT-7 | kit | Segregation namespace and canary result | added in minor 1 | segregation {namespace, canary {ran, foreign_hits}}; the namespace must start with the tenant id; a canary that did not run is shown as not run. |
| KT-8 | kit | Schema minor and migration rules | added in minor 1 | Optional schema_minor 0..1; features need their minor; an unknown minor is refused; see versioning.py. Minor 1 is DRAFT until a real demo exporter passes kitcheck; features with no producer yet are listed as reserved. |
| KT-9 | kit | honesty.kind fixture/run required | already in 1.0 | Exists in 1.0; required from minor 1. |
| KT-10 | kit | Clean export: only export.json, console page, _headers, report.json; hygiene scan with a deny-list | fixed in the kit, no field | Not a schema field: kitcheck enforces the file allowlist, forbidden file types and a deny-list scan. |
| AD-1 | askdata | askdata has no approvals; its real metric ('result equals gold on both seed databases') lives only in free text | demo side, no field | Decided minimally: per-case `correct` plus hard_zero_defs (writes_executed, restricted_values_returned) carry the metric; the gold comparison stays in case.expected.checks and facts. No scorecard.metrics[] is added until the real exporter shows a metric that cannot be expressed that way. |
| KT-11 | kit | A scripted run carries the hash of the recorded turns it replayed | added in minor 1 | run.recorded_turns_sha256 (hex64), required for scripted runs from minor 1, forbidden for live runs; ScriptedLLM-side helper recorded_turns_sha256(dir). kitcheck cannot recompute it without the recorded files: the demo's own test must. |
| KT-12 | kit | Vendoring contract: KIT.lock, a per-file hash over the runtime files, a tree hash, a check and kitcheck --kit-lock | fixed in the kit, no field | tools/vendor.py (manifest, copy, verify), python -m hadavin_kit.kitsum --check, kitcheck --kit-lock/--deploy, KIT-SHA256SUMS kept current by a test, vendor/KIT.lock in the format core_sync_check verifies, runtime path-independent via hadavin_kit.paths. |
| KT-13 | kit | case_id is a plain slug, no bucket letter or three-digit cap | fixed in the kit, no field | A loosening (superset of the old pattern), recorded in ACCEPTED_TIGHTENINGS; the bucket stays its own field. |

## Conflicts and how they were resolved (generated)

- **IN-2** (intake): IN-1 says false_abstain.n_should_answer = act + no_action + refuse; RF-8 and the frozen contract say expected act only. Resolution: False abstain is owed on act and no_action only: abstaining where a definite answer was owed. A refuse case cannot be falsely abstained from. n_answered = act + no_action + refuse (unchanged for demos with no no_action gold). Intake's proposal is amended accordingly.
- **IN-10** (intake): See IN-2. Resolution: Resolved in IN-2: n_answered = act + no_action + refuse; n_should_answer = act + no_action.
- **IN-12** (intake): Changing hard_zeros from integer to object would break every existing export. Resolution: hard_zeros stays integer counts (breaches); the new hard_zero_exposure map {key: {exposed, of}} is additive. exposed = cases where the opportunity occurred or was attempted, of = cases at risk. Rule: breaches <= exposed <= of <= n, so '1 exposed, 0 breached' (the gate stopped it) is reportable. The earlier rule that exposed is zero exactly when the count is zero was wrong and is removed. Internally consistent, not independently derived: without of_cases, `of` must equal n and `exposed` is the demo's own assertion; with of_cases and exposed_cases the validator counts them, and every breached case must be among the exposed ones. scorecard.steered may name the exposure it probes (exposure_key).
- **AP-2** (apexc): held is a non-answer, but false_abstain must not be blended with false_hold. Resolution: held counts as not answered (abstention precision/recall) but is excluded from false_abstain; scorecard.false_hold {x, n} reports it beside. Rule: false_hold must match the cases; a held case without false_hold is rejected.
- **AP-3** (apexc): apexc fixes plan_step to resolution/plan_1/plan_2/clear; deal desk has single-step plans. Resolution: plan_step is a slug (the kit does not own the vocabulary). Rule: an excluded user may not appear among the approvers.
- **AP-5** (apexc): approvers[] (where the decision and its roles live) is outside the audit hash chain; only audit events are hashed, and the event recipe has no roles field. Resolution: Event types added (chain recipe unchanged except tenant, see KT-1). roles_at_decision lives on approvers[], with the rule that the decision role is one of them. Because approvers[] is unauthenticated, the hashed approval event of each approver (actor human:<role>) must end its summary with [roles=a,b]; the rule is checked per case, per approver and in both directions (a declared set that no event states, or an event that states a set nobody declares, is refused). Fixtures include a one-person two-role approver and a two-approver case.
- **AP-6** (apexc): The slug pattern forbids upper case, so HZ-01 was invalid as a key. Resolution: Hard-zero keys use the new key pattern (ASCII, upper case allowed, case-insensitively unique). verdict_text stays free text.
- **KT-1** (kit): Adding tenant_id to audit rows outside the hash would leave it unauthenticated; changing hash-chain/1 would break every pinned head. Resolution: New chain format hash-chain/2 hashes tenant_id as a ninth element; hash-chain/1 is untouched and still verified for minor 0. From minor 1 every record carries the export's one tenant id and hash-chain/2 is required.

## Features by minor (generated)

- Minor 1: `adapter_call.outcome=conflict`, `adapter_call.receipt_id`, `adapter_call.tenant_id`, `adapter_call.ticket_id`, `approval.bound`, `approval.excluded_user_ids`, `approval.lifecycle`, `approval.plan_step`, `approval.tenant_id`, `approval.tier=controller_override`, `approval.tier=human_nonmoney`, `approver.roles_at_decision`, `approver.sig_scheme`, `audit.chain=hash-chain/2`, `audit_event.tenant_id`, `audit_event.type=attest`, `audit_event.type=clear`, `audit_event.type=handback`, `audit_event.type=hold`, `audit_event.type=veto`, `case.outcome=held`, `case.queue`, `case.receipt_id`, `case.tenant_id`, `case.terminal_state`, `case.tier=controller_override`, `case.tier=human_nonmoney`, `expected.denied_calls`, `expected.outcome=no_action`, `expected.terminal_state`, `expected.tier=controller_override`, `expected.tier=human_nonmoney`, `expected.verdict`, `root.amounts`, `root.ledger`, `root.load_profile`, `root.provider_terms`, `root.read_log`, `root.schema_minor`, `root.segregation`, `root.tenant_id`, `run.recorded_turns_sha256`, `scorecard.false_hold`, `scorecard.hard_zero_exposure`, `scorecard.steered`

<!-- generated:end -->
