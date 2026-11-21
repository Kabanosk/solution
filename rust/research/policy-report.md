# Pure policy port checkpoint

Implemented `classify` and `context_from_transactions` in `rust/policy/src/port.rs`. Native entry points accept materialized JSON representations of PolicyAction/PolicyContext/Transaction/Rule. LLM-backed `parse_rule` remains unported; this crate is not a full `scout.policy` replacement.

The read-only `rust/policy-reference/contract/policy.py` adapter calls a byte-identical snapshot of the unchanged original pure policy prefix. Its import-time provenance check refuses stale source; only `__all__=[]` is appended to mark the snapshot private. Its JSON-string boundary permits rust-anything recording without mutating production types or sources. Invalid adapter inputs return stable sentinels; this does not claim full Pydantic constructor/default generation equivalence. Supply materialized Rule IDs and timestamps at native integration boundaries, as the original pure functions receive constructed models.

## Verification

- Original characterization: **1,615 cases recorded**, including **1,404 structured cases** and generated malformed/string traps.
- `rust-anything diff rust/policy`: **1,615/1,615 match**.
- Rust tests: **2 passed**; one replays the complete original corpus, one checks Unicode/Decimal boundaries.
- fmt, clippy with warnings denied, build, API, unsafe, lockfile, implementation completeness, panic check, error-case check and behavior gate checks all pass.
- **Overall rust-anything gate passes, all 12 checks**. Size accounting uses the actual source bundle: **538 Rust code lines / 175 Python source lines**, within the standard 3x+50 allowance. Raw result: `rust/policy/gate-report.json`. The earlier adapter-only size failure was resolved by re-bridging the actual hermetic source bundle, without suppressing or changing any gate check.

## Preserved details

Rule precedence including equal timestamps/list order, casefold/whitespace normalization, strategic actions, new-supplier rules, exact 1%/5% and +/-20% boundaries, unknown inflows, signed payments, reason strings, first counterparty spelling, median outflows and inclusive calendar-month spans. Decimal arithmetic explicitly follows precision 28 and HALF_EVEN, preserving preferred exponents and output scale. Python's Unicode 15.0.0 casefold data is captured in `unicode-casefold.json` to avoid silently adopting another Rust Unicode version.

Inputs are in `all-inputs.jsonl`; recordings are `cases.jsonl`; `source-provenance.json` pins the unchanged original modules and adapter/table. rust-anything's adapter freshness check alone does not notice changes in imported Python modules, so verify these hashes before reusing the proof.

This pure unit is **gate-complete**. It does not establish whole-app lift readiness. Remaining work: wire native operations into application consumers, characterize Pydantic validation/defaults separately, and port LLM rule parsing with the LLM/usage unit. The local rust-anything benchmark over the same 1,615 cases measured 19,945 ns/call original versus 9,851 ns/call Rust (2.02x), including bridge crossing. This is a local adapter microbenchmark, not whole-application performance.
