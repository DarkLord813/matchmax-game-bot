import os, sqlite3, random, logging, asyncio, base64, threading, time, requests
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters,
)
from telegram.error import TelegramError

load_dotenv()

TOKEN = os.environ["BOT_TOKEN"]
DB = os.getenv("DB_PATH", "starcards.db")
STARTING_STARS = int(os.getenv("STARTING_STARS", "200"))
CHECKIN_BASE = int(os.getenv("CHECKIN_BASE", "25"))
CHECKIN_PER_STREAK = int(os.getenv("CHECKIN_PER_STREAK", "5"))
CHECKIN_CAP = int(os.getenv("CHECKIN_CAP", "100"))
MAX_LEVEL = int(os.getenv("MAX_LEVEL", "100"))
QUEUE_WAIT = int(os.getenv("QUEUE_WAIT", "15"))
ROUND_TIMEOUT = int(os.getenv("ROUND_TIMEOUT", "60"))
HOUSE_POOL_START = int(os.getenv("HOUSE_POOL_START", "10000"))
HOUSE_REFILL = int(os.getenv("HOUSE_REFILL", "500"))
TASK_REWARD_CAP = int(os.getenv("TASK_REWARD_CAP", "500"))
BACKUP_DEBOUNCE = int(os.getenv("BACKUP_DEBOUNCE", "30"))
FORCE_CHANNELS = [c.strip() for c in os.getenv("FORCE_CHANNELS", "").split(",") if c.strip()]
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER  = os.getenv("GITHUB_OWNER", "DarkLord813")
GITHUB_REPO   = os.getenv("GITHUB_REPO", "matchmax-backup")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_API    = "https://api.github.com"

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s")

BOT_NAMES = [
    "Alex","Sam","Jordan","Riley","Casey","Morgan","Taylor","Jamie",
    "Avery","Quinn","Devon","Skylar","River","Kai","Nova","Echo",
    "Sage","Rowan","Blake","Drew","Emery","Finley","Harper","Indigo",
    "Jules","Kendall","Logan","Marlowe","Noor","Onyx","Phoenix","Reece",
    "Sasha","Toby","Wren","Zara","Aria","Leo","Mia","Noah","Zoe",
    "Idris","Amara","Tunde","Chidi","Nia","Zainab","Yemi","Ada",
    "Kofi","Lulu","Dami","Efe","Ife","Kemi","Sade","Tayo",
]
BOT_EMOJI = ["🌟","🎯","🔥","🎨","🚀","🦊","🐼","🐯","🦁","🐺","🦉","🐢"]

BADGES = {
    1:("🌱","Newcomer"), 5:("⭐","Rising Star"), 10:("🔥","Contender"),
    15:("⚡","Spark"), 20:("🛡️","Defender"), 25:("🎯","Sharpshooter"),
    30:("💎","Gem"), 35:("🌊","Tide"), 40:("🌪️","Storm"),
    45:("🦅","Falcon"), 50:("👑","Champion"), 60:("🐉","Dragon"),
    70:("🔱","Trident"), 80:("🌌","Cosmic"), 90:("☄️","Comet"),
    100:("🏆","Legend"),
}

DIRTY = threading.Event()


# ---------------- GitHub backup ----------------
def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _gh_file_url():
    return f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{DB}"

def _gh_enabled():
    return bool(GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO)

def mark_dirty():
    DIRTY.set()

def gh_pull_db():
    if not _gh_enabled():
        logging.info("github backup disabled")
        return False
    try:
        r = requests.get(_gh_file_url(), headers=_gh_headers(),
                         params={"ref": GITHUB_BRANCH}, timeout=15)
        if r.status_code == 404:
            logging.info("no remote DB yet — starting fresh")
            return False
        r.raise_for_status()
        content = base64.b64decode(r.json()["content"])
        with open(DB, "wb") as f:
            f.write(content)
        logging.info(f"pulled DB from github ({len(content)} bytes)")
        return True
    except Exception as e:
        logging.warning(f"github pull failed: {e}")
        return False

def gh_push_db():
    if not _gh_enabled() or not os.path.exists(DB):
        return False
    try:
        with open(DB, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        sha = None
        r = requests.get(_gh_file_url(), headers=_gh_headers(),
                         params={"ref": GITHUB_BRANCH}, timeout=15)
        if r.status_code == 200:
            sha = r.json().get("sha")
        payload = {
            "message": f"backup {datetime.utcnow().isoformat()}",
            "content": encoded,
            "branch": GITHUB_BRANCH,
        }
        if sha: payload["sha"] = sha
        r = requests.put(_gh_file_url(), headers=_gh_headers(), json=payload, timeout=30)
        r.raise_for_status()
        logging.info("pushed DB to github")
        return True
    except Exception as e:
        logging.warning(f"github push failed: {e}")
        return False

def gh_backup_loop(interval=300, debounce=BACKUP_DEBOUNCE):
    if not _gh_enabled():
        logging.info("backup loop disabled")
        return
    state = {"last_force": time.time()}
    def loop():
        while True:
            time.sleep(debounce)
            now = time.time()
            force_due = (now - state["last_force"]) >= interval
            if DIRTY.is_set() or force_due:
                ok = gh_push_db()
                if ok:
                    DIRTY.clear()
                    state["last_force"] = now
    threading.Thread(target=loop, daemon=True, name="github-backup").start()
    logging.info(f"github backup loop started (debounce {debounce}s, force {interval}s)")

def start_health_server():
    port = int(os.getenv("PORT", "8080"))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *a): pass
    def serve():
        try:
            HTTPServer(("0.0.0.0", port), H).serve_forever()
        except Exception as e:
            logging.warning(f"health server failed: {e}")
    threading.Thread(target=serve, daemon=True, name="health").start()
    logging.info(f"health server on :{port}")

def start_keepalive_loop(interval=600):
    url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("KEEPALIVE_URL", "")
    if not url:
        logging.info("keepalive disabled (no RENDER_EXTERNAL_URL)")
        return
    ping_url = url.rstrip("/") + "/"
    def loop():
        while True:
            time.sleep(interval)
            try:
                r = requests.get(ping_url, timeout=10)
                logging.info(f"keepalive ping {ping_url} → {r.status_code}")
            except Exception as e:
                logging.warning(f"keepalive ping failed: {e}")
    threading.Thread(target=loop, daemon=True, name="keepalive").start()
    logging.info(f"keepalive loop started ({interval}s → {ping_url})")


# ---------------- Level math ----------------
def exp_for_level(lv):
    if lv <= 1: return 0
    total = 0
    for n in range(1, lv):
        if n <= 20: total += 80 * n
        elif n <= 60: total += 150 * n
        else: total += 250 * n
    return total

def level_from_exp(exp):
    lv = 1
    while lv < MAX_LEVEL and exp >= exp_for_level(lv + 1):
        lv += 1
    return lv

