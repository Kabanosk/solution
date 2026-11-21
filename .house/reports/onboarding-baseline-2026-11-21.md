# Scout baseline verification — 2026-11-21

## Source and scope

Repository: https://github.com/Kabanosk/solution, freshly cloned and pulled with `git pull --ff-only origin main`; baseline commit `6f6f7e4`.

These results concern a **local isolated checkout**, not production. No deployment, production migration, commit, push, real employee message or paid LLM call was performed.

## Results before onboarding implementation

| Check | Actual result |
| --- | --- |
| Locked dashboard dependency install | Passed |
| `npm --prefix dashboard run typecheck` | Passed |
| `npm --prefix dashboard run build` | Passed |
| Dashboard unit tests without database | 64 passed, 17 explicitly skipped |
| Dashboard unit tests with isolated migrated database | **81 passed, 0 failed, 0 skipped** |
| Organization browser suite including Python API tenant checks | Passed |
| UI cleanup browser suite | Passed in light/dark, desktop/mobile/landscape |
| Connections browser suite | Passed in light/dark, responsive layouts, keyboard navigation |
| Delegated-reply browser suite | Passed for open/away/reset/retry/visibility restoration using controlled replies |
| Google signup unavailable-state browser test | Passed; **not a live Google OAuth test** |
| Backend with DB tests, default Europe/Warsaw database timezone | 500 passed, 59 skipped, **1 failed** |
| Dedicated tenant-isolation/schema-upgrade backend tests | **35 passed** |
| Backend full suite with tenant tests enabled and UTC database sessions | **520 passed, 40 skipped, 0 failed** |

Dashboard tests used Node 22.23.5 via an ephemeral npm-exec runtime because the machine default is Node 20.20.2. The production build also succeeded on the default runtime.

## Database and browser safety

Created a new PostgreSQL 18 cluster in a uniquely named temporary directory, listening only on loopback port 55437. Separate disposable databases were used for the dashboard and backend tests. The pre-existing Postgres container on another port was not used.

The real browser suite exercised password registration, private empty tenant provisioning, login/logout, authorization against colliding task IDs in two organizations, forged organization headers, anonymous rejection, CSRF rejection, invalid OAuth callbacks and responsive light/dark layouts. Screenshots contain only synthetic test organizations. They were opened and visually inspected.

## Existing defect found and narrowly repaired during approved work

`backend/cfo/usage/report.py::build_live` returned the PostgreSQL session's local timestamp offset instead of normalizing the wire value to UTC. The original API assertion expects `2026-11-21T10:00:00Z`; the default local database produced `2026-11-21T11:00:00+01:00`.

Added a regression that changes the database session to Europe/Warsaw and America/New_York. Both cases failed before the repair with the corresponding local offset. Changed only the returned `sampled_at` datetime to `astimezone(timezone.utc)`.

Post-repair focused verification: **49 LLM-usage tests passed**. The original exact UTC assertion was retained. A subsequent complete backend run with the default non-UTC database timezone passed **522 tests, with 40 explicitly skipped**. A final integrated backend rerun remains part of onboarding acceptance.

## Forty remaining backend skips

The UTC full run's skips require one of:
- organizer-provided confidential bank statement CSV or generated scenario records;
- the optional MLflow extra/registry;
- explicit opt-in to live KSeF test-environment access.

Missing private data was not fabricated or copied from production to make the skip count disappear.

## Live provider status

No mailbox Google client, Slack installation client, identity Google client or connection-encryption key is present in this session's environment. Checks reported presence only, never secret values.

Consequently this baseline **does not establish** live Gmail/Google Workspace consent or mailbox access, live Slack installation, bunq sandbox access, KSeF authentication, VPN connectivity or production readiness. These remain explicit acceptance gates. Mocked callbacks and disabled-button tests must not be described as working live authorization.

## Next work in progress

Approved post-workspace-creation onboarding, durable per-organization setup, actual provider authorization paths, an agent-neutral handoff, and matching Scout UI are being implemented in three isolated task worktrees. The handoff configures company connections; it does not modify Scout source or transfer credentials. No onboarding implementation was included in the baseline runs above.
