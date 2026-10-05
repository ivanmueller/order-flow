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


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _resolve(path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    base = cfg.pop("extends", None)
    if base:  # overlay file: only the keys it lists differ from its base
        cfg = _merge(_load(str(_resolve(base))), cfg)
    return cfg


_announced: set = set()


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Return a deep copy of the active config so callers can never mutate the cached file.

    The active file is GAMMA_EDGE_CONFIG (shell or .env), else `path`, else config.yaml.
    Put GAMMA_EDGE_CONFIG=config.pilot.yaml in .env for the pilot; delete that line to switch back.
    """
    load_env_file()
    path = _resolve(os.environ.get("GAMMA_EDGE_CONFIG") or path or DEFAULT_PATH)
    cfg = copy.deepcopy(_load(str(path)))
    if path != DEFAULT_PATH and str(path) not in _announced:
        _announced.add(str(path))
        import sys
        print(f"[config] using {path.name} ({cfg.get('name', '?')})", file=sys.stderr)
    return cfg


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


def load_env_file(path: str | os.PathLike | None = None) -> None:
    """Read KEY=VALUE lines from the repo's .env (git-ignored) into os.environ.
    Variables already set in the shell win. Keys are never logged or written anywhere."""
    path = Path(path or ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.removeprefix("export ").strip()
        os.environ.setdefault(k, v.strip().strip("'\""))


def data_path(cfg: dict, *parts: str) -> Path:
    root = Path(cfg["data"]["root"])
    if not root.is_absolute():
        root = ROOT / root
    return root.joinpath(*parts)