def exp_progress(exp):
    lv = level_from_exp(exp)
    if lv >= MAX_LEVEL: return lv, 0, 0
    floor = exp_for_level(lv); ceil = exp_for_level(lv + 1)
    return lv, exp - floor, ceil - floor

def badge_for_level(lv):
    best = 1
    for m in sorted(BADGES):
        if lv >= m: best = m
    return BADGES[best]

def badges_earned(lv):
    return [(m, BADGES[m]) for m in sorted(BADGES) if lv >= m]

def safe(n):
    if not n: return "Player"
    for c in "_*[]`": n = n.replace(c, f"\\{c}")
    return n[:32]

def name_full(name, exp, is_bot=False):
    lv = level_from_exp(exp)
    badge, _ = badge_for_level(lv)
    tag = "🤖 " if is_bot else ""
    return f"{tag}{safe(name)} {badge} \\[Lv{lv}\\]"

def is_admin(uid): return uid in ADMIN_IDS


# ---------------- DB ----------------
def db(): return sqlite3.connect(DB)

def init_db():
    gh_pull_db()
    con = db(); cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, name TEXT,
        stars INTEGER DEFAULT 200, exp INTEGER DEFAULT 0,
        streak INTEGER DEFAULT 0, last_gift TEXT,
        wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        seen_tutorial INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS house(
        id INTEGER PRIMARY KEY CHECK(id=1),
        pool INTEGER, last_refill TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS tasks(
        task_id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT, reward INTEGER, title TEXT,
        created_by INTEGER, created_at TEXT,
        active INTEGER DEFAULT 1)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS task_completions(
        user_id INTEGER, task_id INTEGER, completed_at TEXT,
        PRIMARY KEY (user_id, task_id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS admin_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER, action TEXT, details TEXT,
        created_at TEXT)""")
    con.commit()
    cur.execute("INSERT OR IGNORE INTO house(id,pool,last_refill) VALUES(1,?,?)",
                (HOUSE_POOL_START, datetime.utcnow().isoformat()))
    con.commit(); con.close()

def get_user(uid, name=None):
    con = db(); cur = con.cursor()
    cur.execute("""SELECT user_id,name,stars,exp,streak,last_gift,
                   wins,losses,seen_tutorial FROM users WHERE user_id=?""", (uid,))
    r = cur.fetchone()
    if not r:
        cur.execute("INSERT INTO users(user_id,name,stars) VALUES(?,?,?)",
                    (uid, name or "Player", STARTING_STARS))
        con.commit()
        r = (uid, name or "Player", STARTING_STARS, 0, 0, None, 0, 0, 0)
        mark_dirty()
    con.close(); return r

def upd(uid, **fields):
    if not fields: return
    con = db(); cur = con.cursor()
    for k, v in fields.items():
        cur.execute(f"UPDATE users SET {k}=? WHERE user_id=?", (v, uid))
    con.commit(); con.close()
    mark_dirty()

def add_user(uid, stars=0, exp=0, wins=0, losses=0):
    con = db(); cur = con.cursor()
    cur.execute("""UPDATE users SET stars=stars+?, exp=exp+?,
                   wins=wins+?, losses=losses+? WHERE user_id=?""",
                (stars, exp, wins, losses, uid))
    con.commit(); con.close()
    mark_dirty()

def house_pool():
    con = db(); cur = con.cursor()
    cur.execute("SELECT pool,last_refill FROM house WHERE id=1")
    pool, last = cur.fetchone()
    if last:
        last_dt = datetime.fromisoformat(last)
        if datetime.utcnow() - last_dt >= timedelta(hours=24):
            pool = min(pool + HOUSE_REFILL, HOUSE_POOL_START)
            cur.execute("UPDATE house SET pool=?,last_refill=? WHERE id=1",
                        (pool, datetime.utcnow().isoformat()))
            con.commit()
            mark_dirty()
    con.close(); return pool

def house_add(delta):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE house SET pool = MAX(0, pool + ?) WHERE id=1", (delta,))
    con.commit(); con.close()
    mark_dirty()

def top_players(limit=10):
    con = db(); cur = con.cursor()
    cur.execute("""SELECT name,stars,exp FROM users
                   ORDER BY exp DESC, stars DESC LIMIT ?""", (limit,))
    r = cur.fetchall(); con.close(); return r

def total_users():
    con = db(); cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    n = cur.fetchone()[0]; con.close(); return n

def create_task(channel, reward, title, admin_id):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT INTO tasks(channel,reward,title,created_by,created_at)
                   VALUES(?,?,?,?,?)""",
                (channel, reward, title, admin_id, datetime.utcnow().isoformat()))
    tid = cur.lastrowid; con.commit(); con.close()
    mark_dirty()
    return tid

def list_tasks(active_only=True):
    con = db(); cur = con.cursor()
    if active_only:
        cur.execute("SELECT task_id,channel,reward,title FROM tasks WHERE active=1 ORDER BY task_id")
    else:
        cur.execute("SELECT task_id,channel,reward,title,active FROM tasks ORDER BY task_id")
    r = cur.fetchall(); con.close(); return r

def get_task(tid):
    con = db(); cur = con.cursor()
    cur.execute("SELECT task_id,channel,reward,title,active FROM tasks WHERE task_id=?", (tid,))
    r = cur.fetchone(); con.close(); return r

def delete_task(tid):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE tasks SET active=0 WHERE task_id=?", (tid,))
    con.commit(); con.close()
    mark_dirty()

def has_completed(uid, tid):
    con = db(); cur = con.cursor()
    cur.execute("SELECT 1 FROM task_completions WHERE user_id=? AND task_id=?", (uid, tid))
    r = cur.fetchone(); con.close(); return r is not None

def mark_completed(uid, tid):
    con = db(); cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO task_completions(user_id,task_id,completed_at) VALUES(?,?,?)",
                (uid, tid, datetime.utcnow().isoformat()))
    con.commit(); con.close()
    mark_dirty()

def log_admin(admin_id, action, details):
    con = db(); cur = con.cursor()
    cur.execute("INSERT INTO admin_log(admin_id,action,details,created_at) VALUES(?,?,?,?)",
                (admin_id, action, details, datetime.utcnow().isoformat()))
    con.commit(); con.close()
    mark_dirty()

def task_stats():
    con = db(); cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM tasks WHERE active=1")
    active = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM task_completions")
    completions = cur.fetchone()[0]
    con.close(); return active, completions


