# Dashboard on OpenShift

Deploys a separate `dashboard` pod in the selected project. It reads the existing `postgres:5432` service using keys from Secret `postgres`, and calls the internal API at `http://api:8000`. No company records or credentials are included in the build context, manifests or image layers. It does not alter the backend, seed jobs or base-service manifests.

All commands use the current `oc` project. Select the target using the environment variable `OPENSHIFT_PROJECT`; no project name is stored in the manifests:

```sh
oc project "${OPENSHIFT_PROJECT:?Set OPENSHIFT_PROJECT to your project name}"
```

## Login Secret

Before deploying, provide Secret `dashboard-auth` with keys `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`. Set `DASHBOARD_CREDENTIALS_FILE` to a local file outside the repository with mode 0600. The file uses `KEY=value` lines; its path and contents are not committed.

```sh
oc create secret generic dashboard-auth --from-env-file="${DASHBOARD_CREDENTIALS_FILE:?Set DASHBOARD_CREDENTIALS_FILE to your private login file}"
```

For an existing Secret, edit it using your normal secret-management workflow, then restart the dashboard. No default credentials are present in source control. Without this Secret, the pod cannot start; without valid login values, application requests fail closed.

## Build and deploy

From the repository root:

```sh
oc apply -k deploy/openshift/dashboard
python3 dashboard/scripts/package-source.py /tmp/cfo-dashboard-source.tar.gz
oc start-build cfo-dashboard --from-archive=/tmp/cfo-dashboard-source.tar.gz
oc logs -f bc/cfo-dashboard
oc rollout status deployment/dashboard --timeout=180s
oc get route dashboard
```

The packaging script allowlists source and font assets. It excludes `.env` files, records, `node_modules`, local build output and unrelated backend code. Builds are binary uploads, so deploying local changes does not require pushing the public repository. The ImageStream resolves `cfo-dashboard:latest` within the selected project; the image trigger automatically rolls out successful builds.

`dashboard.yaml` defines Deployment, Service and TLS Route (HTTP redirects to HTTPS). The container uses the namespace's non-root UID, drops capabilities, has no service-account token, mounts only temporary scratch space, and sets startup/readiness/liveness probes on `/healthz`. Postgres credentials are separate environment variables, avoiding URI escaping problems.

## Verify

```sh
oc get pods -l app=dashboard
oc get route dashboard
```

Visit the HTTPS route and enter the dashboard login. Unauthenticated `/` and `/api/dashboard` requests must return 401. `/healthz` returns 200 without company data. An authenticated `/api/dashboard` request returns current aggregates and employee/decision records; do not save that response in the repository or print it into public logs.

Compare totals against Postgres using an in-pod query, without exporting the underlying statements. A reload or **Refresh records** must reflect new database bookings. Model outages leave bank data available and show a forecast-unavailable message. Database outages show a generic error with a retry button, without connection details.

Automated HTTP verification (prints pass/fail only, never records):

```sh
DASHBOARD_URL="https://$(oc get route dashboard -o jsonpath='{.spec.host}')"
python3 dashboard/scripts/smoke-test.py "${DASHBOARD_URL}" "${DASHBOARD_CREDENTIALS_FILE:?Set DASHBOARD_CREDENTIALS_FILE to your private login file}"
```

The backend API's existing Route is independently configured and currently has demo access without authentication (`docs/api.md`). This dashboard login protects only the dashboard route. For stronger isolation, the team must separately secure or remove the backend's public Route.

## Update / rollback

Repeat the source packaging and binary build to update only the dashboard. If needed:

```sh
oc rollout undo deployment/dashboard
```

To rotate login credentials, update Secret `dashboard-auth` and run:

```sh
oc rollout restart deployment/dashboard
```
