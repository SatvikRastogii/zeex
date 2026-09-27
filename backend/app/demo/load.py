"""Demo loader.

    uv run python -m app.demo.load --all          # every scenario to its demo point
    uv run python -m app.demo.load --scenario 4   # one scenario
    uv run python -m app.demo.load --play         # scenario 1 end to end (to a closed order)
    uv run python -m app.demo.load --reset        # wipe everything and re-seed

Progress is written to storage/demo_status.json for the Demo Control Panel."""

import argparse
import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Base
from app.db.session import get_engine
from app.demo.autoreply import set_settings, settings
from app.demo.scenarios import SCENARIOS, Driver, play_full
from app.jobs.clock import SystemClock
from app.seed.run import run as seed

SEQUENCES = ("bom_code_seq", "quote_code_seq", "neg_code_seq", "po_code_seq")


def status_path() -> Path:
    return Path(get_settings().storage_dir) / "demo_status.json"


def write_status(**fields: Any) -> None:
    p = status_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    current.update(fields, updated_at=datetime.now(UTC).isoformat())
    p.write_text(json.dumps(current, indent=1), encoding="utf-8")


def reset() -> None:
    """Empty every table (TRUNCATE, which the audit-log guard allows) and re-seed."""
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))  # noqa: S608  table names from metadata
        for seq in SEQUENCES:
            conn.execute(text(f"ALTER SEQUENCE {seq} RESTART"))
    with Session(get_engine()) as db:
        seed(db, SystemClock())


def run(which: list[str], play: bool = False) -> dict[str, Any]:
    d = Driver()
    with Session(get_engine()) as db:
        before = settings(db)
        set_settings(db, auto_reply=False)  # scenarios script every vendor reply themselves
        db.commit()
    results: dict[str, Any] = {}
    try:
        for key, name, fn in SCENARIOS:
            if key in which:
                write_status(state="running", current=f"{key}. {name}")
                results[key] = {"name": name, **fn(d)}
        if play:
            write_status(state="running", current="Full run of scenario 1")
            results["play"] = {"name": "Full run of scenario 1", **play_full(d)}
    finally:
        with Session(get_engine()) as db:
            set_settings(
                db, auto_reply=True, persona_mode=before.get("persona_mode", "scripted")
            )  # live demo: vendors answer by themselves
            db.commit()
    write_status(state="done", current=None, results=results)
    return results


def main() -> int:
    logging.basicConfig(level="WARNING")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--scenario", action="append", default=[])
    ap.add_argument("--play", action="store_true")
    args = ap.parse_args()
    if not get_settings().demo_mode:
        print("demo tools need DEMO_MODE=true", file=sys.stderr)
        return 2
    try:
        if args.reset:
            write_status(state="running", current="Resetting demo data", results={})
            reset()
            write_status(state="done", current=None)
        which = [k for k, _, _ in SCENARIOS] if args.all else args.scenario
        if which or args.play:
            for key, info in run(which, args.play).items():
                print(f"scenario {key}: {info['name']} -> {info['status']}")
    except Exception as e:
        write_status(state="failed", current=None, error=f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