def profile_card(uid, name):
    _,_,s,e,st,last,w,l,seen = get_user(uid, name)
    lv, into, need = exp_progress(e)
    badge, title = badge_for_level(lv)
    if lv >= MAX_LEVEL:
        bar = "▓" * 10; exp_txt = "MAX LEVEL"
    else:
        pct = int(into / need * 10) if need else 0
        bar = "▓" * pct + "░" * (10 - pct)
        exp_txt = f"{into}/{need} EXP"
    nxt = None
    for m in sorted(BADGES):
        if m > lv: nxt = m; break
    next_line = ""
    if nxt:
        b2, t2 = BADGES[nxt]
        next_line = f"\n_Next: {b2} {t2} at Lv{nxt}_"
    total = total_users()
    return (
        f"🌟 *Star Cards*\n\n"
        f"👤 {safe(name)} {badge} *{title}* \\[Lv{lv}\\]\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Progress*\n`{bar}`  {exp_txt}{next_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⭐ Stars: *{s}*\n"
        f"🔥 Streak: *{st} days*\n"
        f"🏆 Record: *{w}W / {l}L*\n"
        f"👥 Total Players: *{total:,}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_Stars are virtual — no real money._"
    )


GAMES = {}
QUEUES = {"solo": [], "duo": [], "squad": []}
NEXT_GID = [1000]
ADMIN_FLOW = {}

def new_gid():
    NEXT_GID[0] += 1
    return NEXT_GID[0]

def new_hand(): return random.sample(range(1, 10), 3)
def random_bot_name(): return f"{random.choice(BOT_EMOJI)} {random.choice(BOT_NAMES)}"


def main_menu(uid):
    rows = [
        [InlineKeyboardButton("🃏 Play", callback_data="p:modes"),
         InlineKeyboardButton("🎁 Daily Gift", callback_data="m:gift")],
        [InlineKeyboardButton("📋 Tasks", callback_data="t:list"),
         InlineKeyboardButton("📈 Progress", callback_data="m:prog")],
        [InlineKeyboardButton("🏅 Star Chart", callback_data="m:chart"),
         InlineKeyboardButton("🏦 House Pool", callback_data="m:house")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="m:home"),
         InlineKeyboardButton("📖 Help", callback_data="h:page:0")],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="a:panel")])
    return InlineKeyboardMarkup(rows)

def back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="m:home")]])

def admin_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin", callback_data="a:panel")]])

def mode_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🃏 Solo (1v1)", callback_data="q:solo")],
        [InlineKeyboardButton("👥 Duo (2v2)", callback_data="q:duo")],
        [InlineKeyboardButton("⚔️ Squad (3v3)", callback_data="q:squad")],
        [InlineKeyboardButton("⬅️ Back", callback_data="m:home")],
    ])

def stake_kb(mode, level):
    cap = max(5, level * 10)
    opts = [o for o in [5, 10, 25, 50, 100, 250] if o <= cap] or [5]
    rows, row = [], []
    for o in opts:
        row.append(InlineKeyboardButton(f"{o}⭐", callback_data=f"q2:{mode}:{o}"))
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("Friendly (0)", callback_data=f"q2:{mode}:0")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="m:home")])
    return InlineKeyboardMarkup(rows)

def card_kb(gid, hand, rnd):
    rows, row = [], []
    for n in range(1, 10):
        if n in hand:
            row.append(InlineKeyboardButton(str(n), callback_data=f"c:{gid}:{rnd}:{n}"))
        else:
            row.append(InlineKeyboardButton("·", callback_data="c:noop"))
        if len(row) == 3: rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)

def reward_keypad_kb(current=""):
    display = current if current else "0"
    rows = [
        [InlineKeyboardButton("⌫ Back", callback_data="k:back"),
         InlineKeyboardButton(f"  {display}  ", callback_data="k:noop"),
         InlineKeyboardButton("✅ Done", callback_data="k:done")],
        [InlineKeyboardButton("1", callback_data="k:d:1"),
         InlineKeyboardButton("2", callback_data="k:d:2"),
         InlineKeyboardButton("3", callback_data="k:d:3")],
        [InlineKeyboardButton("4", callback_data="k:d:4"),
         InlineKeyboardButton("5", callback_data="k:d:5"),
         InlineKeyboardButton("6", callback_data="k:d:6")],
        [InlineKeyboardButton("7", callback_data="k:d:7"),
         InlineKeyboardButton("8", callback_data="k:d:8"),
         InlineKeyboardButton("9", callback_data="k:d:9")],
        [InlineKeyboardButton("0", callback_data="k:d:0"),
         InlineKeyboardButton("✖ Cancel", callback_data="a:panel")],
    ]
    return InlineKeyboardMarkup(rows)


CHANNEL_LABELS = ["⧉ ERROR/404", "📦 NCK DEV Source Codes", "🛠️ NCK DEV"]

async def is_member(ctx, user_id, channel):
    try:
        m = await ctx.bot.get_chat_member(chat_id=channel, user_id=user_id)
        return m.status in ("member", "administrator", "creator")
    except TelegramError as e:
        logging.warning(f"member check failed {channel} {user_id}: {e}")
        return False

async def check_force_channels(ctx, user_id):
    missing = []
    for ch in FORCE_CHANNELS:
        if not await is_member(ctx, user_id, ch):
            missing.append(ch)
    return missing

def force_join_kb():
    rows = []
    for i, ch in enumerate(FORCE_CHANNELS):
        label = CHANNEL_LABELS[i] if i < len(CHANNEL_LABELS) else ch
        link = f"https://t.me/{ch.lstrip('@')}"
        rows.append([InlineKeyboardButton(f"📢 Join {label}", url=link)])
    rows.append([InlineKeyboardButton("✅ I've Joined", callback_data="fj:check")])
    return InlineKeyboardMarkup(rows)

FORCE_JOIN_TEXT = (
    "🔒 *Access Locked*\n\n"
    "To use this bot, please join our channels first.\n\n"
    "Tap each button, join, then tap *I've Joined*."
)

async def enforce_join(target, ctx):
    if not FORCE_CHANNELS:
        return True, None
    if hasattr(target, "effective_user"):
        uid = target.effective_user.id
        send_to = target.message
        is_callback = False
    elif hasattr(target, "from_user"):
        uid = target.from_user.id
        send_to = target.message
        is_callback = True
    else:
        return True, None
    missing = await check_force_channels(ctx, uid)
    if not missing:
        return True, None
    kb = force_join_kb()
    if is_callback:
        try:
            await target.edit_message_text(FORCE_JOIN_TEXT, parse_mode="Markdown", reply_markup=kb)
        except TelegramError:
            await send_to.reply_text(FORCE_JOIN_TEXT, parse_mode="Markdown", reply_markup=kb)
    else:
        await send_to.reply_text(FORCE_JOIN_TEXT, parse_mode="Markdown", reply_markup=kb)
    return False, missing

async def on_join_check(update, ctx):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    missing = await check_force_channels(ctx, uid)
    if missing:
        await q.answer(f"❌ Still missing {len(missing)} channel(s).", show_alert=True)
        return
    u = q.from_user
    get_user(u.id, u.first_name)
    await q.edit_message_text(profile_card(u.id, u.first_name),
                              parse_mode="Markdown", reply_markup=main_menu(u.id))


