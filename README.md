# Energy Analyzer

A Python 3 command-line tool for analyzing household electricity consumption and solar panel production from Huawei SUN2000 / Smart Meter CSV exports. Produces 10 different charts covering everything from high-level overviews to single-day 15-minute drill-downs.

---

## Requirements

Python 3.10+ with the following packages:

```
pip install pandas numpy matplotlib seaborn
```

---

## CSV Format

The tool was built for exports from the **Huawei FusionSolar** portal (or compatible inverter/smart meter systems). The expected columns are:

| Column | Description |
|---|---|
| `Date` | ISO 8601 timestamp with timezone (`2023-07-15T14:00:00+02:00`) |
| `Consumption` | Household power draw in Watts |
| `Production` | Solar inverter output in Watts |
| `Battery Charging` | Battery charge power in Watts (optional) |
| `Battery Discharging` | Battery discharge power in Watts (optional) |
| `Inverter … currentPower` | Raw inverter power reading (used if present) |
| `Smart Meter … currentPower` | Grid meter reading — used to derive import/export |

Data is expected at **15-minute intervals**. Columns with multi-line names in the CSV header are handled automatically.

Grid import and export are derived from the smart meter reading when available, or estimated from the consumption/production balance otherwise.

---

## Quick Start

```bash
# Launch the interactive menu
python energy_analyzer.py data.csv

# Print summary statistics only
python energy_analyzer.py data.csv --summary

# Generate a specific plot (shown in a window)
python energy_analyzer.py data.csv --plot 1

# Generate all plots and save them as PNG files
python energy_analyzer.py data.csv --plot a --save ./charts
```

---

## Interactive Menu

Running the script without `--plot` opens an interactive menu. The data summary is printed first, then you can select analyses by number repeatedly until you quit with `q`.

```
python energy_analyzer.py data.csv
```

```
============================================================
  ENERGY SUMMARY
============================================================
  Period          : 2022-03-03  ->  2024-10-06  (949 days)
  Samples         : 90,799  (15-min intervals)
------------------------------------------------------------
  Total consumption :   19,970.1 kWh  (7,686 kWh/yr)
  Total production  :   10,512.0 kWh  (4,046 kWh/yr)
  Grid import       :    7,183.4 kWh
  Grid export       :        0.0 kWh
  Self-sufficiency  :      64.0 %   (consumption met by solar)
  Self-consumption  :     100.0 %   (production used on-site)
------------------------------------------------------------
  Peak consumption  :   18,184 W  at 2023-12-16 14:15
  Peak production   :    4,125 W  at 2022-05-29 14:15
  Avg daily usage   :    21.04 kWh/day
============================================================

  --- ANALYSIS MENU -------------------------------------------
    [ 1] Overview time series (daily, with rolling average)
    [ 2] Monthly energy balance
    [ 3] Hourly heatmaps (consumption & production by month)
    [ 4] Day-of-week patterns
    [ 5] Seasonal solar curves
    [ 6] Best & worst solar days
    [ 7] Battery analysis
    [ 8] Cost analysis
    [ 9] Year-over-year comparison
    [10] Single day drill-down
    [ a] All plots at once
    [ q] Quit
  -------------------------------------------------------------
  Select >
```

When you select **8** (Cost analysis) from the interactive menu, you will be prompted to enter your tariffs before the chart is generated:

```
  Select > 8
  Import tariff €/kWh [0.2276]:
  Export tariff €/kWh [0.07]:
```

Press Enter to accept the defaults shown in brackets.

---

## Command-Line Reference

```
python energy_analyzer.py <csv> [options]
```

| Argument | Description |
|---|---|
| `csv` | Path to the energy CSV file (required) |
| `--plot N [N ...]` | Plot ID(s) to generate: `1`–`10` or `a` for all |
| `--save DIR` | Save plots as PNG files to this folder instead of displaying them |
| `--summary` | Print the summary statistics table and exit immediately |
| `--import-tariff X` | Electricity import tariff in €/kWh (default: `0.2276`) |
| `--export-tariff X` | Solar export / feed-in tariff in €/kWh (default: `0.07`) |
| `--day YYYY-MM-DD` | Date to use for the single-day drill-down (plot 10) |

---

## All 10 Analyses

### Plot 1 — Overview Time Series

