#!/usr/bin/env python3
"""Daily Garmin coach: analyse today's metrics with Claude and send a WhatsApp plan.

Pipeline:
  1. (optional) refresh the cached Garmin data
  2. build an objective daily briefing from that data
  3. ask Claude (Opus 4.8) to act as coach and write a short daily plan
  4. send it over WhatsApp (CallMeBot by default) and archive a copy

Config lives in ``coach/config.yaml`` (copy from ``config.example.yaml``).
Secrets may also come from the environment and override the file:
  ANTHROPIC_API_KEY, CALLMEBOT_PHONE, CALLMEBOT_APIKEY
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import anthropic
import yaml

from build_briefing import build_briefing, summary_text
import senders

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS = HERE / "reports"
MODEL_DEFAULT = "claude-opus-4-8"

SYSTEM_PROMPT = """\
You are an elite endurance + strength coach and sports dietitian writing a \
SHORT daily WhatsApp briefing for one athlete. You blend marathon-running \
periodisation, hypertrophy training, and evidence-based nutrition.

You are given the athlete's profile, weekly training structure, goal, and an \
OBJECTIVE data briefing from their Garmin (recovery, training load, running \
volume, estimated calorie expenditure). Reason over the numbers, then decide:

1. READINESS — choose PUSH / MODERATE / EASY / REST and give a one-line reason \
grounded in the data. Use these signals:
   - Resting HR vs 60-day baseline (>=4 bpm over = under-recovered).
   - Form (TSB): >5 fresh, -10..5 balanced, <-10 fatigued, <-20 very fatigued.
   - Acute:chronic load ratio (ACWR): >1.3 = injury-risk spike, <0.8 = detraining.
   - Body Battery overnight low, sleep, stress.
2. TODAY'S SESSION — honour the planned modality (run / lift / rest) but adapt \
to recovery. If running, specify the type (easy / tempo / intervals / long), \
distance and a pace OR heart-rate target, and the purpose. If lifting, give a \
focus (push / pull / legs / upper / lower / full), 4-6 exercises with sets x reps \
and an RPE, ~45-60 min. If rest, prescribe mobility / easy walk. Keep them on \
track for ~3 runs + ~3 lifts per week toward a lean, muscular, athletic build.
3. FUEL — set today's calorie target and protein target in grams. The athlete \
is in the stated nutrition phase; calorie-cycle around their TDEE: higher on \
hard training days, lower on rest days, so the weekly average fits the phase \
(recomp ~= maintenance, cut = deficit, bulk = small surplus). Then give a \
concrete MEAL PLAN — breakfast, lunch and dinner (add a snack if needed to hit \
protein), built ONLY from the athlete's FOOD NOTES (the groceries they keep, \
meals they cook, cuisines, restrictions, schedule). For each meal give a short \
realistic description plus approx kcal and protein; the meals together should \
sum close to the day's calorie and protein targets. Keep it easy to actually \
make. Respect any restrictions in the food notes. Time carbs around training.
4. FLAG — if any metric is concerning (rising RHR, ACWR>1.3, very low Body \
Battery, poor sleep streak), say so in one short line. Otherwise omit.

Always honour the athlete's `coaching_notes` (extra preferences — e.g. tone, \
or a closing motivational quote). If they ask for something there, include it.