HELP_PAGES = [
    "🌟 *Welcome to Star Cards!*\n\nA quick guide. Tap ➡️ to continue.",
    "🃏 *Your Hand*\n\nEach match you get 3 cards (1–9).\nBigger number = stronger card.\nOnly you see your cards.",
    "🎴 *Playing a Round*\n\n1. Bot sends you a 3×3 grid\n2. Tap a card\n3. Opponent taps too\n4. Higher card wins\n5. Used cards can't be replayed\n\nWin 2 of 3 rounds to win.",
    "🧠 *Strategy*\n\n• Save a high card for a decisive round\n• If up 1–0, play safe\n• If down 0–1, go big\n• Ties don't score",
    "🎮 *Modes*\n\n🃏 Solo — 1v1\n👥 Duo — 2v2 (team sum)\n⚔️ Squad — 3v3 (team sum)\n\nFriendly (0⭐) = no stakes, no level limits.",
    "⭐ *Stars*\n\nVirtual points — *not* real money.\n\nWin → gain stake\nLose → lose stake\nFriendly → nothing moves\n\nMax stake = Level × 10.",
    "📈 *Levels & Badges*\n\nEarn EXP every match — win or lose.\nMax level 100 = 🏆 Legend.\n\nBadges at Lv 5 ⭐, 10 🔥, 20 🛡️, 30 💎, 50 👑, 100 🏆.\nBadges are permanent.",
    "🎁 *Daily Gift*\n\nClaim once every 24h.\nDay 1 = 25⭐ · Day 7 = 55⭐ · Day 14+ = 100⭐\n\nMiss a day → streak resets.",
    "📋 *Tasks*\n\nAdmins post tasks (like joining a channel).\nComplete them to earn bonus stars.\n\nTap 📋 Tasks in the menu to see what's available.",
    "⚖️ *Fair Play*\n\n• Matchmaking within ±3 levels\n• EXP earned even when losing\n• Auto-play after 60s idle\n• /cancel forfeits mid-match\n• No real money",
    "🎈 *House Rules*\n\n• Be kind\n• Stars are for fun\n• Report anyone asking for money\n• Have fun!",
    "📌 *Quick Reference*\n\n/start — main menu\n/cancel — forfeit current match\n🃏 Play → mode → stake → match\n🎁 Daily Gift\n📋 Tasks\n\nGood luck! 🌟",
]

def help_kb(page):
    row = []
    if page > 0: row.append(InlineKeyboardButton("⬅️ Back", callback_data=f"h:page:{page-1}"))
    if page < len(HELP_PAGES) - 1: row.append(InlineKeyboardButton("➡️ Next", callback_data=f"h:page:{page+1}"))
    kb = [row] if row else []
    kb.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="m:home")])
    return InlineKeyboardMarkup(kb)


async def start(update, ctx):
    ok, missing = await enforce_join(update, ctx)
    if not ok: return
    u = update.effective_user
    get_user(u.id, u.first_name)
    await update.message.reply_text(
        profile_card(u.id, u.first_name),
        parse_mode="Markdown", reply_markup=main_menu(u.id))


async def cancel_match(update, ctx):
    u = update.effective_user
    gid_found = None
    for gid, g in GAMES.items():
        all_ids = [p["uid"] for p in g["teams"][0]] + [p["uid"] for p in g["teams"][1]]
        if u.id in all_ids and not g["is_bot"].get(u.id):
            gid_found = gid; break
    in_queue = False
    for m in QUEUES:
        before = len(QUEUES[m])
        QUEUES[m] = [p for p in QUEUES[m] if p["uid"] != u.id]
        if len(QUEUES[m]) < before: in_queue = True
    if gid_found is None and not in_queue:
        await update.message.reply_text("No active match or search.",
                                        reply_markup=main_menu(u.id)); return
    if in_queue and gid_found is None:
        await update.message.reply_text("✅ Search cancelled.",
                                        reply_markup=main_menu(u.id)); return
    await forfeit_match(ctx, gid_found, u.id, reason="cancelled")
    await update.message.reply_text("🚪 You left the match. Stake forfeited.",
                                    reply_markup=main_menu(u.id))


async def menu(update, ctx):
    q = update.callback_query; await q.answer()
    parts = q.data.split(":"); a = parts[1] if len(parts) > 1 else ""
    u = q.from_user
    ok, missing = await enforce_join(q, ctx)
    if not ok: return
    _,_,s,e,st,last,w,l,seen = get_user(u.id, u.first_name)

    if a == "home":
        await q.edit_message_text(profile_card(u.id, u.first_name),
                                  parse_mode="Markdown", reply_markup=main_menu(u.id))

    elif a == "prog":
        lv, into, need = exp_progress(e)
        if lv >= MAX_LEVEL:
            bar = "▓" * 10; exp_txt = "MAX LEVEL"
        else:
            pct = int(into / need * 10) if need else 0
            bar = "▓" * pct + "░" * (10 - pct)
            exp_txt = f"*{into}/{need}*"
        earned = badges_earned(lv)
        bl = "\n".join(f"{b[0]} Lv{m} {b[1]}" for m, b in earned[-6:]) or "_None yet_"
        nxt = None
        for m in sorted(BADGES):
            if m > lv: nxt = m; break
        nt = f"\n\nNext badge at *Lv{nxt}*: {BADGES[nxt][0]} {BADGES[nxt][1]}" if nxt else "\n\n🏆 Max badge reached!"
        await q.edit_message_text(
            f"📈 *Progress*\n\nLevel *{lv}*\nEXP: {exp_txt}\n`{bar}`\n\n"
            f"Total EXP: *{e}*\nStreak: *{st}* 🔥\n\n"
            f"*Recent badges:*\n{bl}{nt}",
            parse_mode="Markdown", reply_markup=back())

    elif a == "gift":
        if last:
            dt = datetime.fromisoformat(last)
            elapsed = datetime.utcnow() - dt
            if elapsed < timedelta(hours=24):
                wait = timedelta(hours=24) - elapsed
                await q.edit_message_text(
                    f"⏳ Come back in *{wait.seconds//3600}h {(wait.seconds%3600)//60}m*.",
                    parse_mode="Markdown", reply_markup=back()); return
            if elapsed > timedelta(hours=48): st = 0
        new_st = st + 1
        reward = min(CHECKIN_BASE + st * CHECKIN_PER_STREAK, CHECKIN_CAP)
        exp_r = min(5 + st, 20)
        old_lv = level_from_exp(e)
        add_user(u.id, stars=reward, exp=exp_r)
        upd(u.id, streak=new_st, last_gift=datetime.utcnow().isoformat())
        _,_,ns,ne,_,_,_,_,_ = get_user(u.id, u.first_name)
        new_lv = level_from_exp(ne)
        txt = (f"🎁 *Daily Gift!*\n\n🔥 Streak: *{new_st} days*\n"
               f"+{reward} ⭐  +{exp_r} EXP\n\nBalance: *{ns}* ⭐")
        if new_lv > old_lv:
            badge, title = badge_for_level(new_lv)
            txt += f"\n\n🎉 *Level {new_lv}!* {badge} *{title}*"
            if new_lv in BADGES: txt += "\n🏅 New badge unlocked!"
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=back())

    elif a == "chart":
        rows = top_players()
        if not rows: txt = "No players yet."
        else:
            lines = ["🏅 *Star Chart*"]
            for i, (n, s2, e2) in enumerate(rows):
                medal = ["🥇","🥈","🥉"][i] if i < 3 else f"{i+1}."
                lines.append(f"{medal} {name_full(n, e2)} — *{s2}* ⭐")
            txt = "\n".join(lines)
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=back())

    elif a == "house":
        pool = house_pool()
        pct = int(pool / HOUSE_POOL_START * 100)
        bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
        await q.edit_message_text(
            f"🏦 *House Pool*\n\n`{bar}`\n*{pool}* / {HOUSE_POOL_START} ⭐\n\n"
            f"_Refills up to {HOUSE_REFILL}⭐ per day._",
            parse_mode="Markdown", reply_markup=back())