```bash
python energy_analyzer.py data.csv --plot 1
```

A three-panel chart covering the full date range:

- **Top panel:** daily consumption vs solar production, with a 7-day rolling average overlaid to smooth noise.
- **Middle panel:** daily grid import and export (export shown as negative).
- **Bottom panel:** daily self-sufficiency rate with a 7-day rolling average.

Useful for spotting long-term trends, seasonal swings, and the impact of weather events.

---

### Plot 2 — Monthly Energy Balance

```bash
python energy_analyzer.py data.csv --plot 2
```

A two-panel monthly chart:

- **Top panel:** stacked bar chart. Left bar = solar breakdown (self-consumed solar + exported solar). Right bar = consumption breakdown (self-consumed solar + grid import). This makes it easy to see how much of your production you actually used and how much you had to top up from the grid each month.
- **Bottom panel:** monthly self-sufficiency % and self-consumption % plotted as lines.

---

### Plot 3 — Hourly Heatmaps

```bash
python energy_analyzer.py data.csv --plot 3
```

Two side-by-side heatmaps showing the **average power in kW** for every hour of the day across every month of the year:

- **Left:** consumption — reveals morning/evening peaks and seasonal patterns in household usage.
- **Right:** solar production — shows when and how much energy the panels produce throughout the year.

---

### Plot 4 — Day-of-Week Patterns

```bash
python energy_analyzer.py data.csv --plot 4
```

Average consumption curves for each day of the week plotted on the same axes. Weekdays (Monday–Friday) are shown in blue shades; weekend days (Saturday, Sunday) in orange shades with a heavier line weight. Useful for identifying whether your habits differ between working days and weekends.

---

### Plot 5 — Seasonal Solar Curves

```bash
python energy_analyzer.py data.csv --plot 5
```

A two-panel seasonal breakdown:

- **Left:** average solar production power curve throughout the day for each season (Winter, Spring, Summer, Autumn). Shows how sunrise/sunset times and sun elevation affect the production profile.
- **Right:** monthly solar production totals as a bar chart, colour-coded by season.

---

### Plot 6 — Best & Worst Solar Days

```bash
python energy_analyzer.py data.csv --plot 6
```

The **top 5 best** and **top 5 worst** solar production days plotted as 15-minute resolution power curves on two side-by-side charts. Each curve is labelled with the date and total kWh produced that day. Useful for understanding your system's ceiling and floor performance.

---

### Plot 7 — Battery Analysis

```bash
python energy_analyzer.py data.csv --plot 7
```

Requires `Battery Charging` and `Battery Discharging` columns with non-zero data. Produces four panels:

- **Daily battery activity:** charge (positive) and discharge (negative) over the full date range.
- **Charge heatmap:** average charge power by hour of day and month.
- **Discharge heatmap:** average discharge power by hour of day and month.
- **Monthly throughput:** total kWh charged and discharged per month as grouped bars.

If no battery data is present, this plot is skipped with a notice.

---

### Plot 8 — Cost Analysis

```bash
# Using default tariffs (0.2276 €/kWh import, 0.07 €/kWh export)
python energy_analyzer.py data.csv --plot 8

# With your actual tariffs
python energy_analyzer.py data.csv --plot 8 --import-tariff 0.2072 --export-tariff 0.13
```

Produces a two-panel cost chart and prints a financial summary to the terminal:

- **Top panel:** grouped monthly bar chart comparing what your bill would have been without solar vs your actual bill with solar, including any export credit.
- **Bottom panel:** cumulative savings from solar over the full period, with the total annotated on the chart.

Terminal output example:

```
  Cost summary  (0.2276 €/kWh import | 0.0700 €/kWh export)
  Estimated bill without solar :   4,545.20 €
  Actual bill with solar       :   1,634.95 €
  Total savings                :  +2,910.25 €  over 2.6 years
  Average savings/year         :  +1,121.28 €/yr
```

---

### Plot 9 — Year-over-Year Comparison

```bash
python energy_analyzer.py data.csv --plot 9
```

Monthly consumption and solar production plotted as line charts, with one line per calendar year. Makes it easy to see whether your usage is growing or shrinking and how production varies between years due to weather. Requires at least 2 years of data.

---

### Plot 10 — Single Day Drill-Down

