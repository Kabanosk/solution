"""Guards for the forecast Jobs (T064): own kustomization, never part of a normal deploy, image has the extras."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
KUBE_CLI = shutil.which("kubectl") or shutil.which("oc")
pytestmark = pytest.mark.skipif(KUBE_CLI is None, reason="kubectl or oc not installed")


def _render(path: str) -> list[dict]:
    out = subprocess.run([KUBE_CLI, "kustomize", str(REPO / path)], check=True, capture_output=True, text=True,
                         timeout=240).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def test_the_forecast_kustomization_renders_exactly_a_job_and_a_cronjob():
    docs = _render("deploy/openshift/forecast")
    assert sorted((d["kind"], d["metadata"]["name"]) for d in docs) == [("CronJob", "forecast-retrain"),
                                                                        ("Job", "forecast-tune")]


def test_the_tune_job_is_bounded_and_runs_the_search_on_the_database():
    job = next(d for d in _render("deploy/openshift/forecast") if d["kind"] == "Job")
    assert job["spec"]["backoffLimit"] == 0 and job["spec"]["ttlSecondsAfterFinished"] == 86400
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    c = pod["containers"][0]
    assert c["args"][:3] == ["python", "-m", "cfo.forecast.tune"]
    assert {"--source", "db", "--promote-if-better"} <= set(c["args"]) and c["args"][c["args"].index("--trials") + 1] == "200"
    assert c["resources"]["requests"] == {"cpu": "1", "memory": "2Gi"}
    assert c["resources"]["limits"] == {"cpu": "2", "memory": "4Gi"}
    assert "nvidia.com/gpu" not in str(c["resources"])
    assert {"configMapRef": {"name": "cfo-config"}} in c["envFrom"]
    assert any(e["name"] == "DATABASE_URL" for e in c["env"])


def test_the_retrain_cronjob_is_nightly_and_never_overlaps():
    cron = next(d for d in _render("deploy/openshift/forecast") if d["kind"] == "CronJob")
    assert cron["spec"]["concurrencyPolicy"] == "Forbid" and re.fullmatch(r"\d+ \d+ \* \* \*", cron["spec"]["schedule"])
    c = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert c["args"][:3] == ["python", "-m", "cfo.forecast.train"]
    assert {"--source", "db", "--register", "--promote-if-better"} <= set(c["args"])


def test_a_normal_deploy_does_not_include_the_forecast_jobs():
    for path in ("deploy", "deploy/openshift/app"):
        assert not [d for d in _render(path) if d["kind"] in ("Job", "CronJob") and "forecast" in d["metadata"]["name"]]
    workflow = (REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "forecast" not in workflow


def test_the_app_config_serves_the_registry_champion():
    cm = next(d for d in _render("deploy/openshift/app") if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "cfo-config")
    assert cm["data"]["FORECAST_MODEL_URI"] == "models:/liquidity-forecast@champion"


@pytest.mark.parametrize("file", ["Containerfile", "Containerfile.dependencies"])
def test_the_backend_image_installs_the_tuning_extra(file):
    sync = [line for line in (REPO / file).read_text(encoding="utf-8").splitlines() if "uv sync" in line]
    assert sync and all("--extra mlflow" in line and "--extra tuning" in line for line in sync)
