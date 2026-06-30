# Daily Garmin Coach → Email

Every morning, Claude reads your Garmin metrics and emails you
**today's training session, a rest-or-push call, and your calorie + protein
targets** — tuned to your goal of a lean, muscular, athletic build on a 3-run /
3-lift week. (WhatsApp via CallMeBot/Twilio is also supported — set
`delivery.provider` in `config.yaml`.)

## How it works

```
fetch_data.py  →  build_briefing.py  →  Claude (Opus/Sonnet)  →  email (Gmail SMTP)
(Garmin data)     (objective signals)   (coaching decision)    (the message)
```

The briefing is **objective signals**, not raw dumps — so Claude reasons over the
same things a coach would:

- **Recovery** — resting HR vs your 60-day baseline, Body Battery overnight low, sleep, stress.
- **Training load** — Fitness (CTL), Fatigue (ATL), Form (TSB), and the acute:chronic ratio (injury-risk flag).
- **Running** — weekly volume vs trend, days since last run / hard workout, VO₂ max, recent sessions.
- **Nutrition** — your TDEE measured from Garmin's daily burn, calorie-cycled by training day.

Example output:

```
Tue 6/30 — EASY day 🟡 Form -28 means deep fatigue.
Run: 30 min easy, HR <150, conversational.
Fuel: ~2350 kcal, 165g protein. Carbs around the run.
Flag: fatigue high — hold back today.
```

---

## Setup

### 1. Install dependencies

```bash
# from the repo root, with the venv active
pip install -e .
pip install curl_cffi pandas -r coach/requirements.txt
```

### 2. Fill in your config

```bash
cp coach/config.example.yaml coach/config.yaml
```

Edit `coach/config.yaml` — especially **age, height, weight** (needed for the
protein/calorie math), your **weekly run/lift days**, and **`food_notes`** (how
you eat, so the meal plan uses foods you actually have).

`config.yaml` contains **no secrets**, so it's safe to commit. Secrets (Anthropic
key, SMTP password) come from environment variables / GitHub secrets, or from a
git-ignored **`coach/config.local.yaml`** for local runs:

```yaml
# coach/config.local.yaml  (git-ignored — merged over config.yaml)
anthropic:
  api_key: "sk-ant-..."
delivery:
  email:
    password: "your gmail app password"
```

### 3. Set up email (Gmail App Password)

The report is emailed via Gmail SMTP. Gmail won't accept your normal password for
SMTP — create an **App Password**:

1. Turn on 2-Step Verification: <https://myaccount.google.com/security>.
2. Create an App Password: <https://myaccount.google.com/apppasswords> (pick "Mail").
3. Put the 16-character password in `config.local.yaml` (`delivery.email.password`)
   for local runs, and as the `SMTP_PASSWORD` GitHub secret for the cloud run.

The `to`/`from`/`username` are already set to `sujitsilas@gmail.com`. Prefer a
different SMTP provider? Change `smtp_host`/`smtp_port` in `config.yaml`.

### 4. Get an Anthropic API key

Create one at <https://console.anthropic.com> → set it as `ANTHROPIC_API_KEY`
(preferred) or in `config.local.yaml`. A daily run costs about a cent or two.

### 5. Test it

```bash
cd coach
# build the briefing + generate the message, but DON'T send:
ANTHROPIC_API_KEY=sk-... python coach.py --dry-run --no-refresh
# happy with it? send it for real:
ANTHROPIC_API_KEY=sk-... python coach.py
```

---

## Run it automatically every morning (GitHub Actions)

The workflow [`.github/workflows/daily-coach.yml`](../.github/workflows/daily-coach.yml)
runs in the cloud at **14:00 UTC (~7am Pacific)** so it fires even when your
laptop is off. Push your fork to GitHub, then add these **repository secrets**
(Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `GARMIN_TOKEN_B64` | base64 of your saved Garmin token (command below) |
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `SMTP_PASSWORD` | your Gmail App Password |

(`config.yaml` is committed, so the runner reads your profile/food notes straight
from the repo — no `COACH_CONFIG` secret needed.)

Generate the token secret value without printing it to your screen:

```bash
base64 -i ~/.garminconnect/garmin_tokens.json | pbcopy   # now paste into the secret
```
Trigger a manual run from the **Actions** tab → *Daily Garmin Coach* → *Run
workflow* to test before relying on the schedule.

Notes:
- GitHub cron is **UTC** and does not shift for daylight saving — adjust the cron
  in the workflow if you want a fixed local time year-round.
- The Garmin token auto-refreshes, but its long-lived consumer token expires
  ~yearly; if Actions starts failing auth, re-run `base64 ...` and update the secret.

---

## Keep the local dashboard fresh (launchd)

Already installed by `scripts/install-launchd.sh` — a `launchd` job refreshes the
dashboard data on your Mac daily at 06:00. Manage it with:

```bash
launchctl start com.garmin.dashboard.refresh     # run now
launchctl unload ~/Library/LaunchAgents/com.garmin.dashboard.refresh.plist  # disable
```

(The GitHub Action fetches its own fresh copy in the cloud, so the two are
independent.)

---

## Tuning the coaching

- **Nutrition phase** — `recomp` (default), `cut`, or `bulk` in `config.yaml`.
- **Model** — defaults to `claude-opus-4-8`; switch to `claude-sonnet-4-6` to cut cost.
- **Coaching style / rules** — edit `SYSTEM_PROMPT` in `coach.py`.
- **Weekly structure** — change `run_days` / `lift_days` in `config.yaml`.

## Privacy

`config.yaml` is committable but holds **no secrets** — secrets live in the
git-ignored `config.local.yaml` (local) or GitHub secrets (cloud). `reports/`,
the dashboard `data/`, and your Garmin token are also git-ignored, so your health
data and secrets are never committed. Note: committing `config.yaml` puts your
profile (age, weight, food notes) in the repo — keep your fork private if that
matters to you.
