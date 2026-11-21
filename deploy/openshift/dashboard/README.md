# Scout dashboard on OpenShift

Deploys the separate dashboard pod, reading PostgreSQL and calling the internal Python API. **Before deploying this auth version, follow [the organization migration and secret guide](../../../docs/scout-auth.md).** It requires the matching backend version and the opt-in auth migration; mixing the old open API with the new dashboard is not a secure deployment.

## Configuration

- Existing Secret `postgres` supplies the DB connection. No records or credentials are part of the image/build context.
- `SCOUT_APP_ORIGIN` in `dashboard.yaml` must exactly match the Route origin. Update it deliberately if the Route changes.
- Google sign-in uses optional Secret `scout-login`, keys `client-id` and `client-secret`. Create/update it from private files through your secret-management workflow, never from secret literals in shell history. Missing Google credentials leave Google visibly disabled; password sign-in still requires the migrated DB.
- The old `dashboard-auth` Basic-login Secret is no longer used or injected. Do not delete unrelated Secrets automatically.
- The Route replaces forwarded headers before the application trusts its single client IP. Do not expose the Service directly to callers who can spoof that header.
- `CFO_API_URL` remains internal `http://api:8000`. All calls carry the member's opaque session and requested organization. The backend validates both independently.
- Existing Pizzeria Amalfi stays locked until its intended existing account is explicitly enrolled by an operator. Never give it to the first Google visitor.

## Build and deploy (operator action, not performed by this change)

Select the intended cluster context/namespace before running deployment commands. From the repo root:

```bash
oc apply -k deploy/openshift/dashboard
```

```bash
python3 dashboard/scripts/package-source.py /tmp/cfo-dashboard-source.tar.gz
```

```bash
oc start-build cfo-dashboard --from-archive=/tmp/cfo-dashboard-source.tar.gz
```

```bash
oc rollout status deployment/dashboard --timeout=180s
```

The source packager allowlists source/fonts and excludes `.env` files, confidential records, dependencies and build output. The deployment uses OpenShift's arbitrary non-root UID, drops capabilities, mounts temporary scratch space, uses a read-only root filesystem and has process-only `/healthz` probes.

## Verification

- `/` is public organization discovery/registration, **not** an HTTP Basic challenge.
- `/<slug>` displays that organization's login when signed out.
- Anonymous `/api/dashboard` and direct Python `/v1/tasks` return 401.
- Valid org A sessions cannot request org B data, even with known IDs or forged org headers.
- `/healthz` returns process health only.
- Check sign-in, logout, membership revocation and invalid OAuth callbacks from a clean browser profile before routing real users.
- Do not use the old Basic-login `scripts/smoke-test.py` against this version. The current browser/auth suite is `dashboard/tests/organization-auth.e2e.cjs`, run against an isolated local migrated DB.

A model outage produces unavailable forecasts/chat errors without substituting sample records. A missing backend/migration fails closed. Do not publish private records in logs or screenshots.

## Rollback

Read the auth guide's data-preserving rollback section first. Application-image rollback must not re-expose the previous unauthenticated API. Preserve all tenant schemas together with auth metadata; never reset or seed a live database as rollback. Secret rotation is an operator action and normally requires an explicit pod restart to pick up changed environment variables; this change does not trigger one automatically.

Strategy news runs inside the dashboard pod every 24 hours after owner enablement. Apply the auth migration before deploying, and ensure `gpt-key` contains `API_KEY`. OpenAI is configured automatically for workspace owners. See [Strategy setup](../../../../docs/strategy-loop.md) for scheduling, encryption and model settings.
