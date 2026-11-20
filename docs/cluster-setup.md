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

## 6. Upload the bank CSV (before the seed job)
The `sample-data` PVC starts empty. The seed job (`cfo-seed`, part of `oc apply -k deploy/`) downloads
`pizzeria-amalfi-bank-statement-2025-v6.csv` from the `files` service; without the file the download answers
404, the job fails and Postgres stays empty. Upload it once, from the machine that has the organizer CSV
(never commit it):

On a fresh namespace the file server and its PVC do not exist yet: apply only them first (this does not
create the seed job), then stream the file into the volume:

```sh
oc apply -f deploy/files.yaml
oc rollout status deploy/files
oc exec -i deploy/files -- sh -c 'cat > /data/pizzeria-amalfi-bank-statement-2025-v6.csv' < ./pizzeria-amalfi-bank-statement-2025-v6.csv
```
Check that the file is on the volume and that the file server serves it (the second command must print `200`):
```sh
oc exec deploy/files -- ls -l /data
oc exec deploy/files -- python -c "import urllib.request as u; print(u.urlopen('http://localhost:8080/pizzeria-amalfi-bank-statement-2025-v6.csv').status)"
```

## 7. Seed the database
```sh
oc apply -k deploy/
oc start-build cfo-backend --follow      # first image; the seed job waits for it (ImagePullBackOff, then runs)
oc wait --for=condition=complete job/cfo-seed --timeout=3600s
oc logs job/cfo-seed
```
The job's `activeDeadlineSeconds` (3600) counts from job creation, so it covers the wait for the first image
build. If the CSV was missing and the job already failed, upload it (step 6) and re-run:
`oc delete job cfo-seed && oc apply -k deploy/` (a Job spec is immutable). Do not re-seed during a live demo
(`load --reset` also resets the simulator).

## 8. Re-applying the base
```sh
oc apply -k deploy/
oc rollout status deploy/postgres
oc rollout status deploy/files
```
The app (API, worker, MCP servers, MLflow) is part of the same kustomization; see
`deploy/openshift/app/README.md`.
