#!/usr/bin/env python3
"""Compute a daily training/recovery briefing from the cached Garmin data.

Turns the raw dashboard tables (in ``dashboard/data``) into a compact set of
objective signals — recovery (RHR vs baseline, Body Battery, sleep, stress),
training load (Fitness/Fatigue/Form + acute:chronic ratio), running volume, and
an estimated calorie budget — that the coach model reasons over.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = HERE.parent / "dashboard" / "data"
RUN_TYPES = ["running", "treadmill_running", "track_running", "trail_running"]
# Activity types we treat as a "strength / lifting" session if logged to Garmin.
LIFT_TYPES = ["strength_training", "indoor_cardio", "hiit", "other", "fitness_equipment"]
MI_PER_KM = 0.621371
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _round(x, n=1):
    return None if x is None or pd.isna(x) else round(float(x), n)


def build_briefing(profile: dict, schedule: dict, nutrition: dict,
                   data_dir: Path = DEFAULT_DATA, today: date | None = None) -> dict:
    today = today or date.today()
    imperial = profile.get("units", "miles") == "miles"

    daily = pd.read_csv(data_dir / "daily.csv")
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date")

    acts = pd.read_csv(data_dir / "activities.csv")
    acts["date"] = pd.to_datetime(acts["date"], errors="coerce")
    acts = acts.dropna(subset=["date"]).sort_values("date")
    runs = acts[acts["type"].isin(RUN_TYPES)].copy()

    def jload(name, default=None):
        p = data_dir / name
        return json.loads(p.read_text()) if p.exists() else default

    race = jload("race_predictions.json", {}) or {}
    prs = jload("personal_records.json", []) or []

    def dist(km):
        return km * MI_PER_KM if imperial else km

    unit = "mi" if imperial else "km"

    # ---- recovery signals ------------------------------------------------ #
    rhr = daily.dropna(subset=["resting_hr"])
    rhr_latest = float(rhr["resting_hr"].iloc[-1]) if len(rhr) else None
    rhr_7 = float(rhr["resting_hr"].tail(7).mean()) if len(rhr) else None
    rhr_30 = float(rhr["resting_hr"].tail(30).mean()) if len(rhr) else None
    rhr_baseline = float(rhr["resting_hr"].tail(60).median()) if len(rhr) else None
    rhr_elevated = (rhr_latest is not None and rhr_baseline is not None
                    and rhr_latest - rhr_baseline >= 4)

    def last_valid(colname):
        if colname not in daily:
            return None
        s = daily[colname].dropna()
        return float(s.iloc[-1]) if len(s) else None

    # Today's row is often not synced yet — fall back to the latest day that has data.
    bb_low = last_valid("bb_low")
    bb_high = last_valid("bb_high")
    sleep_hours = last_valid("sleep_hours")
    sleep_score = last_valid("sleep_score")
    stress_recent = float(daily["avg_stress"].dropna().tail(3).mean()) if "avg_stress" in daily else None

    # ---- training load: Fitness / Fatigue / Form + ACWR ------------------ #
    load = runs.dropna(subset=["training_load"]).copy()
    ctl = atl = tsb = acwr = None
    daily_load_7 = daily_load_28 = None
    if not load.empty:
        dl = load.groupby(load["date"].dt.normalize())["training_load"].sum()
        idx = pd.date_range(dl.index.min(), pd.Timestamp(today))
        dl = dl.reindex(idx, fill_value=0.0)
        ctl_s = dl.ewm(alpha=1 / 42, adjust=False).mean()
        atl_s = dl.ewm(alpha=1 / 7, adjust=False).mean()
        ctl, atl = float(ctl_s.iloc[-1]), float(atl_s.iloc[-1])
        tsb = float(ctl_s.iloc[-2] - atl_s.iloc[-2]) if len(ctl_s) > 1 else ctl - atl
        acute = float(dl.tail(7).sum())
        chronic = float(dl.tail(28).sum()) / 4.0
        acwr = round(acute / chronic, 2) if chronic > 0 else None
        daily_load_7, daily_load_28 = acute, float(dl.tail(28).sum())

    # ---- running volume -------------------------------------------------- #
    def window_miles(days_back_start, days_back_end):
        lo = pd.Timestamp(today - timedelta(days=days_back_start))
        hi = pd.Timestamp(today - timedelta(days=days_back_end))
        sub = runs[(runs["date"] >= lo) & (runs["date"] < hi)]
        return float(dist(sub["distance_km"]).sum()), len(sub)

    miles_7, runs_7 = window_miles(7, 0)
    miles_prev7, _ = window_miles(14, 7)
    miles_28, _ = window_miles(28, 0)

    # this calendar week (Mon-today)
    week_start = pd.Timestamp(today - timedelta(days=today.weekday()))
    wk = runs[runs["date"] >= week_start]
    week_miles = float(dist(wk["distance_km"]).sum())
    week_runs = len(wk)

    last_run = runs.iloc[-1] if not runs.empty else None
    days_since_run = (today - last_run["date"].date()).days if last_run is not None else None
    # hard run = high aerobic training effect
    hard = runs.dropna(subset=["aerobic_te"])
    hard = hard[hard["aerobic_te"] >= 3.0]
    days_since_hard = ((today - hard.iloc[-1]["date"].date()).days
                       if not hard.empty else None)

    # lifts logged to Garmin in the last 7 days (often none — tracked off-device)
    lifts = acts[acts["type"].isin(LIFT_TYPES)]
    lifts_7 = int(len(lifts[lifts["date"] >= pd.Timestamp(today - timedelta(days=7))]))

    # ---- VO2 max + recent runs ------------------------------------------ #
    vo2 = runs.dropna(subset=["vo2max"])
    vo2_now = float(vo2["vo2max"].iloc[-1]) if not vo2.empty else None

    recent = []
    for _, r in runs.tail(5).iloc[::-1].iterrows():
        pace = r.get("pace_min_km")
        pace_disp = pace * (1 / MI_PER_KM) if (imperial and pd.notna(pace)) else pace
        recent.append({
            "date": r["date"].strftime("%a %b %d"),
            "type": r.get("type"),
            "dist": _round(dist(r.get("distance_km", 0)), 2),
            "pace_min_per_unit": _round(pace_disp, 2),
            "avg_hr": _round(r.get("avg_hr"), 0),
            "aerobic_te": _round(r.get("aerobic_te"), 1),
            "load": _round(r.get("training_load"), 0),
        })

    # ---- nutrition: TDEE from Garmin daily expenditure ------------------- #
    tdee = float(daily["total_calories"].tail(7).mean()) if "total_calories" in daily else None
    tdee = round(tdee) if tdee else nutrition.get("tdee_override")
    weight_lb = profile.get("weight_lb")
    protein_g = round(weight_lb * nutrition.get("protein_g_per_lb", 1.0)) if weight_lb else None

    # ---- planned modality (from weekly template) ------------------------- #
    dow = today.weekday()
    planned = []
    if dow in (schedule.get("run_days") or []):
        planned.append("run")
    if dow in (schedule.get("lift_days") or []):
        planned.append("lift")
    if dow == schedule.get("rest_day"):
        planned.append("rest")
    is_long_run_day = dow == schedule.get("long_run_day")

    # ---- race predictions (seconds) -------------------------------------- #
    def secs_to_str(s):
        if not s:
            return None
        s = int(s)
        h, m, sec = s // 3600, (s % 3600) // 60, s % 60
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    race_str = {
        "5K": secs_to_str(race.get("time5K")),
        "10K": secs_to_str(race.get("time10K")),
        "half": secs_to_str(race.get("timeHalfMarathon")),
        "marathon": secs_to_str(race.get("timeMarathon")),
    }

    return {
        "date": today.isoformat(),
        "weekday": WEEKDAYS[dow],
        "unit": unit,
        "recovery": {
            "resting_hr": _round(rhr_latest, 0),
            "rhr_7day_avg": _round(rhr_7, 0),
            "rhr_60day_baseline": _round(rhr_baseline, 0),
            "rhr_elevated_vs_baseline": rhr_elevated,
            "body_battery_overnight_low": _round(bb_low, 0),
            "body_battery_high": _round(bb_high, 0),
            "sleep_hours": _round(sleep_hours, 1),
            "sleep_score": _round(sleep_score, 0),
            "stress_avg_recent": _round(stress_recent, 0),
        },
        "training_load": {
            "fitness_ctl": _round(ctl, 0),
            "fatigue_atl": _round(atl, 0),
            "form_tsb": _round(tsb, 0),
            "acute_chronic_ratio": acwr,
            "load_last_7d": _round(daily_load_7, 0),
            "load_last_28d": _round(daily_load_28, 0),
        },
        "running": {
            f"miles_last_7d_{unit}": _round(miles_7, 1),
            f"miles_prev_7d_{unit}": _round(miles_prev7, 1),
            f"avg_weekly_{unit}_4wk": _round(miles_28 / 4, 1),
            "runs_last_7d": runs_7,
            f"this_week_{unit}": _round(week_miles, 1),
            "this_week_runs": week_runs,
            "days_since_last_run": days_since_run,
            "days_since_hard_workout": days_since_hard,
            "vo2max": _round(vo2_now, 0),
            "recent_runs": recent,
        },
        "strength": {
            "lifts_logged_last_7d": lifts_7,
            "note": "Lifting is usually tracked off-device; rely on the weekly plan.",
        },
        "today_plan": {
            "planned_modality": planned or ["flexible"],
            "is_long_run_day": is_long_run_day,
            "runs_target_per_week": schedule.get("runs_per_week"),
            "lifts_target_per_week": schedule.get("lifts_per_week"),
        },
        "nutrition": {
            "tdee_kcal": tdee,
            "phase": nutrition.get("phase", "recomp"),
            "protein_target_g": protein_g,
            "weight_lb": weight_lb,
        },
        "race_predictions": race_str,
        "personal_records": prs[:6],
    }


def summary_text(b: dict) -> str:
    """Human-readable one-screen summary for logs / dry-runs."""
    r, t, run, n = b["recovery"], b["training_load"], b["running"], b["nutrition"]
    u = b["unit"]
    lines = [
        f"{b['weekday']} {b['date']} — planned: {', '.join(b['today_plan']['planned_modality'])}",
        f"Recovery: RHR {r['resting_hr']} (base {r['rhr_60day_baseline']}, "
        f"{'ELEVATED' if r['rhr_elevated_vs_baseline'] else 'ok'}) · "
        f"BodyBattery low {r['body_battery_overnight_low']} · "
        f"sleep {r['sleep_hours']}h · stress {r['stress_avg_recent']}",
        f"Load: Fitness {t['fitness_ctl']} / Fatigue {t['fatigue_atl']} / "
        f"Form {t['form_tsb']} · ACWR {t['acute_chronic_ratio']}",
        f"Running: {run[f'this_week_{u}']} {u} this wk ({run['this_week_runs']} runs) · "
        f"7d {run[f'miles_last_7d_{u}']} {u} · VO2 {run['vo2max']} · "
        f"{run['days_since_last_run']}d since run",
        f"Nutrition: TDEE ~{n['tdee_kcal']} kcal · phase {n['phase']} · "
        f"protein {n['protein_target_g']}g",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load((HERE / "config.example.yaml").read_text())
    b = build_briefing(cfg["profile"], cfg["schedule"], cfg["nutrition"])
    print(summary_text(b))
    print("\n--- full briefing JSON ---")
    print(json.dumps(b, indent=2))
