"""Guards for the cluster manifests (T042): render `kubectl kustomize deploy/` and check the runtime wiring.

Findings guarded: api / Scout worker need the `cfo-data` PVC at /app/data; every `cfo <command>` the
Deployments run must resolve to a module that exists in backend/cfo or backend/scout; the seed Job must tolerate
the image build.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

pytestmark = pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")


@pytest.fixture(scope="module")
def docs() -> list[dict]:
    out = subprocess.run(
        ["kubectl", "kustomize", str(REPO / "deploy")], check=True, capture_output=True, text=True, timeout=240
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _by_kind(docs, kind):
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == kind}


def _pod_spec(workload: dict) -> dict:
    spec = workload["spec"]["template"]["spec"]
    return spec


def _container(workload: dict, name: str) -> dict:
    return next(c for c in _pod_spec(workload)["containers"] if c["name"] == name)


def _mounts_of_pvc(workload: dict, container: dict, claim: str) -> list[dict]:
    volumes = {v["name"] for v in _pod_spec(workload).get("volumes", []) if v.get("persistentVolumeClaim", {}).get("claimName") == claim}
    return [m for m in container.get("volumeMounts", []) if m["name"] in volumes]


@pytest.mark.parametrize("deployment,container", [("api", "api"), ("scout-worker", "worker")])
def test_cfo_data_mounted_read_only_at_app_data(docs, deployment, container):
    workload = _by_kind(docs, "Deployment")[deployment]
    mounts = _mounts_of_pvc(workload, _container(workload, container), "cfo-data")
    assert [(m["mountPath"], m.get("readOnly", False)) for m in mounts] == [("/app/data", True)]


def test_workstations_mcp_mounts_cfo_data(docs):
    workload = _by_kind(docs, "Deployment")["mcp-workstations"]
    mounts = _mounts_of_pvc(workload, _container(workload, "mcp-workstations"), "cfo-data")
    assert [m["mountPath"] for m in mounts] == ["/app/data"]


def test_seed_job_writes_cfo_data(docs):
    job = _by_kind(docs, "Job")["cfo-seed"]
    mounts = _mounts_of_pvc(job, _container(job, "seed"), "cfo-data")
    assert [(m["mountPath"], m.get("readOnly", False)) for m in mounts] == [("/app/data", False)]


def test_api_runs_no_agent_and_the_bank_mock_has_a_service(docs):
    env = {e["name"]: e.get("value") for e in _container(_by_kind(docs, "Deployment")["api"], "api")["env"]}
    assert "AGENT_IN_API" not in env
    assert "worker" not in _by_kind(docs, "Deployment")  # the December replay worker is gone
    (port,) = _by_kind(docs, "Service")["bank-mock"]["spec"]["ports"]
    assert port["port"] == 8010
    config = _by_kind(docs, "ConfigMap")["cfo-config"]["data"]
    assert config["BANK_API_URL"] == "http://bank-mock:8010" and config["SCOUT_WORKERS"] == "4"


def _entrypoint_modules() -> dict[str, str]:
    """`cfo <command>` -> module, parsed from deploy/cfo-entrypoint.sh (`cmd) ... exec python -m <module>`)."""
    text = (REPO / "deploy" / "cfo-entrypoint.sh").read_text()
    modules = dict(re.findall(r"^    ([\w-]+)\)\n\s+(?:[^\n]*\n\s+)*?[^\n]*?exec python -m ([\w.]+)", text, re.M))
    # `api` runs `python -m uvicorn cfo.api.app:app`: the module that matters is the ASGI app's.
    app = re.search(r"python -m uvicorn (cfo[\w.]*):\w+", text)
    if modules.get("api") == "uvicorn" and app:
        modules["api"] = app.group(1)
    return modules


def _module_exists(module: str) -> bool:
    assert module.split(".")[0] in ("cfo", "scout"), module
    rel = BACKEND.joinpath(*module.split("."))
    return rel.with_suffix(".py").is_file() or (rel / "__init__.py").is_file()


def test_entrypoint_commands_resolve_to_existing_modules():
    modules = _entrypoint_modules()
    assert {"api", "mcp", "worker", "bank-mock", "seed", "migrate", "generate", "load"} <= set(modules)
    assert (modules["worker"], modules["bank-mock"], modules["seed"]) == ("scout.worker", "scout.mock_bank",
                                                                         "scout.seed")
    for command, module in modules.items():
        assert _module_exists(module), f"`cfo {command}` runs python -m {module}, which is not in backend/"


def test_workloads_run_known_cfo_commands(docs):
    """Every `args` of a container running the backend image is a `cfo <command>` the entrypoint handles, or
    `python -m <module>` (executed as is) with a module that exists in backend/cfo."""
    modules = _entrypoint_modules()
    seen = {}
    for kind in ("Deployment", "Job"):
        for name, workload in _by_kind(docs, kind).items():
            for c in _pod_spec(workload)["containers"]:
                if c.get("args") and "command" not in c:
                    seen[f"{name}/{c['name']}"] = c["args"]
    assert seen["scout-worker/worker"] == ["worker"]
    assert seen["bank-mock/bank-mock"] == ["bank-mock"]
    assert seen["api/api"] == ["api"]
    for who, args in seen.items():
        if args[:2] == ["python", "-m"]:
            assert _module_exists(args[2]), f"{who} runs python -m {args[2]}, which is not in backend/"
        else:
            assert args[0] in modules, f"{who} runs `cfo {args[0]}`, not handled by cfo-entrypoint.sh"


def test_seed_job_deadline_covers_image_build(docs):
    # The Job starts together with the first image build (ImagePullBackOff until the ImageStream tag exists).
    job = _by_kind(docs, "Job")["cfo-seed"]
    assert job["spec"]["activeDeadlineSeconds"] >= 3600


def test_scout_reaches_the_guardrails_service(docs):
    """T052: GUARDRAILS_URL in the shared ConfigMap, the token from Secret scout-guardrails-client (key token), for
    api (dashboard chat) and worker; the Secret is optional."""
    config = _by_kind(docs, "ConfigMap")["cfo-config"]["data"]
    assert config["GUARDRAILS_URL"] == "https://scout-guardrails.cfo-agent-uwur.svc.cluster.local"
    deployments = _by_kind(docs, "Deployment")
    for name, container in (("api", "api"), ("scout-worker", "worker")):
        env = {e["name"]: e for e in _container(deployments[name], container)["env"]}
        ref = env["GUARDRAILS_TOKEN"]["valueFrom"]["secretKeyRef"]
        assert (ref["name"], ref["key"], ref.get("optional")) == ("scout-guardrails-client", "token", True)
        assert {"name": "cfo-config"} == next(e["configMapRef"] for e in _container(deployments[name], container)["envFrom"]
                                              if "configMapRef" in e)