async def help_router(update, ctx):
    q = update.callback_query; await q.answer()
    parts = q.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    page = max(0, min(page, len(HELP_PAGES) - 1))
    await q.edit_message_text(HELP_PAGES[page], parse_mode="Markdown",
                              reply_markup=help_kb(page))


async def play_router(update, ctx):
    q = update.callback_query; await q.answer()
    u = q.from_user
    ok, missing = await enforce_join(q, ctx)
    if not ok: return
    _,_,_,e,_,_,_,_,seen = get_user(u.id, u.first_name)
    if not seen:
        upd(u.id, seen_tutorial=1)
        await q.edit_message_text(
            "📖 *Quick tutorial before your first match*\n\n" + HELP_PAGES[0],
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➡️ Next", callback_data="h:page:1")],
                [InlineKeyboardButton("⏭️ Skip to Play", callback_data="p:modes")]]))
        return
    await q.edit_message_text("🃏 *Choose a Mode*", parse_mode="Markdown",
                              reply_markup=mode_kb())


async def tasks_router(update, ctx):
    q = update.callback_query; await q.answer()
    u = q.from_user
    parts = q.data.split(":")
    sub = parts[1] if len(parts) > 1 else "list"

    if sub == "list":
        tasks = list_tasks(active_only=True)
        if not tasks:
            await q.edit_message_text(
                "📋 *Tasks*\n\nNo tasks available right now.\nCheck back later!",
                parse_mode="Markdown", reply_markup=back()); return
        rows = []
        for tid, ch, reward, title in tasks:
            done = has_completed(u.id, tid)
            label = f"{'✅' if done else '⏳'} {title} (+{reward}⭐)"
            rows.append([InlineKeyboardButton(label, callback_data=f"t:view:{tid}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="m:home")])
        await q.edit_message_text(
            "📋 *Tasks*\n\nComplete these to earn bonus stars.\nTap one to see details:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

    elif sub == "view":
        tid = int(parts[2])
        t = get_task(tid)
        if not t or t[4] == 0:
            await q.edit_message_text("Task no longer available.", reply_markup=back()); return
        _, ch, reward, title, active = t
        done = has_completed(u.id, tid)
        link = f"https://t.me/{ch.lstrip('@')}"
        status = "✅ *Completed*" if done else "⏳ *Not yet claimed*"
        rows = [[InlineKeyboardButton(f"📢 Go to {ch}", url=link)]]
        if not done:
            rows.append([InlineKeyboardButton("✅ Check Completion", callback_data=f"t:check:{tid}")])
        rows.append([InlineKeyboardButton("⬅️ Tasks", callback_data="t:list")])
        await q.edit_message_text(
            f"📋 *{safe(title)}*\n\nChannel: {ch}\nReward: *{reward}* ⭐\nStatus: {status}\n\n"
            + ("_You've already claimed this task._" if done else "_Join the channel, then tap Check._"),
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

    elif sub == "check":
        tid = int(parts[2])
        t = get_task(tid)
        if not t or t[4] == 0:
            await q.answer("Task not available.", show_alert=True); return
        _, ch, reward, title, active = t
        if has_completed(u.id, tid):
            await q.answer("Already claimed.", show_alert=True); return
        if not await is_member(ctx, u.id, ch):
            await q.answer("❌ You haven't joined the channel yet.", show_alert=True); return
        add_user(u.id, stars=reward)
        mark_completed(u.id, tid)
        _,_,new_stars,_,_,_,_,_,_ = get_user(u.id, u.first_name)
        await q.answer(f"✅ +{reward} stars!")
        await q.edit_message_text(
            f"✅ *Task Complete!*\n\n{safe(title)}\n+{reward} ⭐\n\nBalance: *{new_stars}* ⭐",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Tasks", callback_data="t:list")]]))


# ---------------- Admin panel ----------------
async def admin_router(update, ctx):
    q = update.callback_query; await q.answer()
    u = q.from_user
    if not is_admin(u.id):
        await q.answer("Admins only.", show_alert=True); return
    parts = q.data.split(":")
    sub = parts[1] if len(parts) > 1 else "panel"

    if sub == "panel":
        active, completions = task_stats()
        await q.edit_message_text(
            f"⚙️ *Admin Panel*\n\n"
            f"Active tasks: *{active}*\n"
            f"Total completions: *{completions}*\n\n"
            f"Use the buttons below. Rewards capped at *{TASK_REWARD_CAP}* ⭐.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Create Task", callback_data="a:new")],
                [InlineKeyboardButton("📋 Active Tasks", callback_data="a:list")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="m:home")]]))

    elif sub == "list":
        tasks = list_tasks(active_only=True)
        if not tasks:
            await q.edit_message_text("No active tasks.", reply_markup=admin_back()); return
        lines = ["⚙️ *Active Tasks*\n"]
        for tid, ch, reward, title in tasks:
            lines.append(f"`#{tid}` — {safe(title)} → {ch} ({reward}⭐)")
        await q.edit_message_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=admin_back())

    elif sub == "new":
        ADMIN_FLOW[u.id] = {"step": "channel", "data": {}}
        await q.edit_message_text(
            "➕ *Create Task — Step 1 of 3*\n\n"
            "Send me the channel username in the chat.\n\n"
            "Example: `@NCK_Dev`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Cancel", callback_data="a:panel")]]))

    elif sub == "del":
        tid = int(parts[2])
        delete_task(tid)
        log_admin(u.id, "deltask", f"#{tid}")
        await q.answer(f"✅ Task #{tid} deactivated.")
        tasks = list_tasks(active_only=True)
        if not tasks:
            await q.edit_message_text("No active tasks.", reply_markup=admin_back()); return
        lines = ["⚙️ *Active Tasks*\n"]
        for t in tasks:
            lines.append(f"`#{t[0]}` — {safe(t[3])} → {t[1]} ({t[2]}⭐)")
        await q.edit_message_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=admin_back())


