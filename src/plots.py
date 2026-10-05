"""Inspection plots for the notebooks (notebooks hold no logic, only calls into src/)."""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def smile(vols: pd.DataFrame, day, n_expiries: int = 3, ax=None):
    ax = ax or plt.subplots(figsize=(8, 4))[1]
    v = vols[vols["date"] == day]
    for (root, exp), g in list(v.groupby(["root", "expiration"]))[:n_expiries]:
        g = g.sort_values("strike")
        ax.plot(g["strike"] / g["F"].iloc[0], g["iv"], marker=".", label=f"{root} {exp}")
    ax.axvline(1.0, color="grey", lw=0.5)
    ax.set(xlabel="K / F", ylabel="implied vol", title=f"Smile {day}")
    ax.legend()
    return ax


def em_vs_vix(gex_daily: pd.DataFrame, daily: pd.DataFrame, ax=None):
    ax = ax or plt.subplots(figsize=(10, 4))[1]
    d = daily.sort_values("date").assign(vix_prev=lambda x: x["vix_close"].shift(1))
    m = gex_daily.merge(d[["date", "vix_prev"]], on="date")
    ax.plot(pd.to_datetime(m["date"]), m["em"] / m["s0"], label="EM / S0")
    ax.plot(pd.to_datetime(m["date"]), m["vix_prev"] / 100 / np.sqrt(252), label="VIX(D-1) / sqrt(252)")
    ax.legend()
    ax.set_title("Gate 0 check 4: expected move tracks VIX")
    return ax


def gex_timeline(gex_daily: pd.DataFrame, ax=None):
    ax = ax or plt.subplots(figsize=(10, 4))[1]
    g = gex_daily.sort_values("date")
    x = pd.to_datetime(g["date"])
    ax.plot(x, g["s0"], color="k", lw=1, label="S0")
    for c, col in (("flip", "tab:purple"), ("call_wall", "tab:green"), ("put_wall", "tab:red")):
        ax.plot(x, g[c], ".", ms=3, color=col, label=c)
    ax2 = ax.twinx()
    ax2.bar(x, g["net_gex"] / 1e9, alpha=0.2, color="tab:blue")
    ax2.set_ylabel("net GEX ($bn per 1%)")
    ax.legend(loc="upper left")
    return ax


def quintile_bars(q: list[dict], title: str, ax=None):
    ax = ax or plt.subplots(figsize=(6, 4))[1]
    df = pd.DataFrame(q)
    ax.bar(df["quintile"].astype(str), df["mean"],
           yerr=[df["mean"] - df["lo"], df["hi"] - df["mean"]], capsize=4)
    ax.set(xlabel="GEX percentile quintile (1 = lowest)", title=title)
    return ax


def success_table(rows: list[dict], ax=None):
    ax = ax or plt.subplots(figsize=(8, 4))[1]
    df = pd.DataFrame(rows)
    df = df[df["gex_tercile"] == "all"].sort_values("group")
    ax.errorbar(df["group"], df["success"], yerr=[df["success"] - df["lo"], df["hi"] - df["success"]],
                fmt="o", capsize=5)
    ax.set(ylabel="success rate (90% Wilson)", title="Stage 2: level groups vs placebo")
    return ax


def equity(sim_trades: pd.DataFrame, groups=None, ax=None):
    ax = ax or plt.subplots(figsize=(10, 4))[1]
    t = sim_trades[sim_trades["pnl_r"].notna()]
    if groups:
        t = t[t["group"].isin(groups)]
    for mode, g in t.groupby("mode"):
        g = g.sort_values("entry_ts")
        ax.plot(pd.to_datetime(g["entry_ts"]), g["pnl_r"].cumsum().to_numpy(), label=f"{mode} (n={len(g)})")
    ax.axhline(0, color="grey", lw=0.5)
    ax.set(ylabel="cumulative R after costs", title="Stage 3 equity curves")
    ax.legend()
    return ax