```bash
# Specify the day explicitly
python energy_analyzer.py data.csv --plot 10 --day 2023-07-15

# Omitting --day selects the best solar production day automatically
python energy_analyzer.py data.csv --plot 10
```

Full 15-minute resolution chart for a single day:

- **Top panel:** consumption, production, and battery flows (if available) throughout the day.
- **Bottom panel:** grid import and export flows (export shown as negative).
- **Footer:** key totals for the day — consumption, production, import, export, and self-sufficiency %.

---

## Combining Plots

You can pass multiple plot IDs to `--plot` to generate several charts in one run:

```bash
# Overview + monthly balance + cost analysis
python energy_analyzer.py data.csv --plot 1 2 8 --import-tariff 0.2072 --export-tariff 0.13

# Heatmaps + seasonal + year-over-year, saved to a folder
python energy_analyzer.py data.csv --plot 3 5 9 --save ./output

# All plots, saved to a folder
python energy_analyzer.py data.csv --plot a --save ./charts
```

When `--save` is used, each plot is written as a numbered PNG file (`01_overview.png`, `02_monthly_balance.png`, etc.) and no window is opened. The folder is created automatically if it does not exist.

---

## Definitions

| Term | Definition |
|---|---|
| **Self-sufficiency** | Percentage of consumption covered by solar (including battery discharge). A value of 64% means 64% of your electricity came from your own panels. |
| **Self-consumption** | Percentage of solar production that was used on-site rather than exported. A value of 100% means none of your production was sent to the grid. |
| **Grid import** | Energy drawn from the public grid when solar production is insufficient. |
| **Grid export** | Surplus solar energy sent back to the grid (also called feed-in). |
| **Self-consumed solar** | The portion of solar production consumed directly in the home (not exported). |

---

## Grafana Dashboard

An interactive Grafana dashboard is included alongside the Python script. It stores all data in a local **InfluxDB 2** time-series database and displays it through **Grafana**, both running as Docker containers.

### Architecture

```
data_export_*.csv
       │
       │  python export_to_influx.py
       ▼
 ┌───────────────┐        ┌───────────────┐
 │  InfluxDB 2   │◄──────►│    Grafana    │  http://localhost:3000
 │  (port 8086)  │  Flux  │  (port 3000)  │
 └───────────────┘        └───────────────┘
   Docker volume            Docker volume
```

- **InfluxDB 2** stores the 15-minute interval measurements in a bucket called `electricity`.
- **`export_to_influx.py`** reads the Huawei FusionSolar CSV, derives all calculated fields (grid import/export, self-consumed energy, etc.) and batch-writes them to InfluxDB.
- **Grafana** connects to InfluxDB using the Flux query language. The datasource and dashboard are provisioned automatically on first start — no manual configuration needed.
- The dashboard is defined in `grafana/dashboards/electricity.json` and is reloaded automatically every 30 seconds when the file changes.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running (Linux containers mode)
- Python 3.10+ with the export dependencies:

```bash
pip install -r requirements_grafana.txt
```

### Installation

**1. Start the stack**

```bash
docker-compose up -d
```

On first run InfluxDB initialises its database, which takes about 10 seconds. The default credentials are defined in `.env` — change them before exposing the service to a network.

**2. Import your CSV data**

```bash
python export_to_influx.py
```

The script auto-detects any `data_export_*.csv` file in the current directory. You can also pass the path explicitly:

```bash
python export_to_influx.py path/to/your/export.csv
```

When you export a new CSV from FusionSolar (covering a longer period or updated data), re-run with `--reset` to cleanly replace the existing data:

```bash
python export_to_influx.py --reset
```

Without `--reset`, InfluxDB overwrites matching timestamps, which is also safe but does not remove rows that fall outside the new file's range.

**3. Open Grafana**

Go to **http://localhost:3000** in a browser.
Default login: `admin` / `admin` (set `GRAFANA_PASSWORD` in `.env` to change it).

The dashboard **"Home Electricity & Solar"** opens automatically as the home page.

### Dashboard panels

