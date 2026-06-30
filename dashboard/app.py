#!/usr/bin/env python3
"""Garmin personal dashboard — minimal, insight-dense.

Reads the cached tables produced by ``fetch_data.py`` (in ``data/``) and renders
a clean Streamlit dashboard covering training, running, vitals and activity.

Run:
    streamlit run app.py
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #
INK = "#0F172A"
MUTED = "#64748B"
FAINT = "#94A3B8"
GRID = "#EEF0F3"
LINE = "#E5E7EB"
ACCENT = "#FF5A36"   # running / energy
HR = "#EF4444"       # heart rate
STRESS = "#F59E0B"   # stress
BATTERY = "#10B981"  # body battery
STEPS = "#3B82F6"    # steps
SLEEP = "#6366F1"    # sleep
VO2 = "#8B5CF6"      # fitness
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"

st.set_page_config(page_title="Garmin Dashboard", page_icon="🏃", layout="wide")

# --------------------------------------------------------------------------- #
# Minimal premium styling
# --------------------------------------------------------------------------- #
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
      html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
      #MainMenu, footer, header {visibility: hidden;}
      .block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1280px;}
      h1, h2, h3, h4 { letter-spacing: -0.02em; color: #0F172A; }
      .stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid #EEF0F3; }
      .stTabs [data-baseweb="tab"] {
          height: 40px; padding: 0 16px; background: transparent;
          font-weight: 600; color: #64748B; border-radius: 8px 8px 0 0;
      }
      .stTabs [aria-selected="true"] { color: #0F172A; background: #F7F7F8; }
      div[data-testid="stMetric"] {
          background: #FFFFFF; border: 1px solid #EEF0F3; border-radius: 14px;
          padding: 16px 18px; box-shadow: 0 1px 2px rgba(15,23,42,0.03);
      }
      div[data-testid="stMetricLabel"] p {
          font-size: 0.74rem; font-weight: 600; letter-spacing: 0.04em;
          text-transform: uppercase; color: #94A3B8;
      }
      div[data-testid="stMetricValue"] { font-size: 1.7rem; font-weight: 700; }
      .kpi-sub { color:#94A3B8; font-size:0.8rem; margin-top:-6px; }
      .section-title { font-size:1.05rem; font-weight:700; margin: 6px 0 2px 0; }
      .section-cap { color:#94A3B8; font-size:0.82rem; margin-bottom:10px; }
      hr { margin: 0.6rem 0 1.1rem 0; border-color:#EEF0F3; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
KM_PER_MI = 1.609344
MI_PER_KM = 0.621371


def fmt_secs(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return "—"
    s = int(round(s))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_pace(p) -> str:
    if p is None or pd.isna(p) or p <= 0:
        return "—"
    m = int(p)
    sec = int(round((p - m) * 60))
    if sec == 60:
        m, sec = m + 1, 0
    return f"{m}:{sec:02d}"


def styled(fig: go.Figure, height: int = 300, ytitle: str | None = None,
           legend: bool = False) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=12, t=10, b=8),
        font=dict(family=FONT, size=13, color=INK),
        paper_bgcolor="white", plot_bgcolor="white",
        hovermode="x unified",
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=12, color=MUTED)),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=LINE,
                     ticks="outside", tickcolor=LINE, tickfont=dict(color=MUTED))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False,
                     title=ytitle, title_font=dict(size=12, color=MUTED),
                     tickfont=dict(color=MUTED))
    return fig


def section(title: str, caption: str = "") -> None:
    st.markdown(f"<div class='section-title'>{title}</div>", unsafe_allow_html=True)
    if caption:
        st.markdown(f"<div class='section-cap'>{caption}</div>", unsafe_allow_html=True)


def trend(curr, prev, lower_is_better=False, fmt="{:+.0f}", unit=""):
    """Return a (delta_str, color) tuple for st.metric, or None."""
    if curr is None or prev is None or pd.isna(curr) or pd.isna(prev):
        return None
    d = curr - prev
    if d == 0:
        return f"0{unit}"
    s = fmt.format(d) + unit
    return s


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def _sig() -> float:
    """Cache signature based on data file mtimes so refresh invalidates cache."""
    files = list(DATA.glob("*.csv")) + list(DATA.glob("*.json"))
    return max((f.stat().st_mtime for f in files), default=0.0)


@st.cache_data(show_spinner=False)
def load_all(sig: float) -> dict:
    def jload(name, default=None):
        p = DATA / name
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return default
        return default

    def cload(name):
        p = DATA / name
        return pd.read_csv(p) if p.exists() else pd.DataFrame()

    daily = cload("daily.csv")
    if not daily.empty:
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.sort_values("date")

    acts = cload("activities.csv")
    if not acts.empty:
        acts["date"] = pd.to_datetime(acts["date"], errors="coerce")
        acts["start_local"] = pd.to_datetime(acts["start_local"], errors="coerce")
        acts = acts.sort_values("start_local")
        # imperial conversions
        acts["distance_mi"] = acts["distance_km"] * MI_PER_KM
        acts["pace_min_mi"] = acts["pace_min_km"] * KM_PER_MI

    steps_hist = cload("steps_history.csv")
    if not steps_hist.empty and "calendarDate" in steps_hist:
        steps_hist["date"] = pd.to_datetime(steps_hist["calendarDate"])

    return {
        "daily": daily,
        "acts": acts,
        "steps_hist": steps_hist,
        "profile": jload("profile.json", {}),
        "race": jload("race_predictions.json", {}),
        "prs": jload("personal_records.json", []),
        "training_status": jload("training_status.json", {}),
        "max_metrics": jload("max_metrics.json", []),
        "body": jload("body_composition.json", {}),
        "weekly_stress": jload("weekly_stress.json", []),
    }


if not (DATA / "daily.csv").exists():
    st.error("No data found. Run `python fetch_data.py` first.")
    st.stop()

D = load_all(_sig())
daily: pd.DataFrame = D["daily"]
acts: pd.DataFrame = D["acts"]
profile = D["profile"]

# --------------------------------------------------------------------------- #
# Sidebar controls
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### 🏃 Garmin")
    st.caption(profile.get("full_name", "Athlete"))
    units = st.radio("Units", ["Miles", "Kilometers"],
                     index=0 if profile.get("unit_system") == "statute_us" else 1,
                     horizontal=True)
    win = st.select_slider("Window", options=[30, 60, 90, 180, 365], value=90,
                           format_func=lambda x: f"{x} days")
    st.divider()
    fetched = profile.get("fetched_at", "")
    if fetched:
        st.caption(f"Data synced: {fetched.replace('T', ' ')}")
    st.caption("Refresh data:")
    st.code("python fetch_data.py --refresh-today", language="bash")

IMP = units == "Miles"
DUNIT = "mi" if IMP else "km"
PUNIT = "/mi" if IMP else "/km"


def dist(df_col_km):
    return df_col_km * MI_PER_KM if IMP else df_col_km


RUN_TYPES = ["running", "treadmill_running", "track_running", "trail_running"]
cutoff = pd.Timestamp(date.today() - timedelta(days=win))
dd = daily[daily["date"] >= cutoff].copy() if not daily.empty else daily
aa = acts[acts["date"] >= cutoff].copy() if not acts.empty else acts
runs_all = acts[acts["type"].isin(RUN_TYPES)].copy() if not acts.empty else acts
runs = aa[aa["type"].isin(RUN_TYPES)].copy() if not aa.empty else aa


def col(df, name):
    return df[name] if (not df.empty and name in df) else pd.Series(dtype="float64")


# --------------------------------------------------------------------------- #
# Header + KPI row
# --------------------------------------------------------------------------- #
st.markdown(f"## {profile.get('full_name', 'Your')} · Fitness Dashboard")
st.markdown(
    f"<div class='kpi-sub'>Last {win} days · "
    f"{len(runs)} runs · {len(aa)} activities</div>",
    unsafe_allow_html=True,
)
st.write("")

# derive headline numbers
vo2_series = runs_all.dropna(subset=["vo2max"]).groupby(
    runs_all["date"].dt.date)["vo2max"].max() if not runs_all.empty else pd.Series(dtype=float)
vo2_now = vo2_series.iloc[-1] if len(vo2_series) else None
vo2_prev = vo2_series.iloc[-30] if len(vo2_series) > 30 else (
    vo2_series.iloc[0] if len(vo2_series) else None)

rhr_now = col(dd, "resting_hr").dropna().iloc[-1] if col(dd, "resting_hr").dropna().size else None
rhr_prev = col(dd, "resting_hr").dropna().iloc[0] if col(dd, "resting_hr").dropna().size else None

# weekly mileage (last 7 days vs previous 7)
def window_miles(df, days_back_start, days_back_end):
    if df.empty:
        return 0.0
    lo = pd.Timestamp(date.today() - timedelta(days=days_back_start))
    hi = pd.Timestamp(date.today() - timedelta(days=days_back_end))
    sub = df[(df["date"] >= lo) & (df["date"] < hi)]
    return float(dist(sub["distance_km"]).sum())


wk_now = window_miles(runs_all, 7, 0)
wk_prev = window_miles(runs_all, 14, 7)

avg_rhr = col(dd, "resting_hr").mean()
avg_stress = col(dd, "avg_stress").mean()
avg_steps = col(dd, "steps").mean()
avg_bb_low = col(dd, "bb_low").mean()

k = st.columns(6)
with k[0]:
    st.metric("VO₂ Max", f"{vo2_now:.0f}" if vo2_now else "—",
              trend(vo2_now, vo2_prev, fmt="{:+.0f}"))
with k[1]:
    st.metric("Resting HR", f"{rhr_now:.0f} bpm" if rhr_now else "—",
              trend(rhr_now, rhr_prev, fmt="{:+.0f}", unit=" bpm"),
              delta_color="inverse")
with k[2]:
    st.metric(f"This week ({DUNIT})", f"{wk_now:.1f}",
              trend(wk_now, wk_prev, fmt="{:+.1f}"))
with k[3]:
    st.metric("Avg steps", f"{avg_steps:,.0f}" if not pd.isna(avg_steps) else "—")
with k[4]:
    st.metric("Avg stress", f"{avg_stress:.0f}" if not pd.isna(avg_stress) else "—",
              delta_color="inverse")
with k[5]:
    st.metric("Body Battery low", f"{avg_bb_low:.0f}" if not pd.isna(avg_bb_low) else "—")

st.write("")

# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #
has_sleep = not daily.empty and col(daily, "sleep_hours").dropna().size > 0
tab_names = ["Overview", "Running & Training", "Vitals", "Steps & Activity"]
if has_sleep:
    tab_names.append("Sleep")
tabs = st.tabs(tab_names)


# ---- helpers for charts --------------------------------------------------- #
def weekly_sum(df, value_km=True, col_name="distance_km"):
    if df.empty:
        return pd.DataFrame(columns=["week", "val"])
    s = df.set_index("date")[col_name].resample("W-MON").sum()
    out = s.reset_index()
    out.columns = ["week", "val"]
    if value_km:
        out["val"] = dist(out["val"])
    return out


def calendar_heatmap(dates, values, title, color, height=180):
    """GitHub-style calendar heatmap."""
    df = pd.DataFrame({"date": pd.to_datetime(dates), "val": values}).dropna()
    if df.empty:
        return None
    end = pd.Timestamp(date.today())
    start = end - pd.Timedelta(days=363)
    full = pd.DataFrame({"date": pd.date_range(start, end)})
    df = full.merge(df, on="date", how="left")
    df["dow"] = df["date"].dt.weekday               # 0 Mon
    df["week"] = ((df["date"] - start).dt.days // 7)
    z = [[None] * (df["week"].max() + 1) for _ in range(7)]
    text = [["" for _ in range(df["week"].max() + 1)] for _ in range(7)]
    for _, r in df.iterrows():
        z[int(r["dow"])][int(r["week"])] = r["val"]
        if pd.notna(r["val"]):
            text[int(r["dow"])][int(r["week"])] = f"{r['date']:%b %d}: {r['val']:.1f}"
    fig = go.Figure(go.Heatmap(
        z=z, text=text, hoverinfo="text", xgap=3, ygap=3,
        colorscale=[[0, "#F1F5F9"], [1, color]], showscale=False,
    ))
    fig.update_yaxes(autorange="reversed", showticklabels=True,
                     tickvals=[0, 2, 4, 6], ticktext=["Mon", "Wed", "Fri", "Sun"])
    fig.update_xaxes(showticklabels=False)
    return styled(fig, height=height)


# =========================================================================== #
# OVERVIEW
# =========================================================================== #
with tabs[0]:
    c1, c2 = st.columns(2)
    with c1:
        section("Weekly running volume", f"Distance per week ({DUNIT})")
        w = weekly_sum(runs_all)
        w = w[w["week"] >= cutoff]
        fig = go.Figure(go.Bar(x=w["week"], y=w["val"], marker_color=ACCENT,
                               marker_line_width=0,
                               hovertemplate="%{x|%b %d}<br>%{y:.1f} " + DUNIT + "<extra></extra>"))
        st.plotly_chart(styled(fig, height=280, ytitle=DUNIT), width="stretch")
    with c2:
        section("VO₂ Max trend", "Estimated from each run")
        if len(vo2_series):
            vs = vo2_series[vo2_series.index >= cutoff.date()]
            fig = go.Figure(go.Scatter(
                x=list(vs.index), y=list(vs.values), mode="lines+markers",
                line=dict(color=VO2, width=2.5), marker=dict(size=5, color=VO2),
                hovertemplate="%{x|%b %d}<br>VO₂ %{y:.1f}<extra></extra>"))
            st.plotly_chart(styled(fig, height=280, ytitle="ml/kg/min"),
                            width="stretch")
        else:
            st.info("No VO₂ data yet.")

    c3, c4 = st.columns(2)
    with c3:
        section("Resting heart rate", "Lower trending = better recovery")
        r = dd.dropna(subset=["resting_hr"])
        if not r.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=r["date"], y=r["resting_hr"], mode="lines",
                                     line=dict(color=HR, width=2),
                                     name="Resting HR",
                                     hovertemplate="%{x|%b %d}<br>%{y:.0f} bpm<extra></extra>"))
            if "rhr_7day_avg" in r:
                fig.add_trace(go.Scatter(x=r["date"], y=r["rhr_7day_avg"], mode="lines",
                                         line=dict(color=FAINT, width=1.5, dash="dot"),
                                         name="7-day avg"))
            st.plotly_chart(styled(fig, height=280, ytitle="bpm", legend=True),
                            width="stretch")
        else:
            st.info("No heart-rate data.")
    with c4:
        section("Daily steps", "vs goal")
        s = dd.dropna(subset=["steps"])
        if not s.empty:
            fig = go.Figure()
            fig.add_trace(go.Bar(x=s["date"], y=s["steps"], marker_color=STEPS,
                                 marker_line_width=0, name="Steps",
                                 hovertemplate="%{x|%b %d}<br>%{y:,.0f}<extra></extra>"))
            fig.add_trace(go.Scatter(x=s["date"],
                                     y=s["steps"].rolling(7, min_periods=1).mean(),
                                     mode="lines", line=dict(color=INK, width=2),
                                     name="7-day avg"))
            if "step_goal" in s:
                fig.add_trace(go.Scatter(x=s["date"], y=s["step_goal"], mode="lines",
                                         line=dict(color=FAINT, width=1, dash="dot"),
                                         name="Goal"))
            st.plotly_chart(styled(fig, height=280, ytitle="steps", legend=True),
                            width="stretch")
        else:
            st.info("No step data.")

    section("Running calendar", f"Distance per day ({DUNIT}) · last 12 months")
    if not runs_all.empty:
        by_day = runs_all.groupby(runs_all["date"].dt.date)["distance_km"].sum()
        ch = calendar_heatmap(list(by_day.index), list(dist(by_day).values),
                              "runs", ACCENT)
        if ch:
            st.plotly_chart(ch, width="stretch")


# =========================================================================== #
# RUNNING & TRAINING
# =========================================================================== #
with tabs[1]:
    # Race predictions
    race = D["race"] or {}
    section("Race predictions", "Garmin model estimates")
    rc = st.columns(4)
    for i, (label, key) in enumerate(
        [("5K", "time5K"), ("10K", "time10K"),
         ("Half", "timeHalfMarathon"), ("Marathon", "timeMarathon")]):
        with rc[i]:
            st.metric(label, fmt_secs(race.get(key)))

    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        section("Pace trend", f"Running pace ({PUNIT}) · higher = faster")
        pcol = "pace_min_mi" if IMP else "pace_min_km"
        rp = runs_all.dropna(subset=[pcol])
        rp = rp[rp["date"] >= cutoff]
        if not rp.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=rp["start_local"], y=rp[pcol], mode="markers",
                marker=dict(size=7, color=ACCENT, opacity=0.65),
                name="Run",
                customdata=dist(rp["distance_km"]),
                hovertemplate="%{x|%b %d}<br>%{y:.2f} " + PUNIT
                              + "<br>%{customdata:.1f} " + DUNIT + "<extra></extra>"))
            fig.add_trace(go.Scatter(
                x=rp["start_local"], y=rp[pcol].rolling(5, min_periods=1).mean(),
                mode="lines", line=dict(color=INK, width=2), name="5-run avg"))
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(styled(fig, height=300, ytitle=PUNIT, legend=True),
                            width="stretch")
        else:
            st.info("No running pace data.")
    with c2:
        section("Heart rate vs pace", "Aerobic efficiency · bubble = distance")
        pcol = "pace_min_mi" if IMP else "pace_min_km"
        re = runs_all.dropna(subset=[pcol, "avg_hr"])
        re = re[re["date"] >= cutoff]
        if not re.empty:
            fig = go.Figure(go.Scatter(
                x=re[pcol], y=re["avg_hr"], mode="markers",
                marker=dict(size=(dist(re["distance_km"]) * 2.2 + 5),
                            color=re["date"].astype("int64"),
                            colorscale=[[0, "#FBD5C9"], [1, ACCENT]],
                            opacity=0.8, line=dict(width=0)),
                customdata=dist(re["distance_km"]),
                hovertemplate="%{x:.2f} " + PUNIT + "<br>%{y:.0f} bpm"
                              "<br>%{customdata:.1f} " + DUNIT + "<extra></extra>"))
            fig.update_xaxes(autorange="reversed")
            st.plotly_chart(styled(fig, height=300, ytitle="avg bpm"),
                            width="stretch")
            st.caption("Left & lower = faster at a lower heart rate (fitter).")
        else:
            st.info("No HR/pace data.")

    # Training load — Fitness / Fatigue / Form (PMC)
    section("Training load · Fitness, Fatigue & Form",
            "Exponentially-weighted load (CTL/ATL). Form = Fitness − Fatigue.")
    if not runs_all.empty and runs_all["training_load"].notna().any():
        load = runs_all.dropna(subset=["training_load"]).copy()
        daily_load = load.groupby(load["date"].dt.normalize())["training_load"].sum()
        idx = pd.date_range(daily_load.index.min(), pd.Timestamp(date.today()))
        dl = daily_load.reindex(idx, fill_value=0.0)
        ctl = dl.ewm(alpha=1 / 42, adjust=False).mean()
        atl = dl.ewm(alpha=1 / 7, adjust=False).mean()
        form = ctl - atl
        view = idx >= cutoff
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=idx[view], y=form[view], mode="lines", name="Form",
                                 line=dict(color="#CBD5E1", width=0),
                                 fill="tozeroy", fillcolor="rgba(148,163,184,0.18)"))
        fig.add_trace(go.Scatter(x=idx[view], y=ctl[view], mode="lines", name="Fitness (CTL)",
                                 line=dict(color=VO2, width=2.5)))
        fig.add_trace(go.Scatter(x=idx[view], y=atl[view], mode="lines", name="Fatigue (ATL)",
                                 line=dict(color=ACCENT, width=2)))
        st.plotly_chart(styled(fig, height=300, ytitle="load", legend=True),
                        width="stretch")
        fcur = float(form.iloc[-1])
        msg = ("🟢 Fresh — good time to race or push" if fcur > 5 else
               "🟡 Balanced / building" if fcur > -10 else
               "🔴 Fatigued — prioritise recovery")
        st.caption(f"Current form: {fcur:+.0f} · {msg}")
    else:
        st.info("No training-load data.")

    c3, c4 = st.columns(2)
    with c3:
        section("Distance per run", DUNIT)
        rr = runs_all[runs_all["date"] >= cutoff].dropna(subset=["distance_km"])
        if not rr.empty:
            fig = go.Figure(go.Bar(
                x=rr["start_local"], y=dist(rr["distance_km"]),
                marker_color=ACCENT, marker_line_width=0,
                hovertemplate="%{x|%b %d}<br>%{y:.2f} " + DUNIT + "<extra></extra>"))
            st.plotly_chart(styled(fig, height=270, ytitle=DUNIT), width="stretch")
    with c4:
        section("Time in HR zones", "Aggregate minutes · last window")
        zcols = [f"hr_zone_{i}" for i in range(1, 6)]
        if not runs.empty and all(c in runs for c in zcols):
            mins = [runs[c].sum() / 60 for c in zcols]
            zcolors = ["#BFDBFE", "#86EFAC", "#FDE047", "#FB923C", "#EF4444"]
            fig = go.Figure(go.Bar(
                x=[f"Z{i}" for i in range(1, 6)], y=mins,
                marker_color=zcolors, marker_line_width=0,
                hovertemplate="%{x}<br>%{y:.0f} min<extra></extra>"))
            st.plotly_chart(styled(fig, height=270, ytitle="minutes"),
                            width="stretch")

    # Personal records + recent runs
    section("Personal records", "")
    prs = D["prs"] or []
    if prs:
        rows = []
        for p in prs:
            v = p.get("value")
            kind = p.get("kind")
            if kind == "time":
                val = fmt_secs(v)
            elif kind == "distance":
                val = f"{(v or 0) / 1000 * (MI_PER_KM if IMP else 1):.2f} {DUNIT}" if IMP \
                      else f"{(v or 0) / 1000:.2f} km"
            elif kind == "count":
                val = f"{v:,.0f}"
            else:
                val = f"{v:,.0f}"
            rows.append({"Record": p.get("label"), "Value": val,
                         "Date": p.get("date")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    section("Recent runs", "")
    if not runs_all.empty:
        pcol = "pace_min_mi" if IMP else "pace_min_km"
        show = runs_all.sort_values("start_local", ascending=False).head(12).copy()
        tbl = pd.DataFrame({
            "Date": show["start_local"].dt.strftime("%b %d"),
            "Name": show["name"],
            f"Dist ({DUNIT})": dist(show["distance_km"]).round(2),
            "Time": (show["duration_min"] * 60).apply(fmt_secs),
            f"Pace ({PUNIT})": show[pcol].apply(fmt_pace),
            "Avg HR": show["avg_hr"].round(0),
            "Load": show["training_load"].round(0),
        })
        st.dataframe(tbl, hide_index=True, width="stretch")


# =========================================================================== #
# VITALS
# =========================================================================== #
with tabs[2]:
    c1, c2 = st.columns(2)
    with c1:
        section("Stress", "Daily average · 0–100")
        s = dd.dropna(subset=["avg_stress"])
        if not s.empty:
            fig = go.Figure()
            fig.add_hrect(y0=0, y1=25, fillcolor="#D1FAE5", opacity=0.5, line_width=0)
            fig.add_hrect(y0=25, y1=50, fillcolor="#FEF3C7", opacity=0.4, line_width=0)
            fig.add_hrect(y0=50, y1=100, fillcolor="#FEE2E2", opacity=0.35, line_width=0)
            fig.add_trace(go.Scatter(x=s["date"], y=s["avg_stress"], mode="lines",
                                     line=dict(color=STRESS, width=2),
                                     hovertemplate="%{x|%b %d}<br>%{y:.0f}<extra></extra>"))
            st.plotly_chart(styled(fig, height=280, ytitle="stress"),
                            width="stretch")
    with c2:
        section("Body Battery", "Daily high → low energy range")
        b = dd.dropna(subset=["bb_high", "bb_low"])
        if not b.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=b["date"], y=b["bb_high"], mode="lines",
                                     line=dict(color=BATTERY, width=0),
                                     name="High", showlegend=False))
            fig.add_trace(go.Scatter(x=b["date"], y=b["bb_low"], mode="lines",
                                     line=dict(color=BATTERY, width=0), fill="tonexty",
                                     fillcolor="rgba(16,185,129,0.18)",
                                     name="Low", showlegend=False))
            fig.add_trace(go.Scatter(x=b["date"], y=b["bb_high"], mode="lines",
                                     line=dict(color=BATTERY, width=2), name="High",
                                     hovertemplate="High %{y:.0f}<extra></extra>"))
            fig.add_trace(go.Scatter(x=b["date"], y=b["bb_low"], mode="lines",
                                     line=dict(color="#34D399", width=1.5, dash="dot"),
                                     name="Low",
                                     hovertemplate="Low %{y:.0f}<extra></extra>"))
            st.plotly_chart(styled(fig, height=280, ytitle="0–100"),
                            width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        section("Heart rate range", "Daily min / resting / max")
        h = dd.dropna(subset=["max_hr"])
        if not h.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=h["date"], y=h["max_hr"], mode="lines",
                                     line=dict(color="#FCA5A5", width=1.5), name="Max"))
            fig.add_trace(go.Scatter(x=h["date"], y=h["resting_hr"], mode="lines",
                                     line=dict(color=HR, width=2), name="Resting"))
            fig.add_trace(go.Scatter(x=h["date"], y=h["min_hr"], mode="lines",
                                     line=dict(color="#FECACA", width=1.5), name="Min"))
            st.plotly_chart(styled(fig, height=280, ytitle="bpm", legend=True),
                            width="stretch")
    with c4:
        section("Respiration", "Average waking breaths/min")
        rr = dd.dropna(subset=["avg_waking_respiration"])
        if not rr.empty:
            fig = go.Figure(go.Scatter(
                x=rr["date"], y=rr["avg_waking_respiration"], mode="lines",
                line=dict(color=SLEEP, width=2),
                hovertemplate="%{x|%b %d}<br>%{y:.0f} br/min<extra></extra>"))
            st.plotly_chart(styled(fig, height=280, ytitle="br/min"),
                            width="stretch")
        else:
            st.info("No respiration data.")

    section("Weekly intensity minutes", "Moderate + 2×vigorous vs 150 goal")
    if not daily.empty and col(daily, "vigorous_intensity_min").notna().any():
        im = daily.dropna(subset=["vigorous_intensity_min"]).copy()
        im["im"] = im["moderate_intensity_min"].fillna(0) + 2 * im["vigorous_intensity_min"].fillna(0)
        wk = im.set_index("date")["im"].resample("W-MON").sum().reset_index()
        wk = wk[wk["date"] >= cutoff]
        colors = [BATTERY if v >= 150 else STRESS for v in wk["im"]]
        fig = go.Figure(go.Bar(x=wk["date"], y=wk["im"], marker_color=colors,
                               marker_line_width=0,
                               hovertemplate="%{x|%b %d}<br>%{y:.0f} min<extra></extra>"))
        fig.add_hline(y=150, line=dict(color=INK, width=1, dash="dot"))
        st.plotly_chart(styled(fig, height=250, ytitle="minutes"),
                        width="stretch")


# =========================================================================== #
# STEPS & ACTIVITY
# =========================================================================== #
with tabs[3]:
    c1, c2 = st.columns(2)
    with c1:
        section("Steps", "Daily with 7-day average")
        s = dd.dropna(subset=["steps"])
        if not s.empty:
            fig = go.Figure()
            fig.add_trace(go.Bar(x=s["date"], y=s["steps"], marker_color=STEPS,
                                 marker_line_width=0, name="Steps",
                                 hovertemplate="%{x|%b %d}<br>%{y:,.0f}<extra></extra>"))
            fig.add_trace(go.Scatter(x=s["date"],
                                     y=s["steps"].rolling(7, min_periods=1).mean(),
                                     mode="lines", line=dict(color=INK, width=2),
                                     name="7-day avg"))
            st.plotly_chart(styled(fig, height=280, ytitle="steps", legend=True),
                            width="stretch")
    with c2:
        section("Calories", "Total vs active")
        s = dd.dropna(subset=["total_calories"])
        if not s.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=s["date"], y=s["total_calories"], mode="lines",
                                     line=dict(color=FAINT, width=1.5), name="Total"))
            fig.add_trace(go.Scatter(x=s["date"], y=s["active_calories"], mode="lines",
                                     line=dict(color=ACCENT, width=2), fill="tozeroy",
                                     fillcolor="rgba(255,90,54,0.10)", name="Active"))
            st.plotly_chart(styled(fig, height=280, ytitle="kcal", legend=True),
                            width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        section("Floors climbed", "Daily")
        s = dd.dropna(subset=["floors_ascended"])
        if not s.empty:
            fig = go.Figure(go.Bar(x=s["date"], y=s["floors_ascended"],
                                   marker_color="#0EA5E9", marker_line_width=0,
                                   hovertemplate="%{x|%b %d}<br>%{y:.0f} floors<extra></extra>"))
            st.plotly_chart(styled(fig, height=260, ytitle="floors"),
                            width="stretch")
    with c4:
        section("Active vs sedentary", "Daily hours")
        s = dd.dropna(subset=["sedentary_sec"])
        if not s.empty:
            active_h = (s["highly_active_sec"].fillna(0) + s["active_sec"].fillna(0)) / 3600
            sed_h = s["sedentary_sec"].fillna(0) / 3600
            fig = go.Figure()
            fig.add_trace(go.Bar(x=s["date"], y=active_h, name="Active",
                                 marker_color=BATTERY, marker_line_width=0))
            fig.add_trace(go.Bar(x=s["date"], y=sed_h, name="Sedentary",
                                 marker_color="#E2E8F0", marker_line_width=0))
            fig.update_layout(barmode="stack")
            st.plotly_chart(styled(fig, height=260, ytitle="hours", legend=True),
                            width="stretch")

    section("Steps calendar", "Daily steps · last 12 months")
    if not daily.empty:
        sd = daily.dropna(subset=["steps"])
        ch = calendar_heatmap(sd["date"], sd["steps"] / 1000, "steps", STEPS)
        if ch:
            st.plotly_chart(ch, width="stretch")
            st.caption("Shade = thousands of steps.")


# =========================================================================== #
# SLEEP (conditional)
# =========================================================================== #
if has_sleep:
    with tabs[4]:
        section("Sleep duration & stages", "Hours per night")
        s = daily.dropna(subset=["sleep_hours"])
        s = s[s["date"] >= cutoff]
        fig = go.Figure()
        for label, c_, color in [("deep_sleep_hours", "Deep", "#3730A3"),
                                 ("light_sleep_hours", "Light", "#818CF8"),
                                 ("rem_sleep_hours", "REM", "#C4B5FD"),
                                 ("awake_hours", "Awake", "#FCA5A5")]:
            if label in s:
                fig.add_trace(go.Bar(x=s["date"], y=s[label], name=c_,
                                     marker_color=color, marker_line_width=0))
        fig.update_layout(barmode="stack")
        st.plotly_chart(styled(fig, height=320, ytitle="hours", legend=True),
                        width="stretch")
        if col(s, "sleep_score").notna().any():
            section("Sleep score", "")
            ss = s.dropna(subset=["sleep_score"])
            fig = go.Figure(go.Scatter(x=ss["date"], y=ss["sleep_score"], mode="lines+markers",
                                       line=dict(color=SLEEP, width=2)))
            st.plotly_chart(styled(fig, height=240, ytitle="score"),
                            width="stretch")
