# Native runtime configuration

`scout-config` preserves the three source Pydantic settings profiles, their defaults and per-organization removal of global integration credentials. Loading is explicit and never mutates the process environment. Repository and working-directory `.env` files precede environment overrides; the API and Scout profiles ignore empty overrides as before.

301/301 rust-anything application replays pass against the original models. Cases cover all setting fields, accepted and rejected typed values, Decimal scale and signed zero, path normalization, case-insensitive keys, file precedence, expansion, quotes, multiline values, parse warnings, nullable list handling, and both legacy and newly enrolled tenant isolation. Invalid-settings results compare acceptance rather than the Pydantic exception prose. The reference harness clears inherited environment variables and supplies temporary files explicitly; it never reads the repository's real `.env`.

The file parser follows the documented [python-dotenv format](https://bbc2.github.io/python-dotenv/#file-format) and file-first [variable expansion](https://bbc2.github.io/python-dotenv/#variable-expansion), checked against the installed original implementation. Python remains only in the development characterization harness.