| Panel | What it shows |
|---|---|
| **Total Consumption** | Total kWh consumed over the selected time range |
| **Total Production** | Total kWh generated by the solar panels |
| **Grid Import** | Total kWh drawn from the public grid |
| **Self-Sufficiency** | % of consumption covered by solar — see definition below |
| **Power — Real-time** | 15-minute interval time series for consumption, production, and grid flows |
| **Daily Energy Balance** | Day-by-day bar chart in kWh |
| **Monthly Energy Balance** | Month-by-month bar chart in kWh |
| **Self-Sufficiency gauge** | Gauge showing self-sufficiency for the selected period |
| **Self-Consumption gauge** | Gauge showing self-consumption for the selected period |
| **Daily Self-Sufficiency %** | Trend line of daily self-sufficiency over time |

Use the **time range picker** in the top-right of Grafana to zoom into any period. All queries adapt automatically.

### Metric definitions (dashboard)

| Metric | Formula | Question it answers |
|---|---|---|
| **Self-Sufficiency** | `(1 − grid_import / consumption) × 100` | Of all the electricity I *consumed*, how much came from my own panels? |
| **Self-Consumption** | `self_consumed / production × 100` | Of all the electricity I *produced*, how much did I actually use myself? |

A household with high self-sufficiency is largely independent from the grid. A household with high self-consumption wastes little production (nothing is exported). Both figures can be 100% simultaneously only if production equals consumption exactly.

### Stopping and restarting the stack

```bash
# Stop containers (data is preserved in Docker volumes)
docker-compose down

# Start again
docker-compose up -d

# Stop and delete all stored data (full reset)
docker-compose down -v
```

### Updating the dashboard

Edit `grafana/dashboards/electricity.json` directly. Grafana reloads it within 30 seconds. There is no need to restart the containers.

---

## Accessing Grafana over the internet

By default Grafana is only reachable on your local network (`localhost:3000`). Three practical approaches to make it accessible remotely, in order of increasing complexity:

### Option 1 — Tailscale (recommended for personal use)

[Tailscale](https://tailscale.com) creates an encrypted mesh VPN between your devices. Once installed, your home machine gets a stable private IP that is reachable from your phone or laptop anywhere in the world, without opening any ports on your router.

1. Install Tailscale on the machine running Docker and on your remote device.
2. Sign in with the same account on both.
3. Access Grafana at `http://<tailscale-ip>:3000` from anywhere.

No domain name, no SSL certificate, no router configuration needed. The free tier supports up to 100 devices.

### Option 2 — Cloudflare Tunnel (public URL, no open ports)

[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) exposes a local service to the internet through Cloudflare's network without opening any inbound ports on your router or firewall. Traffic is encrypted end-to-end.

1. Create a free Cloudflare account and add your domain (or use a free `trycloudflare.com` subdomain).
2. Install `cloudflared` on the host machine.
3. Run:
   ```bash
   cloudflared tunnel --url http://localhost:3000
   ```
   This gives you a public HTTPS URL immediately, no configuration file needed for quick testing.
4. For a permanent setup, create a named tunnel and configure a DNS record pointing to it.

Before making Grafana public, set a strong `GRAFANA_PASSWORD` in `.env`, restart the stack, and consider disabling anonymous access by removing `GF_AUTH_ANONYMOUS_ENABLED` from `docker-compose.yml`.

### Option 3 — Reverse proxy with nginx + Let's Encrypt (self-hosted, full control)

If you already have a domain and want full control over the setup:

1. Install **nginx** and **Certbot** on the host.
2. Configure nginx as a reverse proxy forwarding `https://grafana.yourdomain.com` → `http://localhost:3000`.
3. Obtain a free TLS certificate with Certbot:
   ```bash
   certbot --nginx -d grafana.yourdomain.com
   ```
4. Forward **port 443** (HTTPS) from your router to the machine running Docker.

This approach requires a static public IP or a dynamic DNS service (e.g. DuckDNS) if your ISP assigns a dynamic IP.

### Security checklist before going public

Regardless of the method chosen:

- Change all default passwords in `.env` before exposing the service.
- Set `GF_AUTH_ANONYMOUS_ENABLED=false` in `docker-compose.yml` so unauthenticated users cannot view the dashboard.
- Do **not** expose InfluxDB (port 8086) publicly — only Grafana needs to be reachable.
- Consider enabling Grafana's built-in [HTTPS support](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#protocol) or terminating TLS at the reverse proxy / Cloudflare level.
