#!/usr/bin/env python3
"""Fetch Garmin Connect data and cache it locally for the dashboard.

Authentication reuses the token saved in ``~/.garminconnect`` (created by
``example.py`` or the first interactive login). Raw per-day responses are
cached under ``data/raw/`` so re-runs are incremental — only missing days are
fetched. Processed, dashboard-ready tables are written to ``data/`` as CSV/JSON.

Usage:
    python fetch_data.py --days 120 --history-days 365

Environment:
    GARMINTOKENS   path to token store (default ~/.garminconnect)
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
RAW_DIR = DATA_DIR / "raw"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def safe_call(fn: Callable[..., Any], *args: Any, retries: int = 3) -> Any:
    """Call an API method, retrying on rate limits, returning None on failure."""
    for attempt in range(retries):
        try:
            return fn(*args)
        except GarminConnectTooManyRequestsError:
            wait = 5 * (attempt + 1)
            log(f"  rate limited, sleeping {wait}s ...")
            time.sleep(wait)
        except (GarminConnectConnectionError, Exception) as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg:
                wait = 5 * (attempt + 1)
                log(f"  429, sleeping {wait}s ...")
                time.sleep(wait)
                continue
            return None
    return None


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, default=str))


# --------------------------------------------------------------------------- #
# Per-day cache (user_summary + sleep)
# --------------------------------------------------------------------------- #
def fetch_day(api: Garmin, d: str, force: bool = False) -> dict[str, Any]:
    """Return cached raw bundle for a day, fetching if missing."""
    cache = RAW_DIR / f"{d}.json"
    if cache.exists() and not force:
        try:
            return json.loads(cache.read_text())
        except Exception:  # noqa: BLE001
            pass

    bundle = {
        "date": d,
        "summary": safe_call(api.get_user_summary, d),
        "sleep": safe_call(api.get_sleep_data, d),
    }
    write_json(cache, bundle)
    return bundle


# --------------------------------------------------------------------------- #
# Flatteners
# --------------------------------------------------------------------------- #
def flatten_daily(bundle: dict[str, Any]) -> dict[str, Any]:
    s = bundle.get("summary") or {}
    sleep = bundle.get("sleep") or {}
    dto = (sleep.get("dailySleepDTO") or {}) if isinstance(sleep, dict) else {}
    scores = dto.get("sleepScores") or {}

    def g(d: dict, *keys, default=None):
        for k in keys:
            if isinstance(d, dict) and d.get(k) is not None:
                return d.get(k)
        return default

    dist_m = g(s, "totalDistanceMeters", default=0) or 0
    sleep_secs = dto.get("sleepTimeSeconds")
    overall = scores.get("overall") or {}

    return {
        "date": bundle.get("date"),
        # activity / steps
        "steps": s.get("totalSteps"),
        "step_goal": s.get("dailyStepGoal"),
        "distance_km": round(dist_m / 1000, 2) if dist_m else None,
        "total_calories": s.get("totalKilocalories"),
        "active_calories": s.get("activeKilocalories"),
        "floors_ascended": s.get("floorsAscended"),
        "floors_goal": s.get("userFloorsAscendedGoal"),
        "moderate_intensity_min": s.get("moderateIntensityMinutes"),
        "vigorous_intensity_min": s.get("vigorousIntensityMinutes"),
        "intensity_min_goal": s.get("intensityMinutesGoal"),
        "highly_active_sec": s.get("highlyActiveSeconds"),
        "active_sec": s.get("activeSeconds"),
        "sedentary_sec": s.get("sedentarySeconds"),
        # heart / vitals
        "resting_hr": s.get("restingHeartRate"),
        "rhr_7day_avg": s.get("lastSevenDaysAvgRestingHeartRate"),
        "min_hr": s.get("minHeartRate"),
        "max_hr": s.get("maxHeartRate"),
        # stress
        "avg_stress": s.get("averageStressLevel"),
        "max_stress": s.get("maxStressLevel"),
        "stress_qualifier": s.get("stressQualifier"),
        # body battery
        "bb_high": s.get("bodyBatteryHighestValue"),
        "bb_low": s.get("bodyBatteryLowestValue"),
        "bb_charged": s.get("bodyBatteryChargedValue"),
        "bb_drained": s.get("bodyBatteryDrainedValue"),
        # respiration / spo2
        "avg_waking_respiration": s.get("avgWakingRespirationValue"),
        "avg_spo2": s.get("averageSpo2"),
        "lowest_spo2": s.get("lowestSpo2"),
        # sleep
        "sleep_hours": round(sleep_secs / 3600, 2) if sleep_secs else None,
        "deep_sleep_hours": _h(dto.get("deepSleepSeconds")),
        "light_sleep_hours": _h(dto.get("lightSleepSeconds")),
        "rem_sleep_hours": _h(dto.get("remSleepSeconds")),
        "awake_hours": _h(dto.get("awakeSleepSeconds")),
        "sleep_score": overall.get("value") if isinstance(overall, dict) else None,
    }


def _h(secs) -> float | None:
    return round(secs / 3600, 2) if secs else None


def flatten_activity(a: dict[str, Any]) -> dict[str, Any]:
    at = a.get("activityType") or {}
    speed = a.get("averageSpeed")  # m/s
    pace_min_km = (1000 / speed / 60) if speed else None
    dist_m = a.get("distance") or 0
    dur = a.get("duration") or 0
    return {
        "activity_id": a.get("activityId"),
        "name": a.get("activityName"),
        "type": at.get("typeKey"),
        "start_local": a.get("startTimeLocal"),
        "date": (a.get("startTimeLocal") or " ").split(" ")[0],
        "distance_km": round(dist_m / 1000, 3) if dist_m else None,
        "duration_min": round(dur / 60, 2) if dur else None,
        "pace_min_km": round(pace_min_km, 3) if pace_min_km else None,
        "avg_speed_kmh": round(speed * 3.6, 2) if speed else None,
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "calories": a.get("calories"),
        "elevation_gain": a.get("elevationGain"),
        "avg_cadence": a.get("averageRunningCadenceInStepsPerMinute"),
        "avg_power": a.get("avgPower"),
        "avg_stride_length_cm": a.get("avgStrideLength"),
        "avg_ground_contact_ms": a.get("avgGroundContactTime"),
        "avg_vertical_oscillation": a.get("avgVerticalOscillation"),
        "vo2max": a.get("vO2MaxValue"),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "training_load": a.get("activityTrainingLoad"),
        "training_effect_label": a.get("trainingEffectLabel"),
        "steps": a.get("steps"),
        "moderate_intensity_min": a.get("moderateIntensityMinutes"),
        "vigorous_intensity_min": a.get("vigorousIntensityMinutes"),
        "hr_zone_1": a.get("hrTimeInZone_1"),
        "hr_zone_2": a.get("hrTimeInZone_2"),
        "hr_zone_3": a.get("hrTimeInZone_3"),
        "hr_zone_4": a.get("hrTimeInZone_4"),
        "hr_zone_5": a.get("hrTimeInZone_5"),
    }


PR_LABELS = {
    1: ("1 km", "time"),
    2: ("1 mile", "time"),
    3: ("5 km", "time"),
    4: ("10 km", "time"),
    7: ("Longest run", "distance"),
    8: ("Longest ride", "distance"),
    12: ("Most steps (day)", "count"),
    13: ("Most steps (week)", "count"),
    14: ("Most steps (month)", "count"),
    16: ("Most floors (day)", "count"),
    17: ("Longest swim", "distance"),
}


def flatten_prs(prs: list[dict]) -> list[dict]:
    out = []
    for pr in prs or []:
        tid = pr.get("typeId")
        label, kind = PR_LABELS.get(tid, (f"PR type {tid}", "raw"))
        out.append(
            {
                "type_id": tid,
                "label": label,
                "kind": kind,
                "value": pr.get("value"),
                "activity_type": pr.get("activityType"),
                "activity_name": pr.get("activityName"),
                "date": (pr.get("prStartTimeGmtFormatted")
                         or pr.get("actStartDateTimeInGMTFormatted") or "")[:10],
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch Garmin data for the dashboard")
    ap.add_argument("--days", type=int, default=120,
                    help="days of detailed daily vitals (default 120)")
    ap.add_argument("--history-days", type=int, default=365,
                    help="days of activity/range history (default 365)")
    ap.add_argument("--refresh-today", action="store_true",
                    help="force re-fetch of the most recent 3 days")
    args = ap.parse_args()

    tokenstore = os.path.expanduser(os.getenv("GARMINTOKENS", "~/.garminconnect"))
    api = Garmin()
    api.login(tokenstore)
    log("Logged in with saved tokens.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    today = date.today()
    detail_start = today - timedelta(days=args.days)
    history_start = today - timedelta(days=args.history_days)

    # ---- profile -------------------------------------------------------- #
    profile = {
        "full_name": safe_call(api.get_full_name),
        "unit_system": safe_call(api.get_unit_system),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "detail_days": args.days,
        "history_days": args.history_days,
    }
    write_json(DATA_DIR / "profile.json", profile)
    log(f"Profile: {profile['full_name']} ({profile['unit_system']})")

    # ---- daily vitals (cached per day) ---------------------------------- #
    days = list(daterange(detail_start, today))
    daily_rows = []
    recent_cut = today - timedelta(days=3)
    for i, d in enumerate(days, 1):
        ds = d.isoformat()
        force = args.refresh_today and d >= recent_cut
        bundle = fetch_day(api, ds, force=force)
        daily_rows.append(flatten_daily(bundle))
        if i % 20 == 0 or i == len(days):
            log(f"  daily vitals {i}/{len(days)}")
        if not (RAW_DIR / f"{ds}.json").exists() or force:
            time.sleep(0.2)
    import pandas as pd
    pd.DataFrame(daily_rows).to_csv(DATA_DIR / "daily.csv", index=False)
    log(f"Wrote daily.csv ({len(daily_rows)} rows)")

    # ---- activities (range) --------------------------------------------- #
    acts = safe_call(api.get_activities_by_date,
                     history_start.isoformat(), today.isoformat()) or []
    act_rows = [flatten_activity(a) for a in acts]
    pd.DataFrame(act_rows).to_csv(DATA_DIR / "activities.csv", index=False)
    log(f"Wrote activities.csv ({len(act_rows)} activities)")

    # ---- daily steps long history (range) ------------------------------- #
    steps = safe_call(api.get_daily_steps,
                      history_start.isoformat(), today.isoformat()) or []
    pd.DataFrame(steps).to_csv(DATA_DIR / "steps_history.csv", index=False)
    log(f"Wrote steps_history.csv ({len(steps)} days)")

    # ---- weekly aggregates ---------------------------------------------- #
    weeks = max(8, args.history_days // 7)
    write_json(DATA_DIR / "weekly_steps.json",
               safe_call(api.get_weekly_steps, today.isoformat(), weeks) or [])
    write_json(DATA_DIR / "weekly_stress.json",
               safe_call(api.get_weekly_stress, today.isoformat(), weeks) or [])
    write_json(DATA_DIR / "weekly_intensity.json",
               safe_call(api.get_weekly_intensity_minutes, today.isoformat(), weeks) or [])
    log("Wrote weekly aggregates.")

    # ---- training & performance snapshots ------------------------------- #
    write_json(DATA_DIR / "training_status.json",
               safe_call(api.get_training_status, today.isoformat()))
    write_json(DATA_DIR / "max_metrics.json",
               safe_call(api.get_max_metrics, today.isoformat()))
    write_json(DATA_DIR / "race_predictions.json",
               safe_call(api.get_race_predictions))
    write_json(DATA_DIR / "personal_records.json",
               flatten_prs(safe_call(api.get_personal_record) or []))

    # body composition / weight (may be empty)
    write_json(DATA_DIR / "body_composition.json",
               safe_call(api.get_body_composition,
                         history_start.isoformat(), today.isoformat()))
    log("Wrote training/performance snapshots.")

    log("DONE. Data written to %s" % DATA_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
