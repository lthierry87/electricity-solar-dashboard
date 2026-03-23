#!/usr/bin/env python3
"""
Household Energy Analyzer
Analyzes electricity consumption and solar production from Huawei SUN2000 / Smart Meter CSV exports.
Supports 15-minute interval data. All power values assumed in Watts.
"""

import sys
import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
import seaborn as sns

warnings.filterwarnings("ignore", category=FutureWarning)

# ── Aesthetic defaults ────────────────────────────────────────────────────────
PALETTE = {
    "consumption": "#E05252",
    "production":  "#F5A623",
    "grid_import": "#C0392B",
    "grid_export":  "#27AE60",
    "battery_charge": "#2980B9",
    "battery_discharge": "#8E44AD",
    "self_sufficiency": "#16A085",
    "self_consumption": "#D35400",
}
plt.rcParams.update({
    "figure.facecolor": "#1C1C1E",
    "axes.facecolor":   "#2C2C2E",
    "axes.edgecolor":   "#48484A",
    "axes.labelcolor":  "#EBEBF5",
    "axes.titlecolor":  "#EBEBF5",
    "xtick.color":      "#EBEBF5",
    "ytick.color":      "#EBEBF5",
    "text.color":       "#EBEBF5",
    "grid.color":       "#3A3A3C",
    "grid.linestyle":   "--",
    "grid.alpha":       0.5,
    "legend.facecolor": "#2C2C2E",
    "legend.edgecolor": "#48484A",
    "legend.labelcolor": "#EBEBF5",
    "font.size":        10,
})

INTERVAL_H = 0.25  # 15-minute intervals → hours per sample


# ── Data loading ──────────────────────────────────────────────────────────────

