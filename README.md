# 🌟 Match Max

A Telegram card-duel bot with matchmaking, levels, badges, tasks, and virtual-only currency.

Players are matched against real users when available, and bots fill empty seats so no one waits long. Everything runs through inline buttons — the only commands are `/start` and a few admin tools.

---

## 🎮 Features

- **Card duels** — 3 cards (1–9), best of 3 rounds, highest card wins
- **Three modes** — Solo (1v1), Duo (2v2), Squad (3v3)
- **Matchmaking** — real players first, bots fill gaps within 15 seconds
- **Levels 1–100** with 16 milestone badges
- **Daily Gift** with streak tracking
- **Tasks** — admins create join-channel tasks; users earn stars
- **Force-join** — require users to join specific channels before using the bot
- **Virtual currency only** — stars cannot be bought, sold, or cashed out
- **GitHub backup** — DB state persists across Render restarts
- **Health check** — HTTP endpoint for Render uptime monitoring

---

## 🚀 Deploy on Render

### 1. Fork or create a repo

Push these files:
- `game.py` (or `bot.py` — matches your Render start command)
- `requirements.txt`
- `README.md`

### 2. Create a GitHub backup repo

Create a **separate** repo to hold the database backup (e.g. `matchmax-backup`). It can be private or public. Can start empty.

### 3. Generate a GitHub token

Go to **github.com/settings/tokens?type=beta** → **Generate new token** (fine-grained).

- **Repository access:** Only select repositories → pick your backup repo
- **Permissions:** Repository permissions → **Contents: Read and write**
- Copy the token

### 4. Create the Telegram bot

- Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → get your **BOT_TOKEN**
- Talk to [@userinfobot](https://t.me/userinfobot) to get your own **Telegram user ID** for `ADMIN_IDS`

### 5. Add the bot as admin to any force-join channels

For every channel listed in `FORCE_CHANNELS`, add your bot as **admin**. No permissions needed — the bot just needs to be in the admin list to check membership.

### 6. Configure Render

**New → Web Service** → connect your repo.

- **Runtime:** Python 3
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python game.py` (or `python bot.py`)
- **Health Check Path:** `/`

### 7. Add environment variables

In Render → your service → **Environment**:

| Key | Value |
|---|---|
| `BOT_TOKEN` | your Telegram bot token |
| `GITHUB_TOKEN` | your fine-grained GitHub token |
| `GITHUB_OWNER` | `DarkLord813` |
| `GITHUB_REPO` | `matchmax-backup` |
| `GITHUB_BRANCH` | `main` |
| `DB_PATH` | `starcards.db` |
| `PORT` | `8080` |
| `ADMIN_IDS` | your Telegram user ID (comma-separated for multiple) |
| `FORCE_CHANNELS` | `@ERRORX404E,@NCKDEVFreeSC,@NCK_Dev` |

Optional tuning (defaults in parentheses):
| Key | Default | Meaning |
|---|---|---|
| `STARTING_STARS` | 200 | starting star balance |
| `CHECKIN_BASE` | 25 | base daily gift |
| `CHECKIN_PER_STREAK` | 5 | additional stars per streak day |
| `CHECKIN_CAP` | 100 | max daily gift |
| `MAX_LEVEL` | 100 | level cap |
| `QUEUE_WAIT` | 15 | seconds to wait for real players |
| `ROUND_TIMEOUT` | 60 | seconds before auto-play |
| `HOUSE_POOL_START` | 10000 | house pool max |
| `HOUSE_REFILL` | 500 | daily house pool refill |
| `TASK_REWARD_CAP` | 500 | max stars per task |

Click **Deploy**.

---

## 📦 Local development

```bash
# install
pip install -r requirements.txt

# configure
cp .env.example .env
# edit .env with your values

# run
python game.py
