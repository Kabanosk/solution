# Dashboard migration research

Status: inventory and architectural research, 2026-11-21. No dashboard implementation has been ported or certified equivalent by this report.

The existing dashboard contains **134 source files / 10,466 lines** under `app`, `components`, `lib`, and `hooks`, with **37 page/route files**. `dashboard-inventory.json` records every source file, imports, exports, environment references, request paths, dependencies, route methods, and feature groups. This is a source inventory, not runtime coverage. `app/icon.svg`, static font files, middleware/proxy, instrumentation, asset build scripts, and framework configuration are additional application inputs.

## Recommended target

Use a native Axum HTTP server with **Leptos SSR and browser hydration compiled to WebAssembly**. Retain the current route shapes, HTTP/JSON contracts, semantic CSS tokens, Tailwind utility classes, local fonts, brand assets, and Lucide SVG paths. Compile both UI state and UI event handling from Rust. Keep the current Next.js application operational behind the migration front door until browser parity is established for each feature.

This is an architectural recommendation inferred from the documented capabilities: Leptos supports server rendering, hydration, browser rendering and shared Rust components; `leptos_axum` integrates the renderer with Axum. It does not establish drop-in React compatibility. [Leptos crate documentation](https://docs.rs/leptos/latest/leptos/), [Leptos Axum integration](https://docs.rs/leptos_axum/latest/leptos_axum/).

`cargo-leptos` supplies the combined build workflow. Pin a mutually compatible release of Leptos, its router, integration, build tool and `wasm-bindgen` after checking the available toolchain. Generated JavaScript used to load WASM and browser bindings remains a build artifact, not rewritten business logic. [cargo-leptos repository](https://github.com/leptos-rs/cargo-leptos), [wasm-bindgen repository](https://github.com/wasm-bindgen/wasm-bindgen).

The user explicitly requests Rust throughout. That supersedes the existing framework-specific requirement to build with Next.js and React; it does not change the mandatory Scout design baseline. Preserve the existing shadcn component DOM/classes and semantic behavior as the source contract for equivalent Rust primitives. A React dashboard that merely calls WASM model helpers is an intermediate migration state, not the requested whole application.

## Feature scope

| Feature | Existing source and behavior that must survive |
|---|---|
| Authentication | `organization-auth`, `auth-store`, `auth`, `auth-crypto`, `proxy`: organization lookup/creation, password login, Google registration/login/link, one-use join/login links, membership checks, switching, logout, rate limits, tenant isolation, origin checks, cookie attributes, private cache policy. |
| Shell | `dashboard`, `app-sidebar`, `nav`: default Scout chat, hash navigation, aliases, back/forward history, deep task links, Command/Ctrl-K search, `g` shortcuts, collapsible groups, mobile drawer/bottom navigation, focus restoration and unique DOM IDs. |
| Overview | `overview/*`, `overview-model`, `finance*`: bank totals, period spend, quarter/year liquidity chart, safety buffer, what-if overlay, team spending/budget donut table, activity ring and recent outcomes. |
| Tasks | `tasks/*`, `scout*`, `task-signals`: filter/search, owner/inbox presets, steps and audit/evidence, approval/rejection, simulated labels, live refresh, per-tenant detail cache, unseen results and loop activity signals. |
| Chat | `llm-chat`, `chat-*`, `external-*`, `markdown`: incremental NDJSON stream, abort/timeout/error states, steps, file artifacts, followups and acknowledgment, bounded context, provider selection, owner-stored provider, context consent, safe Markdown. |
| Connections/setup | `connections`, `onboarding*`, `ksef`, `connection-display`: bank, invoicing, KSeF, Google mail, Slack/network; persisted checklist, signed OAuth state/browser proof, channel selection, enable/disable, KSeF progress and invoices, sanitized setup handoff. |
| Settings | `strategy-*`, `guardrails-*`, `access-policies`: strategy goals/provider/daily review, encrypted credentials, owner-only actions, per-organization guardrails and audit. |
| Records | `transactions`, `audit-log`, `staffing`, `invoice-model`, `use-records`: lazy bank/audit records, invoice line items/text, employee/payroll display, tenant-scoped caches and error handling. |
| Usage | `llm-usage*`: usage, costs, model load, timeseries buckets and charts, refresh and upstream error states. |
| Public/demo/assets | privacy/terms, configured legal URLs, local fonts/icons, health, intentionally labeled development preview, gated demo scenarios, loading/empty/error states. |

The legacy paths `/tasks`, `/tasks/[id]`, `/inbox`, `/audit`, and `/connections` are separately present and must retain their current redirects/rendering behavior. The principal authenticated page is `/[organization]`; hash views are not represented by separate HTTP routes. Preserve the aliases in `lib/nav.ts`, including `#llm`, `#forecast`, `#investigations`, `#agent-jobs`, `#inbox`, and `#loop-*`.

## Dependency replacements and limits

| Current dependency | Rust target | Parity considerations |
|---|---|---|
| Next.js/React | Leptos SSR/hydration + Axum | Rewrite component and event/state logic. Preserve routes and server/client separation; default server-function endpoints must not replace the established `/api/*` routes. |
| shadcn/ui, Radix | Port local component source/classes to Rust primitives | No verified direct replacement for all existing behavior. Preserve portals, dismissal, focus traps/return, keyboard navigation, ARIA state and mobile drawer behavior. |
| Recharts | Rust-rendered SVG chart components | Explicitly port line/band/reference geometry, responsive measurement, tick selection/formatting, donut slices, legends, labels and tooltips. No chart crate has been established as output-compatible. |
| Lucide React | Existing licensed static Lucide SVG paths/assets | Framework-independent SVGs preserve icon identity. Keep stroke widths, size and decorative/label semantics. |
| `pg` | `tokio-postgres` or SQLx behind a small repository interface | Preserve query semantics, transactions, readonly settings, search path lifetime, nullable fields, string money, authentication schema and pool behavior. |
| Node crypto | RustCrypto scrypt/SHA-256/AES-GCM, secure RNG, base64url | Preserve the current serialized formats exactly; default PHC password serialization is incompatible. |
| `jose` | A verified JWT/JWK implementation | Verify signature, allowed algorithms, Google issuer/audience/nonce/expiry/email constraints and one-use transaction consumption before trusting identity. |
| Fetch/ReadableStream/browser APIs | `reqwest` on server; `web-sys`/WASM on browser | Preserve redirects, cancellation, bounded reads, partial UTF-8/line handling, timeout/status mapping and no-store policies. |
| Tailwind/PostCSS/fonts | Retain generated CSS/static assets, or rebuild CSS from Rust source classes | CSS is an asset, not application logic. A Rust application need not discard source style tokens. Build scanner must actually see Rust class strings. |

shadcn supplies editable component code; it does not provide a cross-framework compiler. Radix documents focus management, keyboard navigation and ARIA behavior that must be re-established when replacing its primitives. [shadcn introduction](https://ui.shadcn.com/docs), [Radix accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility).

Recharts is a React chart library, so retaining it at runtime retains React. Lucide supplies standalone SVG assets that avoid a framework dependency. [Recharts repository](https://github.com/recharts/recharts), [Lucide static assets](https://lucide.dev/guide/static).

Axum supports streaming SSE responses. Browser bindings expose `EventSource`, but **the current chat transport is POST plus NDJSON**, not ordinary EventSource; the replacement must follow the actual `app/api/chat/route.ts` behavior. [Axum SSE](https://docs.rs/axum/latest/axum/response/sse/), [web-sys EventSource](https://docs.rs/web-sys/latest/web_sys/struct.EventSource.html), [Leptos browser API integration](https://book.leptos.dev/web_sys.html).

## Concrete semantic traps

1. **Session compatibility:** cookie names are `scout_session` and `scout_oauth`. Session tokens are opaque; SHA-256 hashes are stored in the database. Middleware strips client-supplied organization/schema headers and independently authorizes membership. Preserve failure statuses and private/no-store/security headers. Never trust the path or `X-Scout-Organization` by itself.
2. **Password compatibility:** the stored form is `scrypt$<32 lowercase hex salt chars>$<128 lowercase hex key chars>`. Node scrypt receives the **hex salt string as UTF-8**, not decoded 16-byte salt, with N=32768, r=8, p=1, dkLen=64. Rust must use those exact inputs; the RustCrypto default PHC string is a different format. Minimum password length uses JavaScript UTF-16 code units, maximum uses UTF-8 bytes. [RustCrypto scrypt documentation](https://docs.rs/scrypt/latest/scrypt/).
3. **Provider encryption:** `strategy-credentials.ts` stores `base64url(iv).base64url(tag).base64url(ciphertext)` with 12-byte IV, AES-256-GCM, SHA-256 of configured secret as key and organization ID bytes as AAD. Preserve existing ciphertext decoding and organization binding. Use a fixture generated by the original and verify both directions.
4. **Money:** SQL aggregates return bigint text cents; frontend converts with explicit helpers, including `BigInt` in totals. Do not introduce f64 for stored money. Display formatting uses `Intl.NumberFormat("en-IE", {currency:"EUR"})`; Rust locale formatting, Unicode spacing, rounding and negative signs may differ.
5. **JS semantics:** string lengths/offsets are UTF-16, URL parsing uses WHATWG URL/URLSearchParams, object order can affect presentation, regex whitespace/word/Unicode behavior differs, and JavaScript rounding differs for negative halves. Preserve signed and zero behavior in finance and chart calculations.
6. **Hydration:** browser-only storage, DOM APIs, timers and initial hash parsing cannot run during server rendering. Server and browser DOM structure must match. Existing `DomIds` deliberately waits until the client tree hydrates. [Leptos hydration guidance](https://book.leptos.dev/ssr/24_hydration_bugs.html).
7. **Storage isolation:** `scopeKey` binds caches to rendered organization and user. Keep that boundary for task details, seen results, sidebar state, conversations and records; global module caches cannot leak prior tenant data after switching.
8. **Streaming:** chat sanitation drops internal `external_context` events, accepts validated delta/step/done/error/file/followup events, and emits an error on interrupted streams. The older `completionEvents` path has bounded SSE parsing and JSON response handling. A buffered JSON response is not equivalent to streaming.
9. **Unavailable systems:** forecast failure must not hide real bank data. Setup handoff fails if state cannot be read instead of fabricating a checklist. Live research checks actual provider tool-search metadata and citations; a completion from memory is not equivalent.
10. **Responsive/accessibility:** retain light default and saved dark theme, theme cross-tab sync, keyboard shortcuts, focus return after search/dialog, reduced-motion rules, mobile drawer and non-hover alternatives. The skill search `keyboard focus dialog --stack shadcn` was run; recommendations supplement existing component behavior and do not authorize a redesign.

## Recommended order and acceptance evidence

1. Record pure functions before replacing them. A small first unit can cover string/bool helpers in `auth-crypto.ts` (`validSlug`, `registeredSchema`, `tokenHash`, `sameOrigin`), with explicit Unicode, reserved slug, malformed URL and schema cases. That file also imports crypto and contains async/OIDC behavior; verify the analyzer's bridge/export support before treating the whole file as one completed unit.
2. Port pure model modules with existing unit-test inputs: navigation, finance, task signals, invoice, chat sanitation, onboarding, guardrails, strategy and usage. Structured JSON arguments need recorded real cases; autogenerated primitive cases alone are insufficient.
3. Port authentication/control-plane repositories and `/api/*` handlers behind Axum. Use a separate test PostgreSQL schema and recorded request sequences for registration, login/logout, switching, invalid origin, expired/reused state, unauthorized role and cross-tenant requests. Preserve password/ciphertext fixture compatibility.
4. Port the reusable UI primitives and shell first, then feature views. Preserve local markup/classes rather than substitute a new visual theme. Take baseline screenshots of every view in light/dark and at narrow/wide widths and compare keyboard/pointer interactions using the existing Playwright tests.
5. Replay HTTP plus real browser flows and compare side effects. HTTP recording of SSR markup alone cannot prove WASM hydration, keyboard focus, mobile behavior, chat increments or chart interaction.
6. Remove React/Next.js runtime fallback only after the map covers **every** listed feature and both HTTP and browser parity evidence are green. A reduced static dashboard cannot satisfy this application rewrite.

## Remaining blockers

- No verified Rust equivalents for all current Radix/Shadcn primitive behaviors and Recharts output have been selected or tested; these are implementation tasks, not automatic crate swaps.
- Full characterization requires an isolated seeded PostgreSQL instance and controlled integrations; arbitrary recording of auth/demo/setup POSTs against real services would mutate state.
- The present report does not provide screenshots, execute a WASM build, prove a Rust minimum version, or certify any port. It supplies the feature checklist and exact contracts needed to begin that work.
