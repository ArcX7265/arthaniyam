"""Durable synthetic receipt worker: python -m app.support.worker.

Optional standalone alternative to the API lifespan worker. Multiple instances
are safe because each bounded batch holds a shared-database write transaction.
"""
import logging
import time

from app.runtime.storage import SQLiteRuntimeRepository
from app.settings import settings
from app.support.service import SupportService


def main():
    if settings.razorpay_mode != "simulate":
        raise SystemExit("Support worker only runs in simulator mode")
    service = SupportService(SQLiteRuntimeRepository())
    while True:
        try:
            service.tick()
        except Exception:
            logging.exception("Support worker batch rolled back; will retry")
        time.sleep(2)


if __name__ == "__main__":
    main()
