#!/usr/bin/env python3
"""
Export electricity CSV data to InfluxDB 2.x for Grafana dashboards.

Usage:
    python export_to_influx.py [data_export_*.csv]

If no file is specified, the script auto-detects data_export_*.csv in the
current directory.

Environment variables (override defaults in .env / docker-compose):
    INFLUX_URL    - InfluxDB URL          (default: http://localhost:8086)
    INFLUX_TOKEN  - API token             (default: my-super-secret-auth-token)
    INFLUX_ORG    - Organisation name     (default: electricity)
    INFLUX_BUCKET - Bucket name           (default: electricity)
"""

import csv
import io
import os
import sys
from pathlib import Path

import pandas as pd
from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

# ── Configuration ─────────────────────────────────────────────────────────────

INFLUX_URL    = os.getenv("INFLUX_URL",    "http://localhost:8086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN",  "my-super-secret-auth-token")
INFLUX_ORG    = os.getenv("INFLUX_ORG",    "electricity")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "electricity")

INTERVAL_H = 0.25  # 15-minute intervals


# ── CSV loading (mirrors energy_analyzer.py) ──────────────────────────────────

def load_csv(path: str) -> pd.DataFrame:
    """Parse the Huawei FusionSolar CSV and return a UTC-indexed DataFrame."""
    p = Path(path)
    raw = p.read_bytes().decode("utf-8", errors="replace")
    lines = raw.splitlines()

    # Find the first data row (starts with an ISO timestamp like "2022-...")
    header_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip().strip('"')
        if stripped.startswith("20") and "T" in stripped:
            header_end = i
            break

    header_block = "\n".join(lines[:header_end])
    reader = csv.reader(io.StringIO(header_block))
    raw_cols = []
    for row in reader:
        raw_cols.extend(row)
    raw_cols = [" ".join(c.split()) for c in raw_cols if c.strip()]

    data_block = "\n".join(lines[header_end:])
    df = pd.read_csv(io.StringIO(data_block), header=None, low_memory=False)

    n_cols = min(len(raw_cols), df.shape[1])
    df = df.iloc[:, :n_cols]
    df.columns = raw_cols[:n_cols]

    # Rename columns to standard names
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

    if "timestamp" not in df.columns:
        df.rename(columns={df.columns[0]: "timestamp"}, inplace=True)

    # Parse as UTC-aware (InfluxDB stores UTC internally)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df.dropna(subset=["timestamp"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.set_index("timestamp", inplace=True)

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).clip(lower=0)

    # Derived energy columns (Wh per 15-min interval)
    df["consumption_wh"] = df["consumption_w"] * INTERVAL_H
    df["production_wh"]  = df["production_w"]  * INTERVAL_H

    if "battery_charge_w" in df.columns:
        df["battery_charge_wh"]    = df["battery_charge_w"]    * INTERVAL_H
        df["battery_discharge_wh"] = df["battery_discharge_w"] * INTERVAL_H

    # Grid import / export
    if "meter_w" in df.columns and df["meter_w"].sum() > 0:
        df["grid_import_w"] = df["meter_w"].clip(lower=0)
        df["grid_export_w"] = 0.0
    else:
        df["grid_import_w"] = (df["consumption_w"] - df["production_w"]).clip(lower=0)
        df["grid_export_w"] = (df["production_w"]  - df["consumption_w"]).clip(lower=0)

    df["grid_import_wh"]   = df["grid_import_w"]  * INTERVAL_H
    df["grid_export_wh"]   = df["grid_export_w"]  * INTERVAL_H
    df["self_consumed_wh"] = (df["production_wh"] - df["grid_export_wh"]).clip(lower=0)

    return df


# ── InfluxDB export ────────────────────────────────────────────────────────────

EXPORT_FIELDS = [
    "consumption_w", "production_w", "grid_import_w", "grid_export_w",
    "consumption_wh", "production_wh", "grid_import_wh", "grid_export_wh",
    "self_consumed_wh",
]
OPTIONAL_FIELDS = [
    "battery_charge_w", "battery_discharge_w",
    "battery_charge_wh", "battery_discharge_wh",
]

CHUNK_SIZE = 5_000


def reset_bucket(client: InfluxDBClient, start: str, stop: str) -> None:
    """Delete all 'electricity' measurement data in the given time range."""
    delete_api = client.delete_api()
    delete_api.delete(start, stop, '_measurement="electricity"',
                      bucket=INFLUX_BUCKET, org=INFLUX_ORG)
    print(f"  Cleared existing data ({start} → {stop}).")


def export_to_influx(df: pd.DataFrame, reset: bool = False) -> None:
    fields = EXPORT_FIELDS + [f for f in OPTIONAL_FIELDS if f in df.columns]
    export_df = df[[c for c in fields if c in df.columns]].copy()

    # Ensure all values are float (InfluxDB rejects mixed types)
    export_df = export_df.astype(float)

    print(f"Connecting to InfluxDB at {INFLUX_URL} …")
    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        health = client.health()
        if health.status != "pass":
            sys.exit(f"[ERROR] InfluxDB health check failed: {health.message}")

        if reset:
            # Wipe the full span covered by this file so stale rows are removed
            t_start = df.index[0].strftime("%Y-%m-%dT%H:%M:%SZ")
            t_stop  = df.index[-1].strftime("%Y-%m-%dT%H:%M:%SZ")
            reset_bucket(client, t_start, t_stop)

        print(f"  Connected. Writing {len(export_df):,} rows to bucket '{INFLUX_BUCKET}' …")

        write_api = client.write_api(write_options=SYNCHRONOUS)
        total = len(export_df)
        written = 0

        for start in range(0, total, CHUNK_SIZE):
            chunk = export_df.iloc[start : start + CHUNK_SIZE]
            write_api.write(
                bucket=INFLUX_BUCKET,
                org=INFLUX_ORG,
                record=chunk,
                data_frame_measurement_name="electricity",
                data_frame_tag_columns=[],
            )
            written += len(chunk)
            pct = written / total * 100
            print(f"  {written:,}/{total:,} rows written ({pct:.0f}%) …", end="\r")

        print(f"\n  Done. {written:,} data points in bucket '{INFLUX_BUCKET}'.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export electricity CSV to InfluxDB.")
    parser.add_argument("csv", nargs="?", help="Path to data_export_*.csv (auto-detected if omitted)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete existing data for the CSV's time span before writing")
    args = parser.parse_args()

    if args.csv:
        csv_path = args.csv
    else:
        candidates = sorted(Path(".").glob("data_export_*.csv"))
        if not candidates:
            sys.exit("No CSV file found. Usage: python export_to_influx.py [file.csv] [--reset]")
        csv_path = str(candidates[0])
        print(f"Auto-detected: {csv_path}")

    print(f"Loading {csv_path} …")
    df = load_csv(csv_path)
    print(f"Loaded {len(df):,} rows  [{df.index[0].date()} → {df.index[-1].date()}]")
    export_to_influx(df, reset=args.reset)
