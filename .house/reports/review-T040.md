# Review T040

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (5/40 today)
- **Base:** 471e51a16abbff75de3bed67f0c741334f6b2364
- **Head:** 1db8d2a271c6916a5acec8faafba506c1bde333f
- **Status:** ok
- **Blockers:** 0
- **Majors:** 3
- **Minors:** 0
- **Nits:** 0
- **Verdict:** Changes requested for output-file safety and lock ordering. Acceptance tests were not run under the review-only tool restriction.
- **Date:** 2026-11-20 16:14

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/data/generate.py:157 — A pre-existing `scenario.json` can list any file inside `--out`, including an unrelated `notes.txt`, and regeneration deletes it. Path containment does not establish that the generator owns the file.
MAJOR backend/cfo/data/generate.py:161 — Publication follows symlinked destination directories. If `--out/bank` points outside the output directory, regeneration overwrites `history.jsonl` at the symlink target.
MAJOR backend/cfo/data/load.py:85 — Seeding holds transaction row locks from the bank upserts before requesting invoice-number locks. A concurrent attachment for the same payment and invoice number takes those locks in reverse order, so PostgreSQL can abort either operation with a deadlock.
VERDICT: Changes requested for output-file safety and lock ordering. Acceptance tests were not run under the review-only tool restriction.
