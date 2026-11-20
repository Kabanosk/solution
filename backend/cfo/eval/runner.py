"""One evaluation = one MLflow run in experiment `cfo-agent-eval`.

`run_eval` plays the scenarios one after another (each is one trace: span `eval.scenario` with the agent's
`agent.handle` / node / `llm.chat` spans of `cfo.observability` below it), then hands the traces with the
outputs and expectations to `mlflow.genai.evaluate` and the scorers of `cfo.eval.scoring`. MLflow attaches the
scores to the traces (run page, tab Traces) and logs `<scorer>/mean` metrics. On top of that the run gets:

* tags `llm_model`, `llm_mode`, `git_sha`, `scenarios`;
* metrics `scenarios`, `scenarios_passed`, `pass_rate` (a scenario passes when no pass/fail scorer failed it);
* the table `eval_scenarios.json` (run page, tab Artifacts): one row per scenario with scores, reasons, trace id.

The scenarios run sequentially on purpose: they share one Postgres schema, and latency should be the model's,
not the thread pool's. The LLM calls of an evaluation are not written to the `llm_usage` table.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cfo import observability as obs
from cfo.config import get_settings
from cfo.eval import scoring
from cfo.eval.harness import EvalWorld, run_scenario, scripted_llm
from cfo.eval.scenarios import Scenario, load_scenarios
from cfo.llm import LLM, Message, get_llm
from cfo.usage import recorder

log = logging.getLogger(__name__)

EXPERIMENT = "cfo-agent-eval"
TABLE = "eval_scenarios.json"
JUDGE = "explanation_clear_judge"


@dataclass
class EvalReport:
    run_id: str
    experiment_id: str
    metrics: dict[str, float]
    rows: list[dict[str, Any]]  # one per scenario: id, scores, rationales, trace id, outputs
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if not r["passed"]]


class _NoUsage:
    def write(self, record: Any) -> None:
        pass


def git_sha() -> str:
    """`GIT_SHA` from the environment (set it in an image, where there is no checkout), else `git rev-parse`."""
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True,
                             timeout=10, check=True)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def judge_explanation(llm: LLM, outputs: dict[str, Any]) -> scoring.Score:
    """Optional LLM-as-judge (flag `--judge`), asked through `cfo.llm` like every other model call (D-009), so
    it is the same open-weights endpoint. Question: can the reader tell what happened and what is wanted?"""
    texts = [m["text"] for m in outputs.get("messages") or []]
    if not texts:
        return scoring.Score(None, "no message was sent")
    system = ("You review a message a finance assistant sent to a colleague. Answer with one word, YES or NO, "
              "then one short sentence. YES only if the message says what happened and what, if anything, the "
              "reader should do.")
    try:
        reply = llm.chat([Message(role="system", content=system), Message(role="user", content=texts[0][:2000])],
                         max_tokens=60).content or ""
    except Exception as exc:  # noqa: BLE001
        return scoring.Score(None, f"judge call failed: {exc}")
    verdict = reply.strip().upper()
    if not verdict.startswith(("YES", "NO")):
        return scoring.Score(None, f"judge gave no verdict: {reply[:80]!r}")
    return scoring.Score(verdict.startswith("YES"), reply.strip()[:200])


def _mlflow_scorers(extra: dict[str, dict[str, scoring.Score]]) -> list[Any]:
    """The scoring functions as MLflow scorers. `extra`: scores computed outside (judge), by name and scenario."""
    from mlflow.entities import Feedback
    from mlflow.genai.scorers import scorer

    def feedback(s: scoring.Score) -> Any:
        return None if s.value is None else Feedback(value=s.value, rationale=s.rationale)

    @scorer
    def policy_level_correct(outputs, expectations):
        return feedback(scoring.policy_level_correct(outputs, expectations))

    @scorer
    def action_correct(outputs, expectations):
        return feedback(scoring.action_correct(outputs, expectations))

    @scorer
    def no_execution_without_approval(outputs, expectations):
        return feedback(scoring.no_execution_without_approval(outputs, expectations))

    @scorer
    def explanation_cites_amount(outputs, expectations):
        return feedback(scoring.explanation_cites_amount(outputs, expectations))

    @scorer(aggregations=["mean", "max"])
    def latency_s(outputs):
        return scoring.latency_s(outputs).value

    out = [policy_level_correct, action_correct, no_execution_without_approval, explanation_cites_amount, latency_s]
    for name, by_scenario in extra.items():
        def precomputed(outputs, _scores=by_scenario):
            return feedback(_scores.get(outputs.get("scenario"), scoring.Score(None, "")))

        out.append(scorer(precomputed, name=name))
    return out


def _row(scenario: Scenario, outputs: dict[str, Any], scores: dict[str, scoring.Score], trace_id: str | None) -> dict[str, Any]:
    failed = [n for n in scoring.PASS_FAIL if scores[n].value is False]
    row: dict[str, Any] = {
        "scenario": scenario.id, "title": scenario.title, "source": scenario.source, "target": scenario.target,
        "passed": not failed,
        "expected_level": scenario.expect.level, "level": outputs["level"], "actions": ", ".join(outputs["actions"]),
    }
    for name, s in scores.items():
        row[name] = s.value if not isinstance(s.value, bool) else int(s.value)
    row["why_failed"] = "; ".join(f"{n}: {scores[n].rationale}" for n in failed)
    row["llm_calls"] = outputs["llm_calls"]
    row["first_message"] = (outputs["texts"] or [""])[0][:300]
    row["trace_id"] = trace_id
    row["outputs"] = outputs
    return row


def run_eval(*, tracking_uri: str | None = None, llm_mode: str = "fake", llm: LLM | None = None,
             scenarios: Sequence[Scenario] | str | Path | None = None, only: list[str] | None = None,
             database_url: str | None = None, schema: str | None = None, experiment: str = EXPERIMENT,
             artifact_location: str | None = None, run_name: str | None = None, judge: bool = False) -> EvalReport:
    """Run the scenarios and log the evaluation. Needs the `mlflow` extra and a reachable Postgres.

    `llm_mode`: `fake` (scripted model, checks the code paths) or `real` (`LLM_BASE_URL` / `LLM_MODEL`); `llm`
    overrides both. `tracking_uri` defaults to `MLFLOW_TRACKING_URI`. `schema`: the Postgres schema the
    scenarios live in (default `cfo_eval_<pid>`; created, and dropped at the end).
    """
    import mlflow

    if llm_mode not in ("fake", "real"):
        raise ValueError(f"llm_mode must be fake or real, got {llm_mode!r}")
    settings = get_settings()
    uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    if not uri:
        raise ValueError("no MLflow tracking URI: pass --tracking-uri or set MLFLOW_TRACKING_URI")
    items = list(scenarios) if isinstance(scenarios, Sequence) and not isinstance(scenarios, str) else load_scenarios(scenarios)
    if only:
        items = [s for s in items if s.id in only]
    if not items:
        raise ValueError("no scenarios to run")
    if llm is None:
        llm = scripted_llm() if llm_mode == "fake" else get_llm(settings)
    model_name = str(getattr(llm, "model", None) or ("fake" if llm_mode == "fake" else settings.llm_model))

    mlflow.set_tracking_uri(uri)
    if artifact_location and mlflow.get_experiment_by_name(experiment) is None:
        mlflow.create_experiment(experiment, artifact_location=artifact_location)
    # agent spans (cfo.observability) go to the evaluation experiment, next to the run
    obs.init_tracing(obs.TracingSettings(mlflow_tracking_uri=uri, mlflow_agent_experiment=experiment))
    mlflow.set_experiment(experiment)

    tags = {"llm_model": model_name, "llm_mode": llm_mode, "git_sha": git_sha(), "scenarios": str(len(items))}
    previous_sink = recorder._sink  # restore exactly what was installed (tests install their own)
    recorder.set_sink(_NoUsage())
    rows: list[dict[str, Any]] = []
    data: list[dict[str, Any]] = []
    judged: dict[str, scoring.Score] = {}
    try:
        with mlflow.start_run(run_name=run_name or f"{llm_mode}-{model_name}"[:60]) as run:
            mlflow.set_tags(tags)
            with EvalWorld(database_url or settings.database_url, llm, schema) as world:
                for sc in items:
                    inputs = {"scenario": sc.id, "title": sc.title, "steps": sc.steps}
                    with mlflow.start_span(name="eval.scenario", span_type="CHAIN") as sp:
                        sp.set_inputs(inputs)
                        outputs = run_scenario(world, sc)
                        sp.set_outputs(outputs)
                        mlflow.update_current_trace(tags={
                            "cfo.eval.scenario": sc.id, "cfo.eval.target": sc.target, "cfo.eval.llm": model_name})
                        trace_id = sp.trace_id
                    expectations = sc.expect.model_dump(exclude_none=True)
                    scores = scoring.score_all(outputs, expectations)
                    if judge:
                        judged[sc.id] = scores[JUDGE] = judge_explanation(llm, outputs)
                    rows.append(_row(sc, outputs, scores, trace_id))
                    data.append({"trace_id": trace_id, "inputs": inputs, "outputs": outputs, "expectations": expectations})
                    log.info("%s: %s (%.2fs)%s", sc.id, "pass" if rows[-1]["passed"] else "FAIL", outputs["latency_s"],
                             f" - {rows[-1]['why_failed']}" if rows[-1]["why_failed"] else "")

            mlflow.flush_trace_async_logging()
            for d in data:  # the stored trace, so MLflow attaches the scores to it and links it to the run
                d["trace"] = mlflow.get_trace(d.pop("trace_id"))
            result = mlflow.genai.evaluate(data=data, scorers=_mlflow_scorers({JUDGE: judged} if judge else {}))

            passed = sum(1 for r in rows if r["passed"])
            summary = {"scenarios": float(len(rows)), "scenarios_passed": float(passed), "pass_rate": passed / len(rows)}
            mlflow.log_metrics(summary)
            import pandas as pd

            table = pd.DataFrame([{k: v for k, v in r.items() if k != "outputs"} for r in rows])
            mlflow.log_table(data=table, artifact_file=TABLE)
            metrics = {**{k: float(v) for k, v in result.metrics.items()}, **summary}
            return EvalReport(run_id=run.info.run_id, experiment_id=run.info.experiment_id, metrics=metrics, rows=rows,
                              tags=tags)
    finally:
        recorder.set_sink(previous_sink)
        try:
            mlflow.flush_trace_async_logging()
        except Exception:
            log.debug("flushing traces failed", exc_info=True)


def format_report(report: EvalReport, tracking_uri: str = "") -> str:
    """Plain-text summary for the terminal."""
    names = [n for n in (*scoring.PASS_FAIL, JUDGE) if any(n in r for r in report.rows)]
    short = {"policy_level_correct": "level", "action_correct": "action", "no_execution_without_approval": "no-exec",
             "explanation_cites_amount": "cites", JUDGE: "judge"}
    width = max(len(r["scenario"]) for r in report.rows)
    mark = {1: "ok", 0: "FAIL", None: "-"}
    lines = [f"{'scenario':<{width}}  " + "  ".join(f"{short[n]:>7}" for n in names) + "  latency_s  level"]
    for r in report.rows:
        lines.append(f"{r['scenario']:<{width}}  " + "  ".join(f"{mark[r.get(n)]:>7}" for n in names)
                     + f"  {r['latency_s']:>9.2f}  {r['level']}"
                     + (f" (expected {r['expected_level']})" if r["expected_level"] not in (None, r["level"]) else ""))
    lines.append("")
    for r in report.failed:
        lines.append(f"FAIL {r['scenario']}: {r['why_failed']}")
    lines.append(f"{int(report.metrics['scenarios_passed'])}/{int(report.metrics['scenarios'])} scenarios passed; "
                 + ", ".join(f"{k}={v:.3f}" for k, v in sorted(report.metrics.items()) if k.endswith("/mean")))
    lines.append(f"model {report.tags.get('llm_model')} ({report.tags.get('llm_mode')}), git {report.tags.get('git_sha', '')[:12]}")
    where = tracking_uri.rstrip("/")
    link = f"{where}/#/experiments/{report.experiment_id}/runs/{report.run_id}" if where.startswith("http") else where
    lines.append(f"MLflow run {report.run_id} in experiment {EXPERIMENT} ({report.experiment_id}): {link}")
    return "\n".join(lines)