async def admin_message(update, ctx):
    u = update.effective_user
    if not is_admin(u.id):
        return
    flow = ADMIN_FLOW.get(u.id)
    if not flow:
        return
    text = (update.message.text or "").strip()
    step = flow["step"]

    if step == "channel":
        if not text.startswith("@"):
            text = "@" + text
        flow["data"]["channel"] = text
        flow["step"] = "reward"
        flow["data"]["reward_str"] = ""
        await update.message.reply_text(
            f"✅ Channel: `{text}`\n\n"
            f"➕ *Step 2 of 3*\n\n"
            f"Tap the keypad to enter the reward (1–{TASK_REWARD_CAP}).\n"
            f"Then tap ✅ Done.",
            parse_mode="Markdown",
            reply_markup=reward_keypad_kb(""))

    elif step == "title":
        flow["data"]["title"] = text[:64]
        d = flow["data"]
        tid = create_task(d["channel"], d["reward"], d["title"], u.id)
        log_admin(u.id, "newtask", f"#{tid} {d['channel']} {d['reward']} {d['title']}")
        ADMIN_FLOW.pop(u.id, None)
        await update.message.reply_text(
            f"✅ *Task Created*\n\n"
            f"ID: `#{tid}`\nChannel: {d['channel']}\n"
            f"Reward: *{d['reward']}* ⭐\nTitle: {safe(d['title'])}",
            parse_mode="Markdown",
            reply_markup=admin_back())


async def keypad_router(update, ctx):
    q = update.callback_query; await q.answer()
    u = q.from_user
    if not is_admin(u.id):
        await q.answer("Admins only.", show_alert=True); return
    flow = ADMIN_FLOW.get(u.id)
    if not flow or flow["step"] != "reward":
        await q.answer("Session expired.", show_alert=True); return

    data = q.data.split(":")
    action = data[1] if len(data) > 1 else "noop"
    cur = flow["data"].get("reward_str", "")

    if action == "noop":
        return
    elif action == "d":
        digit = data[2]
        if len(cur) < 4:
            cur = (cur + digit).lstrip("0") or "0"
            flow["data"]["reward_str"] = cur
            await q.edit_message_reply_markup(reply_markup=reward_keypad_kb(cur))
    elif action == "back":
        cur = cur[:-1]
        flow["data"]["reward_str"] = cur
        await q.edit_message_reply_markup(reply_markup=reward_keypad_kb(cur))
    elif action == "done":
        if not cur or cur == "0":
            await q.answer("Enter a value first.", show_alert=True); return
        val = int(cur)
        if val < 1 or val > TASK_REWARD_CAP:
            await q.answer(f"Must be 1–{TASK_REWARD_CAP}.", show_alert=True); return
        flow["data"]["reward"] = val
        flow["step"] = "title"
        await q.edit_message_text(
            f"✅ Channel: `{flow['data']['channel']}`\n"
            f"✅ Reward: *{val}* ⭐\n\n"
            f"➕ *Step 3 of 3*\n\n"
            f"Send the task title as text in the chat.\n"
            f"Example: `Join NCK DEV`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Cancel", callback_data="a:panel")]]))


# ---------------- Queue & matchmaking ----------------
async def queue_ticker(ctx, chat_id, msg_id, mode, amount, uid):
    remaining = QUEUE_WAIT
    while remaining > 0:
        await asyncio.sleep(1)
        remaining -= 1
        still_queued = any(p["uid"] == uid for p in QUEUES[mode])
        if not still_queued:
            return
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=(f"🔍 *Searching for {mode.capitalize()}...*\n\n"
                      f"⏱ *{remaining}s* remaining\n\n"
                      f"Stake: " + (f"*{amount}* ⭐" if amount else "Friendly 🎈") + "\n\n"
                      f"_If no real player joins, a bot will fill the seat._"),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data="cq:cancel")]]),
            )
        except TelegramError:
            return


async def queue_router(update, ctx):
    q = update.callback_query; await q.answer()
    u = q.from_user
    parts = q.data.split(":")

    if parts[0] == "q" and len(parts) == 2:
        mode = parts[1]
        _,_,_,e,_,_,_,_,_ = get_user(u.id, u.first_name)
        lv = level_from_exp(e)
        await q.edit_message_text(
            f"🎯 *{mode.capitalize()}* — pick your stake:",
            parse_mode="Markdown", reply_markup=stake_kb(mode, lv))
        return

    mode = parts[1]; amount = int(parts[2])
    _,_,stars,e,_,_,_,_,_ = get_user(u.id, u.first_name)
    if amount > 0 and stars < amount:
        await q.edit_message_text(f"❌ Not enough stars. You have *{stars}*.",
                                  parse_mode="Markdown", reply_markup=back()); return
    for m in QUEUES:
        QUEUES[m] = [p for p in QUEUES[m] if p["uid"] != u.id]
    QUEUES[mode].append({
        "uid": u.id, "name": u.first_name, "exp": e,
        "amount": amount, "joined": datetime.utcnow(),
    })

    await q.edit_message_text(
        f"🔍 *Searching for {mode.capitalize()}...*\n\n"
        f"⏱ *{QUEUE_WAIT}s* remaining\n\n"
        f"Stake: " + (f"*{amount}* ⭐" if amount else "Friendly 🎈") + "\n\n"
        f"_If no real player joins, a bot will fill the seat._",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="cq:cancel")]]))

    asyncio.create_task(queue_ticker(
        ctx, q.message.chat_id, q.message.message_id,
        mode, amount, u.id))

    ctx.application.job_queue.run_once(
        try_match_job, QUEUE_WAIT, data={"mode": mode}, name=f"q_{mode}_{u.id}")
    await try_match(ctx, mode=mode)


async def cancel_queue(update, ctx):
    q = update.callback_query; await q.answer()
    u = q.from_user
    for m in QUEUES:
        QUEUES[m] = [p for p in QUEUES[m] if p["uid"] != u.id]
    await q.edit_message_text("✅ Search cancelled.", reply_markup=back())


async def try_match_job(ctx):
    mode = ctx.job.data["mode"]
    await try_match(ctx, mode=mode)
    await force_fill_queue(ctx, mode)


async def force_fill_queue(ctx, mode):
    size = {"solo": 2, "duo": 4, "squad": 6}[mode]
    now = datetime.utcnow()
    while True:
        waiting = [p for p in QUEUES[mode]
                   if (now - p["joined"]).total_seconds() >= QUEUE_WAIT - 1]
        if not waiting:
            break
        host = waiting[0]
        host_lv = level_from_exp(host["exp"])
        pool = [p for p in QUEUES[mode]
                if abs(level_from_exp(p["exp"]) - host_lv) <= 3
                and p["amount"] == host["amount"]]
        pool = pool[:size]
        for p in pool:
            if p in QUEUES[mode]:
                QUEUES[mode].remove(p)
        while len(pool) < size:
            pool.append(make_bot(host["amount"], host_lv))
        await start_match(ctx, mode, pool)


