# Rust authentication primitives

Completed unit: the eight exports of `dashboard/lib/auth-crypto.ts`, through isolated characterization adapters. This does not migrate the PostgreSQL auth store, OAuth HTTP handlers, JWT signature verification, or browser authentication UI.

Implementation: `rust/auth/src/port.rs`. Native callers use `scout_auth_rs::port::{valid_slug, registered_schema, token_hash, new_token, password_hash, verify_password, same_origin, validate_identity}`. The clock argument of `validate_identity` is **milliseconds**; callers supply the current clock after verifying the Google JWT signature. As in the original helper, identity validation alone does not verify a signature.

## Evidence

- `rust/auth/gate.json`: every gate check passed: formatting, Clippy warnings-as-errors, Rust tests, lockfile, error paths, build, API exports, and **801/801 recorded cases**.
- `rust/auth/cases.jsonl`: 801 original outcomes; 370 are errors. Per-export counts: slug 80, registered schema 114, token hash 55, new token 53, password hash 94, verification 109, origins 108, identity 188.
- `rust/auth-reference/inputs.jsonl`: 237 explicit domain/adversarial inputs, augmented by generated cases and regex traps.
- Recording reports executable line coverage **88/88** across the adapter and imported unchanged reference; line coverage is not exhaustive branch/input coverage.
- Three Rust regression tests cover the exact Node scrypt vector, secure random production tokens/password roundtrip and UTF-16 password length, plus escaped-surrogate identity behavior.
- `rust/auth-reference/source.json` binds the unchanged reference to source SHA-256 `e5577d6df4aa310e4bc63349f2d2100783a6f33d7efcb386d6433baa5cefaea2`.

The explicit cases cover all reserved slugs, ASCII bounds, UUID/schema/legacy validation, Unicode token hashing, deterministic token bytes, valid/invalid entropy, 11/12 UTF-16 unit and 1024/1025 UTF-8 byte password boundaries, malformed hashes, wrong passwords, known Node scrypt output, normalized/default-port/IDN/IPv6/opaque/blob origins, wrong/expired/malformed identity claims, contextual Greek lowercasing, ECMAScript whitespace, duplicate claim keys, large numeric expiry, controls and escaped lone surrogates in claims and unknown keys.

Reproduce with `RUSTUP_TOOLCHAIN=stable` (toolchain 1.100; generated crate declares 1.97):

```sh
rust-anything record rust/auth --count 20 --inputs rust/auth-reference/inputs.jsonl --node-arg=--experimental-strip-types
rust-anything build rust/auth
rust-anything diff rust/auth --json
rust-anything gate rust/auth --json
rust-anything bench rust/auth --min-ms 50 --json
```

The machine-generated bridge currently records its reference root as an absolute local path; re-bridge or update that manifest location when moving the checkout. The original application files have not been modified.

## Compatibility choices

Password hashing keeps `scrypt$<32 lowercase hex salt chars>$<128 lowercase hex key chars>`, N=32768, r=8, p=1, 64 output bytes, and **the hex salt string as UTF-8**, matching Node. Comparisons use `subtle::ConstantTimeEq`. The minimum password length counts UTF-16 units and the maximum counts UTF-8 bytes. Production randomness uses the operating system through `getrandom`; the isolated test adapter temporarily supplies controlled crypto bytes to the unchanged reference, and Rust characterization exports accept those bytes explicitly. Production token generation remains random.

Origins use the WHATWG-based `url` crate and require the caller's origin string to equal its normalized serialization, matching the original strict check. Typed identity input uses `serde_json::Value`. The JSON characterization path uses `RawValue` plus serde byte strings to preserve JavaScript WTF-8/lone-surrogate behavior and emits the same escaped JSON result. An adversarial mismatch discovered during development was fixed and added to the recording; no known-difference waiver is used.

Relevant dependency sources, checked 2026-11-21: [RustCrypto scrypt](https://docs.rs/scrypt/0.12.0/scrypt/fn.scrypt.html), [constant-time comparison](https://docs.rs/subtle/2.6.1/subtle/trait.ConstantTimeEq.html), [URL origins](https://docs.rs/url/2.5.9/url/struct.Url.html#method.origin), [OS randomness](https://docs.rs/getrandom/0.4.3/getrandom/fn.fill.html), [serde_json byte-string decoding](https://docs.rs/serde_json/1.0.154/serde_json/struct.Deserializer.html#method.deserialize_bytes), [serde byte buffers](https://docs.rs/serde_bytes/0.11.19/serde_bytes/struct.ByteBuf.html). Exact installed dependency versions are in `rust/auth/Cargo.lock`.

## Performance and limits

`rust/auth/bench.json` measures the characterization adapters including the NAPI bridge. Password hash/verify averaged approximately **1.27x / 1.10x faster**, token hashing **1.61x**, origin checking **1.55x** and identity checks **1.09x**. Tiny slug/schema checks were approximately **5.9x / 7.6x slower** across NAPI; deterministic test-token generation was **4.1x slower**. This is a language migration, not a recommendation to ship separate native calls for tiny predicates. Native Rust-to-Rust callers avoid that bridge cost; no native application benchmark is claimed.

The port's own source forbids unsafe code. The generated NAPI wire has three tool-generated unsafe operations with safety comments, checked by the gate. The NAPI bridge represents primitive JavaScript strings as Rust UTF-8 strings, so an isolated JavaScript lone surrogate passed directly as a primitive string is inherently normalized by that binding; raw claim JSON is explicitly preserved and tested. Cryptographic error messages caused by operating-system RNG failure are platform-specific and have not been fault-injected. The HTTP auth flow and whole-application fallback rate remain the coordinator's separate integration work.
