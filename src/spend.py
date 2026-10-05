"""Databento spending guard (CLAUDE.md rule 7).

Every pull is priced with metadata.get_cost first and written to data/spend_ledger.csv.
A run may spend at most --approve-usd (default: the $5 "ask first" threshold), and no pull may
push the ledger total past the $100 line without --allow-past-total.
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

from src.config import data_path


class SpendRefused(RuntimeError):
    pass


def client():
    import databento as db
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError("Set DATABENTO_API_KEY in the environment (never in code or config).")
    return db.Historical(key)


def ledger_path(cfg):
    return data_path(cfg, "spend_ledger.csv")


def total_spent(cfg) -> float:
    p = ledger_path(cfg)
    return float(pd.read_csv(p)["cost_usd"].sum()) if p.exists() else 0.0


def _record(cfg, job: str, what: str, cost: float):
    p = ledger_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with open(p, "a") as f:
        if new:
            f.write("ts_utc,job,what,cost_usd\n")
        f.write(f"{dt.datetime.now(dt.timezone.utc).isoformat()},{job},{what},{cost:.6f}\n")


class Budget:
    """Tracks spend within one CLI run against the user's approval."""

    def __init__(self, cfg, approve_usd: float | None, allow_past_total: bool = False):
        b = cfg["budget"]
        self.cfg = cfg
        self.approve = b["ask_above_single_pull_usd"] if approve_usd is None else approve_usd
        self.total_line = b["ask_above_total_usd"]
        self.allow_past_total = allow_past_total
        self.spent = 0.0

    def price(self, cl, args: dict) -> float:
        return float(cl.metadata.get_cost(**args))

    def pull(self, cl, job: str, what: str, args: dict):
        cost = self.price(cl, args)
        if self.spent + cost > self.approve + 1e-9:
            raise SpendRefused(
                f"{what}: ${cost:.4f} would take this run to ${self.spent + cost:.2f}, "
                f"over the approved ${self.approve:.2f}. Re-run with a higher --approve-usd after review.")
        ledger = total_spent(self.cfg)
        if ledger + cost > self.total_line and not self.allow_past_total:
            raise SpendRefused(
                f"{what}: ledger total would reach ${ledger + cost:.2f}, past the ${self.total_line:.0f} line. "
                "Ask first; then pass --allow-past-total.")
        data = cl.timeseries.get_range(**args)
        self.spent += cost
        _record(self.cfg, job, what, cost)
        return data, cost
