"""Load the frozen parameter file. Every tunable number comes from config.yaml."""
from __future__ import annotations

import copy
import hashlib
import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config.yaml"


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Return a deep copy of the config so callers can never mutate the cached file."""
    path = os.environ.get("GAMMA_EDGE_CONFIG", path or DEFAULT_PATH)
    return copy.deepcopy(_load(str(path)))


def param(cfg: dict, name: str):
    """Value of a registry parameter, e.g. param(cfg, "abs_threshold") -> 2.0."""
    return cfg["params"][name]["value"]


def with_params(cfg: dict, **overrides) -> dict:
    """Copy of cfg with registry values replaced (used only by the robustness nudges)."""
    out = copy.deepcopy(cfg)
    for k, v in overrides.items():
        if k not in out["params"]:
            raise KeyError(f"unknown parameter {k}")
        out["params"][k]["value"] = v
    return out


def config_hash(cfg: dict) -> str:
    return hashlib.sha1(yaml.safe_dump(cfg, sort_keys=True).encode()).hexdigest()[:10]


def data_path(cfg: dict, *parts: str) -> Path:
    root = Path(cfg["data"]["root"])
    if not root.is_absolute():
        root = ROOT / root
    return root.joinpath(*parts)
