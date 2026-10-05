"""Append an entry to RUNLOG.md for every analysis run (CLAUDE.md rule 4)."""
from __future__ import annotations

import datetime as dt
import subprocess

from src.config import ROOT, config_hash, load_config


def git_commit() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.call(["git", "diff", "--quiet"], cwd=ROOT) != 0
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def log_run(stage: str, change: str, headline: str, config_diff: str = "none", cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    entry = (f"\n## {dt.date.today().isoformat()} | {stage}\n"
             f"- commit: {git_commit()}  config: {config_hash(cfg)}\n"
             f"- change: {change}\n- config diff: {config_diff}\n- result: {headline}\n")
    with open(ROOT / "RUNLOG.md", "a") as f:
        f.write(entry)
    return entry