OUTPUT RULES — this is an EMAIL, so:
- Plain text only. NO markdown headings, NO **bold**, NO bullet syntax like "- ".
- Use short labelled lines and a few tasteful emojis (one per line max).
- Don't use too many emojis; keep it professional and readable.
- Be specific and motivating, not generic. Reference the athlete's actual numbers.
- You have room for the meal plan, but stay scannable — keep it under ~2200 characters.
- Start with a one-line greeting that includes the day and the readiness call.
- Output ONLY the message text — no preamble, no explanation.
"""


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config() -> dict:
    path = HERE / "config.yaml"
    if not path.exists():
        sys.exit("coach/config.yaml not found. Copy config.example.yaml and fill it in.")
    cfg = yaml.safe_load(path.read_text())

    # Local secret overrides (git-ignored); env vars still win over both.
    local = HERE / "config.local.yaml"
    if local.exists():
        _deep_merge(cfg, yaml.safe_load(local.read_text()) or {})

    # env overrides for secrets
    cfg.setdefault("anthropic", {})
    cfg["anthropic"]["api_key"] = (os.getenv("ANTHROPIC_API_KEY")
                                   or cfg["anthropic"].get("api_key") or "")

    # support `delivery:` (new) with a fallback to the old `whatsapp:` key
    delivery = cfg.get("delivery") or cfg.get("whatsapp") or {}
    cfg["delivery"] = delivery
    email = delivery.setdefault("email", {})
    email["password"] = os.getenv("SMTP_PASSWORD") or email.get("password") or ""
    if os.getenv("SMTP_USERNAME"):
        email["username"] = os.getenv("SMTP_USERNAME")
    if os.getenv("EMAIL_TO"):
        email["to"] = os.getenv("EMAIL_TO")
    cb = delivery.setdefault("callmebot", {})
    cb["phone"] = os.getenv("CALLMEBOT_PHONE") or cb.get("phone") or ""
    cb["apikey"] = os.getenv("CALLMEBOT_APIKEY") or cb.get("apikey") or ""
    return cfg


def refresh_data() -> None:
    """Pull the latest few days from Garmin before building the briefing."""
    script = REPO / "dashboard" / "fetch_data.py"
    print("Refreshing Garmin data ...", flush=True)
    subprocess.run([sys.executable, str(script), "--refresh-today"],
                   cwd=REPO / "dashboard", check=True)


def generate_message(cfg: dict, briefing: dict) -> str:
    client = anthropic.Anthropic(api_key=cfg["anthropic"]["api_key"] or None)
    model = cfg["anthropic"].get("model", MODEL_DEFAULT)

    nut = cfg["nutrition"]
    user_content = (
        "ATHLETE PROFILE:\n" + json.dumps(cfg["profile"], indent=2)
        + "\n\nGOAL: " + cfg["profile"].get("body_goal", "lean and muscular, athletic")
        + "\n\nWEEKLY STRUCTURE:\n" + json.dumps(cfg["schedule"], indent=2)
        + "\n\nNUTRITION: phase=" + nut.get("phase", "recomp")
        + f", meals_per_day={nut.get('meals_per_day', 3)}"
        + "\n\nFOOD NOTES (build the meal plan ONLY from these):\n"
        + (nut.get("food_notes") or "(none provided — give simple high-protein meals)")
        + "\n\nTODAY'S DATA BRIEFING (from Garmin):\n" + json.dumps(briefing, indent=2)
        + "\n\nWrite today's coaching message following all the output rules."
    )

    resp = client.messages.create(
        model=model,
        # With adaptive thinking, max_tokens is the COMBINED budget for thinking
        # + visible text. 4000 was too tight: at effort=medium the thinking alone
        # consumed it all and the model stopped (max_tokens) before writing the
        # message. 16000 leaves ample room for thinking plus the ~2200-char reply.
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise RuntimeError(
            f"Claude returned no text (stop_reason={resp.stop_reason}). "
            "Try raising max_tokens or lowering effort."
        )
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily Garmin coach → WhatsApp")
    ap.add_argument("--dry-run", action="store_true",
                    help="generate and print the message but do not send")
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip the Garmin data refresh (use cached data as-is)")
    args = ap.parse_args()

    cfg = load_config()

    if not args.no_refresh:
        try:
            refresh_data()
        except Exception as e:  # noqa: BLE001
            print(f"WARN: data refresh failed ({e}); using cached data.", flush=True)

    briefing = build_briefing(cfg["profile"], cfg["schedule"], cfg["nutrition"])
    print("\n=== BRIEFING ===\n" + summary_text(briefing) + "\n", flush=True)

    if not cfg["anthropic"]["api_key"]:
        sys.exit("No Anthropic API key (set ANTHROPIC_API_KEY or anthropic.api_key).")

    message = generate_message(cfg, briefing)
    print("=== MESSAGE ===\n" + message + "\n", flush=True)

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{date.today().isoformat()}.txt").write_text(message)

    if args.dry_run:
        print("(dry-run) not sending.", flush=True)
        return 0

    provider = (cfg["delivery"].get("provider") or "email").lower()
    subject = f"🏃 {briefing['weekday']} training plan — {briefing['date']}"
    try:
        senders.send(message, cfg["delivery"], subject=subject)
        print(f"Sent via {provider}.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR sending via {provider}: {e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
