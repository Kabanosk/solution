"""`python -m cfo.eval`: run the agent evaluation and log it to MLflow. See `cfo.eval.runner`."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from cfo.eval.scenarios import DEFAULT_PATH


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cfo.eval", description="Evaluate the agent on the scenario set; results go to "
                                                             "the MLflow experiment cfo-agent-eval.")
    p.add_argument("--llm", choices=["fake", "real"], default="fake",
                   help="fake: scripted model (checks the code paths); real: LLM_BASE_URL / LLM_MODEL (default: fake)")
    p.add_argument("--scenarios", default=str(DEFAULT_PATH), help="scenario file (default: the packaged scenarios.yaml)")
    p.add_argument("--only", default="", help="comma-separated scenario ids")
    p.add_argument("--tracking-uri", default="", help="MLflow tracking URI (default: MLFLOW_TRACKING_URI)")
    p.add_argument("--run-name", default=None)
    p.add_argument("--schema", default=None, help="Postgres schema for the scenario company; created and dropped "
                                                  "(default: cfo_eval_<pid>)")
    p.add_argument("--judge", action="store_true", help="add one LLM-as-judge score, asked from the same LLM endpoint")
    p.add_argument("--strict", action="store_true", help="exit with 1 when a scenario fails")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from cfo.eval.runner import format_report, run_eval
    from cfo.eval.scenarios import load_scenarios

    uri = args.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI", "")
    try:
        scenarios = load_scenarios(args.scenarios, [s.strip() for s in args.only.split(",") if s.strip()] or None)
        report = run_eval(tracking_uri=uri, llm_mode=args.llm, scenarios=scenarios, run_name=args.run_name,
                          schema=args.schema, judge=args.judge)
    except (ValueError, ImportError) as exc:
        print(f"cfo.eval: {exc}", file=sys.stderr)
        return 2
    print(format_report(report, uri))
    return 1 if args.strict and report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
