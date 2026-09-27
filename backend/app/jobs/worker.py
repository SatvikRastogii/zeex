"""Background worker: `uv run python -m app.jobs.worker`.

Polls the jobs table, re-reading the demo clock each loop."""

import logging
import os
import socket
import time

from sqlalchemy.orm import sessionmaker

from app.db.session import get_engine
from app.jobs import handlers  # noqa: F401  registers job handlers
from app.jobs.demo_clock import load_clock
from app.jobs.queue import claim, recover_stale, run_job

log = logging.getLogger("worker")
POLL_SECONDS = 1.0


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    make = sessionmaker(get_engine(), expire_on_commit=False)
    log.info("worker %s started", worker_id)
    while True:
        with make() as s:
            clock = load_clock(s)
            recovered = recover_stale(s, clock.now())
            if recovered:
                log.warning("recovered %d stale job(s)", recovered)
            jobs = claim(s, worker_id, clock.now(), limit=10)
        for job in jobs:
            ok = run_job(make, clock, job)
            log.info("job %s %s: %s", job.kind, job.id, "done" if ok else "error")
        if not jobs:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