def load_csv(path: str) -> pd.DataFrame:
    """Load and normalise the energy CSV. Handles multi-line headers."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"[ERROR] File not found: {path}")

    # Read raw to detect multi-line header
    raw = p.read_bytes().decode("utf-8", errors="replace")
    lines = raw.splitlines()

    # The first non-empty line that starts with "Date" or an ISO timestamp
    # marks where data begins. The header may span multiple lines due to
    # multi-line quoted column names.
    header_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip().strip('"')
        if stripped.startswith("20") and "T" in stripped:
            header_end = i
            break

    header_block = "\n".join(lines[:header_end])

    # Parse header into clean column names
    import csv, io
    reader = csv.reader(io.StringIO(header_block))
    raw_cols = []
    for row in reader:
        raw_cols.extend(row)
    # Collapse whitespace / newlines in each column name
    raw_cols = [" ".join(c.split()) for c in raw_cols if c.strip()]

    data_block = "\n".join(lines[header_end:])
    df = pd.read_csv(io.StringIO(data_block), header=None, low_memory=False)

    # Trim columns to match header length
    n_cols = min(len(raw_cols), df.shape[1])
    df = df.iloc[:, :n_cols]
    df.columns = raw_cols[:n_cols]

    # Rename to standard names based on content hints
    rename = {}
    for col in df.columns:
        lc = col.lower()
        if "date" in lc:
            rename[col] = "timestamp"
        elif col == "Consumption":
            rename[col] = "consumption_w"
        elif col == "Production":
            rename[col] = "production_w"
        elif "battery charg" in lc or col == "Battery Charging":
            rename[col] = "battery_charge_w"
        elif "battery discharg" in lc or col == "Battery Discharging":
            rename[col] = "battery_discharge_w"
        elif "currentpower" in lc.replace(" ", "") and "inverter" in lc:
            rename[col] = "inverter_w"
        elif "currentpower" in lc.replace(" ", "") and "meter" in lc:
            rename[col] = "meter_w"
    df.rename(columns=rename, inplace=True)

    # Fallback: if still no 'timestamp', try first column
    if "timestamp" not in df.columns:
        df.rename(columns={df.columns[0]: "timestamp"}, inplace=True)

    # Parse datetime (timezone-aware → UTC → tz-naive for simplicity)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["timestamp"] = df["timestamp"].dt.tz_convert("Europe/Paris").dt.tz_localize(None)
    df.dropna(subset=["timestamp"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.set_index("timestamp", inplace=True)

    # Coerce numeric columns
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).clip(lower=0)

    # Derive energy columns (Wh per 15-min interval)
    df["consumption_wh"] = df["consumption_w"] * INTERVAL_H
    df["production_wh"]  = df["production_w"]  * INTERVAL_H

    if "battery_charge_w" in df.columns:
        df["battery_charge_wh"]    = df["battery_charge_w"]    * INTERVAL_H
        df["battery_discharge_wh"] = df["battery_discharge_w"] * INTERVAL_H

    # Grid import / export heuristic
    # If meter_w is available: positive = import, negative = export
    if "meter_w" in df.columns and df["meter_w"].sum() > 0:
        df["grid_import_w"]  = df["meter_w"].clip(lower=0)
        df["grid_export_w"]  = 0.0
    else:
        # Estimate from energy balance
        df["grid_import_w"] = (df["consumption_w"] - df["production_w"]).clip(lower=0)
        df["grid_export_w"] = (df["production_w"]  - df["consumption_w"]).clip(lower=0)

    df["grid_import_wh"] = df["grid_import_w"] * INTERVAL_H
    df["grid_export_wh"] = df["grid_export_w"] * INTERVAL_H

    # Self-consumed solar (production used directly, not exported)
    df["self_consumed_wh"] = df["production_wh"] - df["grid_export_wh"]
    df["self_consumed_wh"] = df["self_consumed_wh"].clip(lower=0)

    return df


# ── Summary statistics ────────────────────────────────────────────────────────

def print_summary(df: pd.DataFrame):
    total_c = df["consumption_wh"].sum() / 1000  # kWh
    total_p = df["production_wh"].sum()  / 1000
    total_import = df["grid_import_wh"].sum() / 1000
    total_export = df["grid_export_wh"].sum() / 1000
    self_suf = (1 - total_import / total_c) * 100 if total_c > 0 else 0
    self_con = (df["self_consumed_wh"].sum() / 1000 / total_p * 100) if total_p > 0 else 0

    span_days = (df.index[-1] - df.index[0]).days + 1
    years = span_days / 365.25

    has_battery = "battery_charge_wh" in df.columns and df["battery_charge_wh"].sum() > 0

    sep  = "=" * 60
    sep2 = "-" * 60
    print(f"\n{sep}")
    print("  ENERGY SUMMARY")
    print(sep)
    print(f"  Period          : {df.index[0].date()}  ->  {df.index[-1].date()}  ({span_days} days)")
    print(f"  Samples         : {len(df):,}  (15-min intervals)")
    print(sep2)
    print(f"  Total consumption : {total_c:>10,.1f} kWh  ({total_c/years:,.0f} kWh/yr)")
    print(f"  Total production  : {total_p:>10,.1f} kWh  ({total_p/years:,.0f} kWh/yr)")
    print(f"  Grid import       : {total_import:>10,.1f} kWh")
    print(f"  Grid export       : {total_export:>10,.1f} kWh")
    print(f"  Self-sufficiency  : {self_suf:>9.1f} %   (consumption met by solar)")
    print(f"  Self-consumption  : {self_con:>9.1f} %   (production used on-site)")
    if has_battery:
        total_bc = df["battery_charge_wh"].sum()   / 1000
        total_bd = df["battery_discharge_wh"].sum() / 1000
        print(f"  Battery charged   : {total_bc:>10,.1f} kWh")
        print(f"  Battery discharged: {total_bd:>10,.1f} kWh")
    print(sep2)
    peak_c = df["consumption_w"].max()
    peak_p = df["production_w"].max()
    avg_daily_c = total_c / span_days
    print(f"  Peak consumption  : {peak_c:>8,.0f} W  at {df['consumption_w'].idxmax().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Peak production   : {peak_p:>8,.0f} W  at {df['production_w'].idxmax().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Avg daily usage   : {avg_daily_c:>8.2f} kWh/day")
    print(sep + "\n")


# ── Helper: resample to daily kWh ─────────────────────────────────────────────

def daily_kwh(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["consumption_wh", "production_wh", "grid_import_wh",
            "grid_export_wh", "self_consumed_wh"]
    if "battery_charge_wh" in df.columns:
        cols += ["battery_charge_wh", "battery_discharge_wh"]
    d = df[cols].resample("D").sum() / 1000  # → kWh
    d.columns = [c.replace("_wh", "_kwh") for c in d.columns]
    d["self_sufficiency"] = (1 - d["grid_import_kwh"] / d["consumption_kwh"].replace(0, np.nan)).clip(0, 1) * 100
    d["self_consumption"] = (d["self_consumed_kwh"] / d["production_kwh"].replace(0, np.nan)).clip(0, 1) * 100
    return d


def monthly_kwh(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["consumption_wh", "production_wh", "grid_import_wh",
            "grid_export_wh", "self_consumed_wh"]
    if "battery_charge_wh" in df.columns:
        cols += ["battery_charge_wh", "battery_discharge_wh"]
    m = df[cols].resample("MS").sum() / 1000
    m.columns = [c.replace("_wh", "_kwh") for c in m.columns]
    m["self_sufficiency"] = (1 - m["grid_import_kwh"] / m["consumption_kwh"].replace(0, np.nan)).clip(0, 1) * 100
    m["self_consumption"] = (m["self_consumed_kwh"] / m["production_kwh"].replace(0, np.nan)).clip(0, 1) * 100
    return m


# ── Plot helpers ──────────────────────────────────────────────────────────────

def _save_or_show(fig, path: str | None, name: str):
    if path:
        out = Path(path) / f"{name}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        print(f"  Saved -> {out}")
        plt.close(fig)
    else:
        plt.tight_layout()
        plt.show()


def _kwh_formatter(x, _):
    return f"{x:,.0f}"


# ── Plot 1: Overview time series (daily) ──────────────────────────────────────

def plot_overview(df: pd.DataFrame, save_dir: str | None = None):
    d = daily_kwh(df)
    # Rolling 7-day smoothing for readability
    roll = d[["consumption_kwh", "production_kwh"]].rolling(7, center=True).mean()

    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True)
    fig.suptitle("Daily Energy Overview", fontsize=16, fontweight="bold", y=0.98)

    # Panel 1: consumption vs production
    ax = axes[0]
    ax.fill_between(d.index, d["consumption_kwh"], alpha=0.25, color=PALETTE["consumption"])
    ax.fill_between(d.index, d["production_kwh"],  alpha=0.25, color=PALETTE["production"])
    ax.plot(roll.index, roll["consumption_kwh"], color=PALETTE["consumption"], lw=1.5, label="Consumption (7d avg)")
    ax.plot(roll.index, roll["production_kwh"],  color=PALETTE["production"],  lw=1.5, label="Production (7d avg)")
    ax.set_ylabel("kWh / day")
    ax.legend(loc="upper right")
    ax.grid(True)
    ax.set_title("Consumption vs Solar Production")

    # Panel 2: grid flows
    ax = axes[1]
    ax.fill_between(d.index,  d["grid_import_kwh"], alpha=0.4, color=PALETTE["grid_import"], label="Grid import")
    ax.fill_between(d.index, -d["grid_export_kwh"], alpha=0.4, color=PALETTE["grid_export"], label="Grid export")
    ax.axhline(0, color="#EBEBF5", lw=0.5)
    ax.set_ylabel("kWh / day")
    ax.legend(loc="upper right")
    ax.grid(True)
    ax.set_title("Grid Import / Export  (export shown negative)")

    # Panel 3: self-sufficiency
    ax = axes[2]
    ax.fill_between(d.index, d["self_sufficiency"].rolling(7, center=True).mean(),
                    alpha=0.5, color=PALETTE["self_sufficiency"])
    ax.axhline(50, color="#EBEBF5", lw=0.7, ls=":")
    ax.set_ylim(0, 100)
    ax.set_ylabel("% / day")
    ax.set_xlabel("Date")
    ax.grid(True)
    ax.set_title("Self-Sufficiency Rate  (7-day rolling)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

    fig.subplots_adjust(hspace=0.35)
    _save_or_show(fig, save_dir, "01_overview")


# ── Plot 2: Monthly energy balance bar chart ──────────────────────────────────

def plot_monthly_balance(df: pd.DataFrame, save_dir: str | None = None):
    m = monthly_kwh(df)
    labels = m.index.strftime("%b\n%Y")
    x = np.arange(len(m))
    w = 0.35

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(max(14, len(m) * 0.55), 11), sharex=True)
    fig.suptitle("Monthly Energy Balance", fontsize=16, fontweight="bold", y=0.98)

    # Stacked bar: self-consumed + exported = production; import + self-consumed = consumption
    ax1.bar(x - w/2, m["self_consumed_kwh"],  w, label="Self-consumed solar", color=PALETTE["production"],  alpha=0.85)
    ax1.bar(x - w/2, m["grid_export_kwh"],    w, label="Solar exported",      color=PALETTE["grid_export"], alpha=0.85,
            bottom=m["self_consumed_kwh"])
    ax1.bar(x + w/2, m["self_consumed_kwh"],  w, label="_nolegend_",          color=PALETTE["production"],  alpha=0.85)
    ax1.bar(x + w/2, m["grid_import_kwh"],    w, label="Grid import",         color=PALETTE["grid_import"], alpha=0.85,
            bottom=m["self_consumed_kwh"])

    ax1.text(-0.3, 1.02, "← Solar", transform=ax1.transAxes, color=PALETTE["production"])
    ax1.text( 0.5, 1.02, "← Consumption", transform=ax1.transAxes, color=PALETTE["consumption"])
    ax1.set_ylabel("kWh")
    ax1.legend(loc="upper left")
    ax1.grid(axis="y")
    ax1.set_title("Solar breakdown (left bars) vs Consumption breakdown (right bars)")

    # Self-sufficiency + self-consumption rates
    ax2.plot(x, m["self_sufficiency"], "o-", color=PALETTE["self_sufficiency"], lw=2, ms=5, label="Self-sufficiency %")
    ax2.plot(x, m["self_consumption"], "s-", color=PALETTE["self_consumption"], lw=2, ms=5, label="Self-consumption %")
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("%")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, fontsize=8)
    ax2.legend(loc="upper left")
    ax2.grid(axis="y")
    ax2.set_title("Monthly Rates")

    fig.subplots_adjust(hspace=0.25)
    _save_or_show(fig, save_dir, "02_monthly_balance")


# ── Plot 3: Hourly heatmaps ───────────────────────────────────────────────────

def plot_heatmaps(df: pd.DataFrame, save_dir: str | None = None):
    tmp = df.copy()
    tmp["hour"]  = tmp.index.hour
    tmp["month"] = tmp.index.month

    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    def pivot(col):
        p = tmp.groupby(["month", "hour"])[col].mean().unstack("hour")
        p.index = [month_names[i-1] for i in p.index]
        return p / 1000  # W → kW average

    fig, axes = plt.subplots(1, 2, figsize=(18, 7))
    fig.suptitle("Average Power by Hour of Day & Month  (kW)", fontsize=15, fontweight="bold")

    for ax, col, title, cmap in [
        (axes[0], "consumption_w", "Consumption", "YlOrRd"),
        (axes[1], "production_w",  "Solar Production", "YlOrBr"),
    ]:
        data = pivot(col)
        sns.heatmap(data, ax=ax, cmap=cmap, annot=False, fmt=".2f",
                    linewidths=0.3, linecolor="#1C1C1E",
                    cbar_kws={"label": "avg kW", "shrink": 0.8})
        ax.set_title(title, fontsize=13)
        ax.set_xlabel("Hour of day")
        ax.set_ylabel("")
        ax.tick_params(axis="both", labelsize=9)

    fig.subplots_adjust(wspace=0.1)
    _save_or_show(fig, save_dir, "03_heatmaps")


# ── Plot 4: Day-of-week patterns ──────────────────────────────────────────────

def plot_weekday_patterns(df: pd.DataFrame, save_dir: str | None = None):
    tmp = df.copy()
    tmp["hour"]    = tmp.index.hour + tmp.index.minute / 60
    tmp["weekday"] = tmp.index.dayofweek  # 0=Mon … 6=Sun
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.suptitle("Average Consumption by Time of Day — Weekday vs Weekend", fontsize=14, fontweight="bold")

    colors_wd = plt.cm.Blues(np.linspace(0.35, 0.85, 5))
    colors_we = plt.cm.Oranges(np.linspace(0.45, 0.85, 2))

    for d in range(7):
        subset = tmp[tmp["weekday"] == d].groupby("hour")["consumption_w"].mean() / 1000
        color = colors_wd[d] if d < 5 else colors_we[d - 5]
        lw = 2 if d >= 5 else 1
        ax.plot(subset.index, subset.values, color=color, lw=lw, label=day_names[d], alpha=0.9)

    ax.set_xlabel("Hour of day")
    ax.set_ylabel("Average power (kW)")
    ax.set_xticks(range(0, 25, 2))
    ax.legend(loc="upper left", ncol=2)
    ax.grid(True)
    _save_or_show(fig, save_dir, "04_weekday_patterns")


# ── Plot 5: Seasonal solar production ─────────────────────────────────────────

def plot_seasonal(df: pd.DataFrame, save_dir: str | None = None):
    tmp = df.copy()
    tmp["hour"]   = tmp.index.hour + tmp.index.minute / 60
    tmp["season"] = tmp.index.month.map({
        12: "Winter", 1: "Winter", 2: "Winter",
        3: "Spring",  4: "Spring", 5: "Spring",
        6: "Summer",  7: "Summer", 8: "Summer",
        9: "Autumn", 10: "Autumn", 11: "Autumn",
    })
    season_order  = ["Winter", "Spring", "Summer", "Autumn"]
    season_colors = {"Winter": "#5B9BD5", "Spring": "#70AD47", "Summer": "#FFC000", "Autumn": "#E05252"}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle("Seasonal Patterns", fontsize=15, fontweight="bold")

    # Average daily production curve per season
    for s in season_order:
        subset = tmp[tmp["season"] == s].groupby("hour")["production_w"].mean() / 1000
        ax1.fill_between(subset.index, subset.values, alpha=0.25, color=season_colors[s])
        ax1.plot(subset.index, subset.values, color=season_colors[s], lw=2, label=s)
    ax1.set_title("Average Solar Production Curve by Season")
    ax1.set_xlabel("Hour of day")
    ax1.set_ylabel("Average power (kW)")
    ax1.set_xticks(range(0, 25, 2))
    ax1.legend()
    ax1.grid(True)

    # Monthly totals bar
    m = monthly_kwh(df)
    month_labels = m.index.strftime("%b %Y")
    month_seasons = m.index.month.map({
        12: "Winter", 1: "Winter", 2: "Winter",
        3: "Spring",  4: "Spring", 5: "Spring",
        6: "Summer",  7: "Summer", 8: "Summer",
        9: "Autumn", 10: "Autumn", 11: "Autumn",
    })
    bar_colors = [season_colors[s] for s in month_seasons]
    bars = ax2.bar(range(len(m)), m["production_kwh"], color=bar_colors, alpha=0.85, edgecolor="#1C1C1E")
    ax2.set_xticks(range(len(m)))
    ax2.set_xticklabels(month_labels, rotation=45, ha="right", fontsize=8)
    ax2.set_title("Monthly Solar Production")
    ax2.set_ylabel("kWh")
    ax2.grid(axis="y")

    # Legend for seasons
    from matplotlib.patches import Patch
    legend_els = [Patch(facecolor=season_colors[s], label=s) for s in season_order]
    ax2.legend(handles=legend_els, loc="upper right")

    fig.subplots_adjust(wspace=0.3)
    _save_or_show(fig, save_dir, "05_seasonal")


# ── Plot 6: Best & worst solar days ───────────────────────────────────────────

def plot_best_worst_days(df: pd.DataFrame, n: int = 5, save_dir: str | None = None):
    d = daily_kwh(df)
    # Only days with at least 6h equivalent production data
    d_valid = d[d["production_kwh"] > 0]
    best_days  = d_valid.nlargest(n, "production_kwh").index
    worst_days = d_valid[d_valid["production_kwh"] > 0].nsmallest(n, "production_kwh").index

    fig, axes = plt.subplots(1, 2, figsize=(16, 6), sharey=True)
    fig.suptitle(f"Top {n} Best & Worst Solar Days", fontsize=15, fontweight="bold")

    colors_best  = plt.cm.YlOrBr(np.linspace(0.4, 0.9, n))
    colors_worst = plt.cm.Blues(np.linspace(0.4, 0.9, n))

    for ax, days, colors, title in [
        (axes[0], best_days,  colors_best,  "Best Days"),
        (axes[1], worst_days, colors_worst, "Worst Days"),
    ]:
        for i, day in enumerate(days):
            day_data = df.loc[day.strftime("%Y-%m-%d"), "production_w"] / 1000
            hours = day_data.index.hour + day_data.index.minute / 60
            total = d.loc[day, "production_kwh"]
            ax.plot(hours, day_data.values, color=colors[i], lw=2,
                    label=f"{day.strftime('%d %b %Y')}  ({total:.1f} kWh)")
        ax.set_title(title)
        ax.set_xlabel("Hour of day")
        ax.set_ylabel("Power (kW)")
        ax.set_xticks(range(0, 25, 2))
        ax.legend(fontsize=8)
        ax.grid(True)

    _save_or_show(fig, save_dir, "06_best_worst_days")


# ── Plot 7: Battery analysis ──────────────────────────────────────────────────

def plot_battery(df: pd.DataFrame, save_dir: str | None = None):
    if "battery_charge_wh" not in df.columns or df["battery_charge_wh"].sum() == 0:
        print("  [skip] No battery data found.")
        return

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle("Battery Analysis", fontsize=15, fontweight="bold")

    d = daily_kwh(df)

    # Daily charge / discharge
    ax = axes[0, 0]
    ax.fill_between(d.index,  d["battery_charge_kwh"],    alpha=0.6, color=PALETTE["battery_charge"],    label="Charge")
    ax.fill_between(d.index, -d["battery_discharge_kwh"], alpha=0.6, color=PALETTE["battery_discharge"], label="Discharge")
    ax.axhline(0, color="#EBEBF5", lw=0.5)
    ax.set_title("Daily Battery Activity")
    ax.set_ylabel("kWh")
    ax.legend()
    ax.grid(True)

    # Hourly average charge / discharge by month
    tmp = df.copy()
    tmp["hour"]  = tmp.index.hour
    tmp["month"] = tmp.index.month
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    for ax, col, title, color in [
        (axes[0, 1], "battery_charge_w",    "Avg Charge Power by Hour & Month",    "Blues"),
        (axes[1, 0], "battery_discharge_w", "Avg Discharge Power by Hour & Month", "Purples"),
    ]:
        pivot = tmp.groupby(["month", "hour"])[col].mean().unstack("hour") / 1000
        pivot.index = [month_names[i-1] for i in pivot.index]
        sns.heatmap(pivot, ax=ax, cmap=color, linewidths=0.3, linecolor="#1C1C1E",
                    cbar_kws={"label": "avg kW", "shrink": 0.8})
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Hour")
        ax.tick_params(labelsize=8)

    # Battery throughput by month
    ax = axes[1, 1]
    m = monthly_kwh(df)
    x = np.arange(len(m))
    w = 0.35
    ax.bar(x - w/2, m["battery_charge_kwh"],    w, color=PALETTE["battery_charge"],    alpha=0.85, label="Charge")
    ax.bar(x + w/2, m["battery_discharge_kwh"],  w, color=PALETTE["battery_discharge"], alpha=0.85, label="Discharge")
    ax.set_xticks(x)
    ax.set_xticklabels(m.index.strftime("%b\n%Y"), fontsize=7)
    ax.set_ylabel("kWh")
    ax.set_title("Monthly Battery Throughput")
    ax.legend()
    ax.grid(axis="y")

    fig.subplots_adjust(hspace=0.4, wspace=0.35)
    _save_or_show(fig, save_dir, "07_battery")


# ── Plot 8: Cost analysis ─────────────────────────────────────────────────────

def plot_cost(df: pd.DataFrame, tariff_import: float, tariff_export: float,
              save_dir: str | None = None):
    m = monthly_kwh(df)
    m["cost_no_solar"]   = m["consumption_kwh"] * tariff_import
    m["cost_with_solar"] = m["grid_import_kwh"] * tariff_import - m["grid_export_kwh"] * tariff_export
    m["savings"]         = m["cost_no_solar"] - m["cost_with_solar"]

    cumulative_savings = m["savings"].cumsum()

    fig, axes = plt.subplots(2, 1, figsize=(16, 10))
    fig.suptitle(f"Cost Analysis  (import: {tariff_import:.4f} €/kWh | export: {tariff_export:.4f} €/kWh)",
                 fontsize=14, fontweight="bold")

    # Monthly costs
    ax = axes[0]
    x = np.arange(len(m))
    w = 0.35
    ax.bar(x - w/2, m["cost_no_solar"],   w, color="#7F8C8D", alpha=0.85, label="Without solar (estimated)")
    ax.bar(x + w/2, m["cost_with_solar"], w, color=PALETTE["production"], alpha=0.85, label="With solar (actual)")
    ax.bar(x + w/2, -m["grid_export_kwh"] * tariff_export, w,
           color=PALETTE["grid_export"], alpha=0.85,
           bottom=m["grid_import_kwh"] * tariff_import, label="Export credit")
    ax.set_xticks(x)
    ax.set_xticklabels(m.index.strftime("%b\n%Y"), fontsize=7)
    ax.set_ylabel("€")
    ax.set_title("Monthly Electricity Cost")
    ax.legend()
    ax.grid(axis="y")

    # Cumulative savings
    ax = axes[1]
    ax.fill_between(m.index, cumulative_savings, alpha=0.4, color=PALETTE["grid_export"])
    ax.plot(m.index, cumulative_savings, color=PALETTE["grid_export"], lw=2)
    ax.axhline(0, color="#EBEBF5", lw=0.5)
    total_saved = cumulative_savings.iloc[-1]
    ax.annotate(f"Total saved: {total_saved:+,.0f} €",
                xy=(m.index[-1], total_saved),
                xytext=(-120, -30), textcoords="offset points",
                color=PALETTE["grid_export"], fontsize=12, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=PALETTE["grid_export"]))
    ax.set_ylabel("€")
    ax.set_title("Cumulative Savings from Solar")
    ax.grid(True)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

    fig.subplots_adjust(hspace=0.4)
    _save_or_show(fig, save_dir, "08_cost_analysis")

    # Print cost summary
    span_years = (df.index[-1] - df.index[0]).days / 365.25
    print(f"\n  Cost summary  ({tariff_import:.4f} €/kWh import | {tariff_export:.4f} €/kWh export)")
    print(f"  Estimated bill without solar : {m['cost_no_solar'].sum():>10,.2f} €")
    print(f"  Actual bill with solar       : {m['cost_with_solar'].sum():>10,.2f} €")
    print(f"  Total savings                : {total_saved:>+10,.2f} €  over {span_years:.1f} years")
    print(f"  Average savings/year         : {total_saved/span_years:>+10,.2f} €/yr\n")


# ── Plot 9: Year-over-year comparison ─────────────────────────────────────────

def plot_yoy(df: pd.DataFrame, save_dir: str | None = None):
    tmp = df.copy()
    tmp["month_num"] = tmp.index.month
    tmp["year"]      = tmp.index.year

    years = sorted(tmp["year"].unique())
    if len(years) < 2:
        print("  [skip] Need at least 2 years of data for YoY comparison.")
        return

    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    colors = plt.cm.tab10(np.linspace(0, 0.7, len(years)))

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle("Year-over-Year Comparison  (monthly totals)", fontsize=14, fontweight="bold")

    for ax, col, title in [
        (axes[0], "consumption_wh", "Consumption"),
        (axes[1], "production_wh",  "Solar Production"),
    ]:
        for i, year in enumerate(years):
            monthly = (tmp[tmp["year"] == year]
                       .groupby("month_num")[col].sum() / 1000)
            # Align to full 12-month index
            monthly = monthly.reindex(range(1, 13))
            ax.plot(range(1, 13), monthly.values, "o-", color=colors[i],
                    lw=2, ms=5, label=str(year), alpha=0.9)
        ax.set_xticks(range(1, 13))
        ax.set_xticklabels(month_names)
        ax.set_ylabel("kWh")
        ax.set_title(title)
        ax.legend()
        ax.grid(True)

    _save_or_show(fig, save_dir, "09_year_over_year")


# ── Plot 10: Single day drill-down ────────────────────────────────────────────

def plot_single_day(df: pd.DataFrame, date_str: str, save_dir: str | None = None):
    try:
        day = pd.Timestamp(date_str)
    except Exception:
        print(f"  [error] Invalid date: {date_str}  (expected YYYY-MM-DD)")
        return

    mask = (df.index.date == day.date())
    if not mask.any():
        print(f"  [error] No data found for {date_str}")
        return

    day_df = df[mask]
    hours  = day_df.index.hour + day_df.index.minute / 60

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(f"Day Drill-Down: {day.strftime('%A, %d %B %Y')}", fontsize=14, fontweight="bold")

    ax = axes[0]
    ax.fill_between(hours, day_df["consumption_w"] / 1000, alpha=0.35, color=PALETTE["consumption"])
    ax.fill_between(hours, day_df["production_w"]  / 1000, alpha=0.35, color=PALETTE["production"])
    ax.plot(hours, day_df["consumption_w"] / 1000, color=PALETTE["consumption"], lw=2, label="Consumption")
    ax.plot(hours, day_df["production_w"]  / 1000, color=PALETTE["production"],  lw=2, label="Production")
    if "battery_charge_w" in day_df.columns:
        ax.plot(hours, day_df["battery_charge_w"]    / 1000, "--", color=PALETTE["battery_charge"],    lw=1.5, label="Battery charge")
        ax.plot(hours, day_df["battery_discharge_w"] / 1000, "--", color=PALETTE["battery_discharge"], lw=1.5, label="Battery discharge")
    ax.set_ylabel("Power (kW)")
    ax.legend()
    ax.grid(True)

    ax = axes[1]
    ax.fill_between(hours,  day_df["grid_import_w"] / 1000, alpha=0.5, color=PALETTE["grid_import"], label="Grid import")
    ax.fill_between(hours, -day_df["grid_export_w"] / 1000, alpha=0.5, color=PALETTE["grid_export"], label="Grid export")
    ax.axhline(0, color="#EBEBF5", lw=0.5)
    ax.set_ylabel("Power (kW)")
    ax.set_xlabel("Hour of day")
    ax.set_xticks(range(0, 25, 1))
    ax.set_xticklabels([str(h) for h in range(0, 25)], fontsize=8)
    ax.legend()
    ax.grid(True)
    ax.set_title("Grid Flows  (export shown negative)")

    daily_totals = daily_kwh(df)
    if day.normalize() in daily_totals.index:
        row = daily_totals.loc[day.normalize()]
        fig.text(0.12, 0.01,
                 f"Consumption: {row['consumption_kwh']:.2f} kWh  |  "
                 f"Production: {row['production_kwh']:.2f} kWh  |  "
                 f"Import: {row['grid_import_kwh']:.2f} kWh  |  "
                 f"Export: {row['grid_export_kwh']:.2f} kWh  |  "
                 f"Self-suff.: {row['self_sufficiency']:.1f}%",
                 fontsize=9, color="#EBEBF5")

    fig.subplots_adjust(hspace=0.3, bottom=0.08)
    _save_or_show(fig, save_dir, f"10_day_{date_str}")


# ── Interactive menu ──────────────────────────────────────────────────────────

MENU_ITEMS = [
    ("1",  "Overview time series (daily, with rolling average)"),
    ("2",  "Monthly energy balance"),
    ("3",  "Hourly heatmaps (consumption & production by month)"),
    ("4",  "Day-of-week patterns"),
    ("5",  "Seasonal solar curves"),
    ("6",  "Best & worst solar days"),
    ("7",  "Battery analysis"),
    ("8",  "Cost analysis"),
    ("9",  "Year-over-year comparison"),
    ("10", "Single day drill-down"),
    ("a",  "All plots at once"),
    ("q",  "Quit"),
]

def interactive_menu(df: pd.DataFrame, tariff_import: float, tariff_export: float,
                     save_dir: str | None):
    print_summary(df)

    while True:
        print("\n  --- ANALYSIS MENU -------------------------------------------")
        for key, desc in MENU_ITEMS:
            print(f"    [{key:>2}] {desc}")
        print("  -------------------------------------------------------------")

        choice = input("  Select > ").strip().lower()

        if choice == "q":
            print("  Bye!")
            break
        elif choice in ("1", "a"):
            print("  Plotting overview…")
            plot_overview(df, save_dir)
        if choice in ("2", "a"):
            print("  Plotting monthly balance…")
            plot_monthly_balance(df, save_dir)
        if choice in ("3", "a"):
            print("  Plotting heatmaps…")
            plot_heatmaps(df, save_dir)
        if choice in ("4", "a"):
            print("  Plotting weekday patterns…")
            plot_weekday_patterns(df, save_dir)
        if choice in ("5", "a"):
            print("  Plotting seasonal analysis…")
            plot_seasonal(df, save_dir)
        if choice in ("6", "a"):
            print("  Plotting best/worst days…")
            plot_best_worst_days(df, save_dir=save_dir)
        if choice in ("7", "a"):
            print("  Plotting battery analysis…")
            plot_battery(df, save_dir)
        if choice in ("8", "a"):
            if choice == "8":
                try:
                    ti = float(input(f"  Import tariff €/kWh [{tariff_import}]: ").strip() or tariff_import)
                    te = float(input(f"  Export tariff €/kWh [{tariff_export}]: ").strip() or tariff_export)
                except ValueError:
                    ti, te = tariff_import, tariff_export
            else:
                ti, te = tariff_import, tariff_export
            print("  Plotting cost analysis…")
            plot_cost(df, ti, te, save_dir)
        if choice in ("9", "a"):
            print("  Plotting year-over-year…")
            plot_yoy(df, save_dir)
        if choice in ("10",):
            date_str = input("  Enter date (YYYY-MM-DD): ").strip()
            plot_single_day(df, date_str, save_dir)
        if choice == "a":
            # single-day drill-down: pick the best production day automatically
            best_day = daily_kwh(df)["production_kwh"].idxmax().strftime("%Y-%m-%d")
            print(f"  Auto-selecting best solar day ({best_day}) for drill-down…")
            plot_single_day(df, best_day, save_dir)


# ── CLI entry point ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Household Energy Analyzer – solar & consumption CSV analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python energy_analyzer.py data.csv
  python energy_analyzer.py data.csv --plot 1 3 5
  python energy_analyzer.py data.csv --plot a --save ./charts
  python energy_analyzer.py data.csv --plot 8 --import-tariff 0.2276 --export-tariff 0.07
  python energy_analyzer.py data.csv --plot 10 --day 2023-07-15
        """,
    )
    p.add_argument("csv", help="Path to the energy CSV file")
    p.add_argument("--plot", nargs="+", metavar="N",
                   help="Plot ID(s) to generate directly (1–10, a=all). Omit for interactive menu.")
    p.add_argument("--save", metavar="DIR",
                   help="Save plots as PNG files in this directory instead of showing them")
    p.add_argument("--import-tariff", type=float, default=0.2276,
                   help="Electricity import tariff in €/kWh (default: 0.2276)")
    p.add_argument("--export-tariff", type=float, default=0.07,
                   help="Solar export tariff in €/kWh (default: 0.07)")
    p.add_argument("--day", metavar="YYYY-MM-DD",
                   help="Date for the single-day drill-down plot (plot 10)")
    p.add_argument("--summary", action="store_true",
                   help="Print summary statistics and exit")
    return p


