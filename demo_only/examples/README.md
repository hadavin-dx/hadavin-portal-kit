# examples

Synthetic fixture exports of the five demos, written by `build_examples.py` (rendering fixtures, not results).
Each has `demo.kit_commit: "uncommitted"` because no vendored kit commit exists for a fixture, so
`kitcheck --kit-lock` fails on them BY DESIGN (`uncommitted is not a full 40-hex commit id`). A real demo sets
`demo.kit_commit` to the 40-hex commit it vendored and rebuilds its page before running the gate.