async def try_match(ctx, mode=None):
    modes = [mode] if mode else list(QUEUES.keys())
    for m in modes:
        size = {"solo":2, "duo":4, "squad":6}[m]
        QUEUES[m].sort(key=lambda p: p["joined"])
        while True:
            if len(QUEUES[m]) < 2: break
            host = QUEUES[m][0]
            host_lv = level_from_exp(host["exp"])
            pool = [p for p in QUEUES[m]
                    if abs(level_from_exp(p["exp"]) - host_lv) <= 3
                    and p["amount"] == host["amount"]]
            pool = pool[:size]
            if len(pool) < 2: break
            for p in pool:
                QUEUES[m].remove(p)
            while len(pool) < size:
                pool.append(make_bot(host["amount"], host_lv))
            await start_match(ctx, m, pool)


def make_bot(amount, host_lv):
    lv = max(1, min(MAX_LEVEL, host_lv + random.randint(-2, 2)))
    exp = exp_for_level(lv)
    return {
        "uid": -random.randint(100000, 999999),
        "name": random_bot_name(),
        "exp": exp, "amount": amount,
        "joined": datetime.utcnow(), "is_bot": True,
    }


async def start_match(ctx, mode, players):
    random.shuffle(players)
    half = len(players) // 2
    team_a, team_b = players[:half], players[half:]
    amount = players[0]["amount"]
    gid = new_gid()
    hands = {p["uid"]: new_hand() for p in players}
    is_bot = {p["uid"]: p.get("is_bot", False) for p in players}
    GAMES[gid] = {
        "mode": mode, "amount": amount,
        "teams": {0: team_a, 1: team_b},
        "hands": hands, "is_bot": is_bot,
        "picks": {}, "team_rounds": {0: 0, 1: 0}, "round": 1,
    }
    for p in players:
        if p.get("is_bot"): continue
        my_team = 0 if p in team_a else 1
        my_ids = [x["uid"] for x in (team_a if my_team == 0 else team_b)]
        mates, opps = [], []
        for o in players:
            line = name_full(o["name"], o["exp"], is_bot=o.get("is_bot", False))
            if o["uid"] in my_ids:
                if o["uid"] != p["uid"]: mates.append(f"• {line}")
            else:
                opps.append(f"• {line}")
        team_hint = ""
        if mode != "solo" and mates:
            team_hint = ("\n👥 *Your teammates:*\n" + "\n".join(mates) +
                         "\n_Team sum wins each round. Coordinate!_\n")
        try:
            await ctx.bot.send_message(
                chat_id=p["uid"],
                text=(f"🎮 *Match Found!*\n\nMode: *{mode.capitalize()}*\n"
                      f"Stake: " + (f"*{amount}* ⭐" if amount else "Friendly 🎈") + "\n"
                      f"{team_hint}\n🎯 *Opponents:*\n" + "\n".join(opps) +
                      "\n\nRound 1 starting now..."),
                parse_mode="Markdown")
        except Exception as ex:
            logging.error(f"notify failed {p['uid']}: {ex}")
    await send_round(ctx, gid)


async def send_round(ctx, gid):
    g = GAMES.get(gid)
    if not g: return
    r = g["round"]
    for uid, hand in g["hands"].items():
        if g["is_bot"].get(uid): continue
        try:
            await ctx.bot.send_message(
                chat_id=uid,
                text=(f"🎴 *Round {r}*\n\nTap a card to play it face-down.\n"
                      "_Opponent sees nothing until both have played._"),
                parse_mode="Markdown",
                reply_markup=card_kb(gid, hand, r))
        except Exception as e:
            logging.error(f"round send failed {uid}: {e}")
    ctx.application.job_queue.run_once(
        round_timeout, ROUND_TIMEOUT,
        data={"gid": gid, "round": r}, name=f"rt_{gid}_{r}")
    await asyncio.sleep(2)
    await bot_pick(ctx, gid, r)


async def bot_pick(ctx, gid, rnd):
    g = GAMES.get(gid)
    if not g or g["round"] != rnd: return
    for uid, hand in list(g["hands"].items()):
        if not g["is_bot"].get(uid): continue
        if uid in g["picks"].get(rnd, {}): continue
        if not hand: continue
        team = 0 if any(p["uid"] == uid for p in g["teams"][0]) else 1
        my_score = g["team_rounds"][team]
        opp_score = g["team_rounds"][1 - team]
        if rnd == 3 or opp_score == 1:
            card = max(hand)
        elif my_score == 1 and rnd == 2:
            card = min(hand)
        else:
            r = random.random()
            card = max(hand) if r < 0.5 else (min(hand) if r < 0.8 else random.choice(hand))
        g["picks"].setdefault(rnd, {})[uid] = card
        g["hands"][uid].remove(card)
    await check_round_complete(ctx, gid, rnd)


async def card_play(update, ctx):
    q = update.callback_query
    if q.data == "c:noop":
        await q.answer("Not valid.", show_alert=True); return
    _, gid, rnd, n = q.data.split(":")
    gid = int(gid); rnd = int(rnd); n = int(n)
    u = q.from_user
    g = GAMES.get(gid)
    if not g: await q.answer("Match ended.", show_alert=True); return
    if u.id not in g["hands"]: await q.answer("Not yours.", show_alert=True); return
    if n not in g["hands"][u.id]:
        await q.answer("Card already used.", show_alert=True); return
    if u.id in g["picks"].get(rnd, {}):
        await q.answer("Already played.", show_alert=True); return
    g["picks"].setdefault(rnd, {})[u.id] = n
    g["hands"][u.id].remove(n)
    await q.answer("Locked in ✅")
    try:
        await q.edit_message_text(
            f"🎴 *Round {rnd}*\n\nYou played *{n}* ✅\n\n_Waiting..._",
            parse_mode="Markdown")
    except Exception:
        pass
    await check_round_complete(ctx, gid, rnd)


async def round_timeout(ctx):
    gid = ctx.job.data["gid"]; rnd = ctx.job.data["round"]
    g = GAMES.get(gid)
    if not g or g["round"] != rnd: return
    for uid, hand in list(g["hands"].items()):
        if uid in g["picks"].get(rnd, {}): continue
        if not hand: continue
        lowest = min(hand)
        g["picks"].setdefault(rnd, {})[uid] = lowest
        g["hands"][uid].remove(lowest)
        if not g["is_bot"].get(uid):
            try:
                await ctx.bot.send_message(
                    chat_id=uid,
                    text=f"⏰ Timeout — auto-played *{lowest}*.",
                    parse_mode="Markdown")
            except Exception: pass
    await check_round_complete(ctx, gid, rnd, force=True)


