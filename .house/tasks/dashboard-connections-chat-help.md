# Connections, chat onboarding and delegated-message replies

Base: `origin/main` at `2cd06a215ed7e0b85fc21f6cdbe100a0a663cc48` (after the previous sidebar/chat cleanup PR was merged and deployed).
Branch: `fix/connections-linear-style`.

## User requirements

- Restyle Connections with UI/UX Pro Max to match Scout's existing Linear-like design; reuse semantic theme tokens, shadcn controls and Lucide SVG icons.
- Make Scout chat the default root landing page while preserving explicit routes and existing deep links.
- Provide compact, on-demand dashboard usage guidance through Scout tools, without putting the full guide into every model prompt.
- Make the UI/UX Pro Max and Linear-like design baseline mandatory in root and dashboard AGENTS.md; other design guidance supplements rather than silently replaces it. Preserve the baseline during regeneration. Higher-priority instructions remain applicable.
- Track DMs delegated by a dashboard user. Replies in the recipient's exact Slack thread must return to the original requester, not fall into generic help or invoice routing. Show them in an active dashboard chat or deliver by Slack DM if the requester has left. Handle attribution, retry/duplicate events and pending work explicitly.
- A channel mention must be answered in the same thread. Ordinary replies in that already-established Scout thread must continue the conversation without another mention; unrelated messages and threads stay ignored. Keep conversation history and prevent duplicate message/app_mention deliveries from adding duplicate placeholders or jobs.

## Approach

UI/UX Pro Max returned a verified Minimalism & Swiss Style match for the dashboard direction. Its disclosure-specific searches did not return a relevant rule; keyboard and expanded-state behavior therefore follow the skill's built-in accessibility defaults, not a claimed database match.

Connections becomes a single compact integrations list with progressive disclosure for logs, permissions and administrator setup, not an inconsistent card grid. Existing Google and KSeF operations remain intact. Dashboard help is a single bounded tool with nine topics; only the selected page's guide enters the model context. Internal help links navigate in the same tab.

Delegated replies use durable correlation and delivery acknowledgment with a queued fallback, rather than relying solely on ephemeral browser presence. Tests use isolated local Postgres schemas, FakeLLM/FakeSlack and controlled HTTP responses; never write to real Slack as part of automated checks.

## Verification

- Dashboard unit suite, both-theme desktop/phone/landscape E2E, production build/type checks.
- Help lookup, invalid topic behavior, response/schema context bounds, chat API tool execution and internal-link navigation.
- Delegated DM correlation, recipient checks, duplicate events/retries, active-chat acknowledgment, closed-chat Slack fallback and failure handling.
- Full backend suite against local Postgres; report any skipped or unrelated failures explicitly.

## Verified results

- 54 dashboard unit tests passed; all three browser suites passed, including both-theme UI checks at 320/375/812/1440 widths and open/away/reset/ack-retry/visibility-restoration DM scenarios.
- Production dashboard build and TypeScript compilation passed, including the private `/api/chat/ack` route.
- Final full backend suite with both local database settings configured: 482 passed, 40 skipped. Remaining skips require organizer/generated data, optional MLflow, or an explicitly enabled live KSeF call. Targeted delegated-reply suite: 15 passed after final lease-ownership hardening. Combined Slack UI/thread and delegated-reply checks: 29 passed.
- Delegated replies are matched before general chat routing; receipts suppress Slack fallback only for active, visible chats. Temporary request errors and stale workers recover; exhausted attempts escalate the original task.
- Delivery is retry-based, not strict exactly-once across a process crash after Slack accepts a message. A duplicate fallback remains possible in that narrow window. Old, untracked Slack threads are not retroactively correlated.

Channel continuations are recognized from the durable task key or the queued initial mention, including after restart and before the first job finishes. Tests exercise the real SlackUI-to-worker path with FakeSlack/FakeLLM and prove the second answer receives the first question/answer as context and stays in the original channel thread. The Slack manifest now subscribes to message.channels and message.groups; these events must be enabled on the existing Slack app, not just deployed in backend code.

These follow-on changes remain uncommitted and undeployed. Releasing them requires the new backend tracking tables plus API/worker and dashboard updates, not just the earlier dashboard-only build. The bare URL opens Scout chat after release; explicit #overview links intentionally keep opening Overview.
