# Garmin Personal Dashboard

A minimal, insight-dense Streamlit dashboard built on top of this `garminconnect`
fork. It pulls your Garmin Connect history into local cache files and renders
training, running, vitals, and activity analytics.

## What it shows

- **Overview** — VO₂ max trend, weekly running volume, resting HR, daily steps,
  and a 12-month running calendar heatmap.
- **Running & Training** — race predictions, pace trend, heart-rate-vs-pace
  efficiency, a Fitness/Fatigue/Form training-load model (CTL/ATL), distance per
  run, HR-zone distribution, personal records, and a recent-runs table.
- **Vitals** — stress, Body Battery range, heart-rate range, respiration, and
  weekly intensity minutes vs goal.
- **Steps & Activity** — steps, calories, floors, active-vs-sedentary time, and a
  steps calendar heatmap.
- **Sleep** — duration, stages, and sleep score (shown only when sleep data exists).

Distances default to **miles / min-per-mile** (your Garmin unit setting); toggle
to kilometers in the sidebar.

## Setup

```bash
# from the repo root
python3 -m venv .venv --copies
source .venv/bin/activate
pip install -e .
pip install curl_cffi streamlit plotly pandas
```

## 1. Authenticate (one time)

Logs in and caches a refreshable token in `~/.garminconnect`:

```bash
EMAIL='you@example.com' PASSWORD='...' python ../example.py
```

The token auto-refreshes, so you normally only do this once.

## 2. Fetch data

```bash
cd dashboard
python fetch_data.py --days 180 --history-days 365
```

- Raw per-day responses are cached in `data/raw/`, so re-runs are **incremental**
  (only missing days are fetched).
- To refresh just the latest days: `python fetch_data.py --refresh-today`.

## 3. Run the dashboard

```bash
streamlit run app.py
```

Then open http://localhost:8501.

## Privacy

`data/` (your personal health data) and the auth token are **git-ignored** and
never committed. Credentials are only used locally to obtain the Garmin token.

## Keeping it fresh

Re-run `python fetch_data.py --refresh-today` (a quick incremental pull) before
opening the dashboard, or schedule it to run daily.
