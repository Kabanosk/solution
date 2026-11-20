"""CLI: python -m cfo.sim start|pause|step|jump YYYY-MM-DD|status|reset|run [--data data/generated]

`start`/`pause`/`jump`/`step`/`reset` change the shared state in Postgres and return at once. The clock only
runs while a worker is ticking: `run` (foreground loop, Ctrl-C to stop) or `start --run`.
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from cfo.sim import control
from cfo.sim.engine import SimStatus


def _print(st: SimStatus) -> None:
    print(st.model_dump_json(indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfo.sim")
    parser.add_argument("--data", type=Path, default=None, help="generated data dir (default data/generated)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser("start", help="resume the clock (optionally set the speed)")
    p_start.add_argument("--speed", type=float, default=None, help="sim-seconds per real second")
    p_start.add_argument("--run", action="store_true", help="then run the worker loop in the foreground")
    sub.add_parser("pause", help="stop the clock")
    sub.add_parser("step", help="emit the next event now")
    p_jump = sub.add_parser("jump", help="move forward to a date, emitting the events in between")
    p_jump.add_argument("to", help="YYYY-MM-DD or ISO datetime")
    sub.add_parser("status", help="print the state")
    sub.add_parser("reset", help="back to 1 Dec 2025, paused (does not clear the events table)")
    sub.add_parser("run", help="worker loop in the foreground")
    args = parser.parse_args(argv)

    control.ensure_schema()
    if args.cmd == "start":
        _print(control.start(args.speed, args.data))
        if not args.run:
            return 0
    elif args.cmd == "pause":
        _print(control.pause(args.data))
    elif args.cmd == "step":
        event = control.step(args.data)
        print(event.model_dump_json(indent=2) if event else "no events left")
    elif args.cmd == "jump":
        events = control.jump(args.to, args.data)
        print(f"emitted {len(events)} events")
        _print(control.status(args.data))
    elif args.cmd == "status":
        _print(control.status(args.data))
    elif args.cmd == "reset":
        _print(control.reset(args.data))
    if args.cmd in ("run", "start"):
        sim = control.make_simulator(args.data)
        stop = threading.Event()
        try:
            sim.run(stop)
        except KeyboardInterrupt:
            stop.set()
        finally:
            sim.repo.conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
