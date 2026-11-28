# Deploy on push to main (GitHub Actions)

`.github/workflows/deploy.yml` builds and rolls out only what a push to `main` changed:

| Changed paths | What happens |
|---|---|
| `backend/**`, `Containerfile`, `deploy/cfo-entrypoint.sh` | `oc start-build cfo-backend --commit=<sha> --wait`; the image triggers roll out api, scout-worker, bank-mock, mcp-*, usage-sampler |
| `dashboard/**` | binary build `cfo-dashboard` from the allowlisted archive, then `oc set image` to the new digest |
| `deploy/openshift/**` | nothing automatic (a notice in the run); run the workflow by hand with `targets=manifests` |

Runs are serialized (`concurrency: deploy-openshift`). The seed job (`deploy/openshift/db`) is never touched.

## One-time setup

```sh
oc project cfo-agent-uwur
oc apply -f deploy/openshift/ci/rbac.yaml
gh secret set OPENSHIFT_SERVER --repo Kabanosk/collabothon-2026 --body "$(oc whoami --show-server)"
oc create token github-deployer --duration=96h | gh secret set OPENSHIFT_TOKEN --repo Kabanosk/collabothon-2026
```

The token expires after 96 h (past the contest); renew with the last line. Revoke at once:
`oc delete sa github-deployer` (deletes its tokens' validity) or `gh secret delete OPENSHIFT_TOKEN`.

## Day to day

```sh
gh run list --workflow deploy.yml --repo Kabanosk/collabothon-2026          # recent deploys
gh run watch --repo Kabanosk/collabothon-2026                                # follow the current one
gh workflow run deploy.yml --repo Kabanosk/collabothon-2026 -f targets=all   # redeploy everything by hand
gh workflow disable deploy.yml --repo Kabanosk/collabothon-2026              # freeze before the live demo
gh workflow enable deploy.yml --repo Kabanosk/collabothon-2026
```

Manual builds (`oc start-build ...`) still work; the BuildConfigs are `Serial`, so a manual build and a workflow build
queue behind each other instead of clashing.
