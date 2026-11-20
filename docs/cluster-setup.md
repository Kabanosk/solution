# Cluster setup (team)

How to get access to our OpenShift AI cluster and the shared base services.
Namespace: `cfo-agent-uwur`.

## 1. Install `oc`
Download the CLI from the cluster web console (`?` → Command Line Tools) or from
https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/ and put `oc` on your `PATH`.

## 2. Log in
Web console → your name (top right) → **Copy login command** → **Display Token**, then:

```sh
oc login --token=<token> --server=<api-url>
oc project cfo-agent-uwur
```

## 3. What is running
| Service | In-cluster address | What |
|---|---|---|
| `postgres` | `postgres:5432`, db `cfo` | PostgreSQL 16; credentials in Secret `postgres` |
| `files` | `http://files:8080` | Read-only file server with the challenge sample data (PVC `sample-data`) |

Pods in this namespace reach both by service name; nothing needs to be exposed.
Everything carries the label `app.kubernetes.io/part-of=cfo`:
`oc get all -l app.kubernetes.io/part-of=cfo`.

## 4. Local access (debugging only)
```sh
oc port-forward svc/postgres 5432:5432   # then: psql -h localhost -U cfo cfo
oc port-forward svc/files 8080:8080      # then: http://localhost:8080
```
Port-forward lives only while the command runs. Do not create a public Route for
`files` or `postgres`: the sample data is organizer material ("for business use only").

## 5. Database password
```sh
oc get secret postgres -o jsonpath='{.data.POSTGRESQL_PASSWORD}' | base64 -d
```
Never commit secrets or the sample data to this repo (it is public).

## 6. Re-applying the base
```sh
oc apply -k deploy/
oc rollout status deploy/postgres
oc rollout status deploy/files
```
The app (API, agent, MCP servers) will be added to `deploy/` later; its guide will be `docs/deploy.md`.
