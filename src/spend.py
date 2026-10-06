"""Databento spending guard (CLAUDE.md rule 7).

Every pull is priced with metadata.get_cost first and written to data/spend_ledger.csv.
A run may spend at most --approve-usd (default: the $5 "ask first" threshold), and no pull may
push the ledger total past the $100 line without --allow-past-total.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time

import pandas as pd

from src.config import data_path, load_env_file


log = logging.getLogger("spend")

RETRY_WAITS_S = (2, 4, 8, 16, 32)


class SpendRefused(RuntimeError):
    pass


def _transient(e: Exception) -> bool:
    """Gateway timeouts, 5xx, 429 rate limits and dropped connections: retry. Other 4xx: don't."""
    import requests
    from databento.common.error import BentoClientError, BentoServerError
    if isinstance(e, BentoClientError):
        return getattr(e, "http_status", None) == 429
    return isinstance(e, (BentoServerError, requests.ConnectionError, requests.Timeout))


def with_retries(fn, what: str):
    for i, wait in enumerate(RETRY_WAITS_S + (None,)):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if wait is None or not _transient(e):
                raise
            log.warning("%s: %s; retry %d/%d in %ds", what, str(e).splitlines()[0][:120], i + 1, len(RETRY_WAITS_S), wait)
            time.sleep(wait)


def client():
    import databento as db
    load_env_file()
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError("Set DATABENTO_API_KEY in .env at the repo root (git-ignored) or in your shell.")
    return db.Historical(key)


def ledger_path(cfg):
    return data_path(cfg, "spend_ledger.csv")


def total_spent(cfg) -> float:
    p = ledger_path(cfg)
    return float(pd.read_csv(p)["cost_usd"].sum()) if p.exists() else 0.0


_ledger_lock = threading.Lock()


def _record(cfg, job: str, what: str, cost: float):
    p = ledger_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _ledger_lock:
        new = not p.exists()
        with open(p, "a") as f:
            if new:
                f.write("ts_utc,job,what,cost_usd\n")
            f.write(f"{dt.datetime.now(dt.timezone.utc).isoformat()},{job},{what},{cost:.6f}\n")


class Budget:
    """Tracks spend within one CLI run against the user's approval. Safe to share across threads:
    the cost of a pull is reserved against the cap before the request and released if it fails."""

    def __init__(self, cfg, approve_usd: float | None, allow_past_total: bool = False):
        b = cfg["budget"]
        self.cfg = cfg
        self.approve = b["ask_above_single_pull_usd"] if approve_usd is None else approve_usd
        self.total_line = b["ask_above_total_usd"]
        self.allow_past_total = allow_past_total
        self.spent = 0.0          # committed
        self.reserved = 0.0       # in flight
        self._lock = threading.Lock()

    def price(self, cl, args: dict) -> float:
        return float(with_retries(lambda: cl.metadata.get_cost(**args), "get_cost"))

    def _reserve(self, what: str, cost: float):
        with self._lock:
            run = self.spent + self.reserved + cost
            if run > self.approve + 1e-9:
                raise SpendRefused(
                    f"{what}: ${cost:.4f} would take this run to ${run:.2f}, "
                    f"over the approved ${self.approve:.2f}. Re-run with a higher --approve-usd after review.")
            ledger = total_spent(self.cfg) + self.reserved
            if ledger + cost > self.total_line and not self.allow_past_total:
                raise SpendRefused(
                    f"{what}: ledger total would reach ${ledger + cost:.2f}, past the ${self.total_line:.0f} line. "
                    "Ask first; then pass --allow-past-total.")
            self.reserved += cost

    def pull(self, cl, job: str, what: str, args: dict):
        cost = self.price(cl, args)
        self._reserve(what, cost)
        try:
            data = with_retries(lambda: cl.timeseries.get_range(**args), what)
        except Exception:
            with self._lock:
                self.reserved -= cost
            raise
        with self._lock:
            self.reserved -= cost
            self.spent += cost
        _record(self.cfg, job, what, cost)
        return data, cost