def main():
    args = build_parser().parse_args()

    print(f"\n  Loading {args.csv} …", end="", flush=True)
    df = load_csv(args.csv)
    print(f" {len(df):,} rows loaded.\n")

    if args.summary:
        print_summary(df)
        return

    save_dir = args.save
    if save_dir:
        Path(save_dir).mkdir(parents=True, exist_ok=True)

    ti = args.import_tariff
    te = args.export_tariff

    if args.plot:
        choices = [c.lower() for c in args.plot]
        print_summary(df)
        if "1" in choices or "a" in choices: plot_overview(df, save_dir)
        if "2" in choices or "a" in choices: plot_monthly_balance(df, save_dir)
        if "3" in choices or "a" in choices: plot_heatmaps(df, save_dir)
        if "4" in choices or "a" in choices: plot_weekday_patterns(df, save_dir)
        if "5" in choices or "a" in choices: plot_seasonal(df, save_dir)
        if "6" in choices or "a" in choices: plot_best_worst_days(df, save_dir=save_dir)
        if "7" in choices or "a" in choices: plot_battery(df, save_dir)
        if "8" in choices or "a" in choices: plot_cost(df, ti, te, save_dir)
        if "9" in choices or "a" in choices: plot_yoy(df, save_dir)
        if "10" in choices or "a" in choices:
            day = args.day or daily_kwh(df)["production_kwh"].idxmax().strftime("%Y-%m-%d")
            plot_single_day(df, day, save_dir)
    else:
        interactive_menu(df, ti, te, save_dir)


if __name__ == "__main__":
    main()