async def check_round_complete(ctx, gid, rnd, force=False):
    g = GAMES.get(gid)
    if not g or g["round"] != rnd: return
    picks = g["picks"].get(rnd, {})
    expected = len(g["hands"])
    if not force and len(picks) < expected: return

    a_ids = [p["uid"] for p in g["teams"][0]]
    b_ids = [p["uid"] for p in g["teams"][1]]
    a_sum = sum(picks.get(uid, 0) for uid in a_ids)
    b_sum = sum(picks.get(uid, 0) for uid in b_ids)

    if a_sum > b_sum:
        g["team_rounds"][0] += 1; winner_txt = "🔵 *Team A wins the round!*"
    elif b_sum > a_sum:
        g["team_rounds"][1] += 1; winner_txt = "🔴 *Team B wins the round!*"
    else:
        winner_txt = "🤝 *Tie round!*"

    for p in g["teams"][0] + g["teams"][1]:
        if g["is_bot"].get(p["uid"]): continue
        try:
            await ctx.bot.send_message(
                chat_id=p["uid"],
                text=(f"🎴 *Round {rnd} Result*\n\n"
                      f"🔵 Team A: *{a_sum}*\n🔴 Team B: *{b_sum}*\n\n"
                      f"{winner_txt}\n"
                      f"Rounds: *{g['team_rounds'][0]} — {g['team_rounds'][1]}*"),
                parse_mode="Markdown")
        except Exception as e:
            logging.error(f"result send failed {p['uid']}: {e}")

    if g["team_rounds"][0] == 2 or g["team_rounds"][1] == 2 or rnd == 3:
        await finish_match(ctx, gid); return
    g["round"] += 1
    await send_round(ctx, gid)


async def finish_match(ctx, gid):
    g = GAMES.pop(gid, None)
    if not g: return
    ra = g["team_rounds"][0]; rb = g["team_rounds"][1]
    amount = g["amount"]
    a_ids = [p["uid"] for p in g["teams"][0]]
    b_ids = [p["uid"] for p in g["teams"][1]]
    win_ids = a_ids if ra > rb else (b_ids if rb > ra else [])
    lose_ids = b_ids if ra > rb else (a_ids if rb > ra else [])

    if g["mode"] == "solo": win_exp, lose_exp = 25, 10
    elif g["mode"] == "duo": win_exp, lose_exp = 30, 12
    else: win_exp, lose_exp = 35, 15

    if not win_ids and not lose_ids:
        for uid in a_ids + b_ids:
            if g["is_bot"].get(uid): continue
            add_user(uid, exp=10)
        footer = "\n_+10 EXP each (draw)_"
        result = "🤝 *Draw!* No stars moved."
    else:
        for uid in win_ids:
            if g["is_bot"].get(uid):
                if amount > 0: house_add(-amount * len(lose_ids))
                continue
            add_user(uid, stars=(amount * len(lose_ids) if amount > 0 else 0),
                     exp=win_exp, wins=1)
        for uid in lose_ids:
            if g["is_bot"].get(uid):
                if amount > 0: house_add(amount * len(win_ids))
                continue
            add_user(uid, stars=(-amount * len(win_ids) if amount > 0 else 0),
                     exp=lose_exp, losses=1)
        team_name = "🔵 Team A" if ra > rb else "🔴 Team B"
        result = f"🏆 *{team_name} wins!*"
        footer = (f"\n_{amount}⭐ moved · +{win_exp} EXP winners / "
                  f"+{lose_exp} EXP losers_") if amount > 0 else \
                 f"\n_Friendly · +{win_exp} / +{lose_exp} EXP_"

    for p in g["teams"][0] + g["teams"][1]:
        if g["is_bot"].get(p["uid"]): continue
        try:
            await ctx.bot.send_message(
                chat_id=p["uid"],
                text=(f"🏁 *Match Over*\n\n🔵 Team A: *{ra}*\n🔴 Team B: *{rb}*\n\n"
                      f"{result}{footer}"),
                parse_mode="Markdown", reply_markup=main_menu(p["uid"]))
        except Exception as e:
            logging.error(f"finish send failed {p['uid']}: {e}")


async def forfeit_match(ctx, gid, leaver_id, reason="left"):
    g = GAMES.pop(gid, None)
    if not g: return
    amount = g["amount"]
    a_ids = [p["uid"] for p in g["teams"][0]]
    b_ids = [p["uid"] for p in g["teams"][1]]
    if leaver_id in a_ids:
        winner_ids, loser_ids = b_ids, a_ids
    else:
        winner_ids, loser_ids = a_ids, b_ids

    if g["mode"] == "solo": win_exp, lose_exp = 25, 10
    elif g["mode"] == "duo": win_exp, lose_exp = 30, 12
    else: win_exp, lose_exp = 35, 15

    for uid in winner_ids:
        if g["is_bot"].get(uid):
            if amount > 0: house_add(-amount * len(loser_ids))
            continue
        add_user(uid, stars=(amount * len(loser_ids) if amount > 0 else 0),
                 exp=win_exp, wins=1)
    for uid in loser_ids:
        if g["is_bot"].get(uid):
            if amount > 0: house_add(amount * len(winner_ids))
            continue
        add_user(uid, stars=(-amount * len(winner_ids) if amount > 0 else 0),
                 exp=lose_exp, losses=1)

    for p in g["teams"][0] + g["teams"][1]:
        if g["is_bot"].get(p["uid"]): continue
        try:
            msg = "🚪 *Match ended early*" if p["uid"] == leaver_id else "🚪 *Opponent left*"
            await ctx.bot.send_message(
                chat_id=p["uid"],
                text=(f"{msg}\n\nReason: {reason}\n"
                      + (f"_{amount}⭐ moved_" if amount > 0 else "_Friendly_")),
                parse_mode="Markdown", reply_markup=main_menu(p["uid"]))
        except Exception as e:
            logging.error(f"forfeit notify failed {p['uid']}: {e}")


# ---------------- main ----------------
def main():
    start_health_server()
    start_keepalive_loop(interval=600)
    init_db()
    gh_backup_loop(interval=300, debounce=BACKUP_DEBOUNCE)

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_match))
    app.add_handler(CallbackQueryHandler(on_join_check, pattern=r"^fj:check$"))
    app.add_handler(CallbackQueryHandler(menu, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(play_router, pattern=r"^p:"))
    app.add_handler(CallbackQueryHandler(help_router, pattern=r"^h:"))
    app.add_handler(CallbackQueryHandler(queue_router, pattern=r"^q"))
    app.add_handler(CallbackQueryHandler(cancel_queue, pattern=r"^cq:"))
    app.add_handler(CallbackQueryHandler(card_play, pattern=r"^c:"))
    app.add_handler(CallbackQueryHandler(tasks_router, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(admin_router, pattern=r"^a:"))
    app.add_handler(CallbackQueryHandler(keypad_router, pattern=r"^k:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_message))

    print("Star Cards running...")
    app.run_polling()


if __name__ == "__main__":
    main()