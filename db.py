"""db.py — Database connection, init, and all CRUD helpers."""
from __future__ import annotations
import json, os, sqlite3, uuid, datetime
from contextlib import contextmanager
from pathlib import Path

DB_PATH    = Path("data/saas.db")
OUTPUT_ROOT= Path("outputs")
PLAN_QUOTAS= {"free": 10, "starter": 50, "pro": 200, "agency": 1000}
PLAN_PRICES= {"free": "$0", "starter": "$19", "pro": "$49", "agency": "$149"}
PLATFORM_CHOICES = ["Instagram", "TikTok", "LinkedIn", "Twitter/X", "Facebook"]
LANGUAGE_CHOICES = ["English", "Arabic", "Egyptian Arabic", "Gulf Arabic",
                    "French", "Spanish", "German"]

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

import logging
logger = logging.getLogger("SignalMind.db")


# ── Helpers ───────────────────────────────────────────────────
def now_iso():      return datetime.datetime.utcnow().isoformat()
def current_month():return datetime.datetime.utcnow().strftime("%Y-%m")

def safe_json_loads(raw, default=None):
    if not raw: return default
    if isinstance(raw, (dict, list)): return raw
    try:    return json.loads(raw)
    except: return default

def safe_json_dumps(obj):
    try:    return json.dumps(obj, ensure_ascii=False)
    except: return "{}"


# ── Connection ────────────────────────────────────────────────
@contextmanager
def get_conn():
    """Fresh connection per call — always reads latest WAL data."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-16000")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=134217728")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema ────────────────────────────────────────────────────
def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            password_hash TEXT NOT NULL, plan TEXT DEFAULT 'free',
            is_active INTEGER DEFAULT 1, created_at TEXT NOT NULL, last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS generations (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, topic TEXT NOT NULL,
            content_type TEXT NOT NULL, platforms TEXT NOT NULL, language TEXT NOT NULL,
            status TEXT DEFAULT 'pending', result_json TEXT, error TEXT, config_json TEXT,
            created_at TEXT NOT NULL, completed_at TEXT, scheduled_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS usage (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, gen_id TEXT NOT NULL,
            month TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS brands (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
            profile_json TEXT NOT NULL DEFAULT '{}', is_default INTEGER DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS strategies (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, brand_id TEXT, title TEXT NOT NULL,
            topic TEXT NOT NULL, duration_days INTEGER DEFAULT 30, status TEXT DEFAULT 'draft',
            plan_json TEXT, created_at TEXT NOT NULL, approved_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS calendar_items (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, strategy_id TEXT, generation_id TEXT,
            brand_id TEXT, title TEXT NOT NULL, platform TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'static', publish_date TEXT NOT NULL,
            publish_time TEXT DEFAULT '09:00', status TEXT DEFAULT 'scheduled',
            notes TEXT, idea_json TEXT, created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT PRIMARY KEY,
            gemini_key TEXT NOT NULL DEFAULT '',
            openrouter_key TEXT NOT NULL DEFAULT '',
            aiml_key TEXT NOT NULL DEFAULT '',
            llm_provider TEXT NOT NULL DEFAULT 'google',
            llm_model TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
            image_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-image-preview',
            video_model TEXT NOT NULL DEFAULT 'google/veo-3.1-i2v',
            updated_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_gen_user     ON generations(user_id);
        CREATE INDEX IF NOT EXISTS idx_usage_user   ON usage(user_id, month);
        CREATE INDEX IF NOT EXISTS idx_brands_user  ON brands(user_id);
        CREATE INDEX IF NOT EXISTS idx_strat_user   ON strategies(user_id);
        CREATE INDEX IF NOT EXISTS idx_cal_user     ON calendar_items(user_id, publish_date);
        CREATE INDEX IF NOT EXISTS idx_settings_user ON user_settings(user_id);
        """)
    migrations = [
        "ALTER TABLE users ADD COLUMN brand_profile TEXT DEFAULT '{}'",
        "ALTER TABLE generations ADD COLUMN scheduled_at TEXT",
        "ALTER TABLE calendar_items ADD COLUMN publish_time TEXT DEFAULT '09:00'",
    ]
    for m in migrations:
        with get_conn() as conn:
            try: conn.execute(m)
            except Exception: pass
    logger.info("Database ready: %s", DB_PATH)


# ── User CRUD ─────────────────────────────────────────────────
def get_user_by_email(email):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=? AND is_active=1",
                           (email.lower().strip(),)).fetchone()
    return dict(row) if row else None

def create_user(email, name, password):
    from auth import hash_password
    uid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id,email,name,password_hash,created_at) VALUES (?,?,?,?,?)",
                     (uid, email.lower().strip(), name.strip(), hash_password(password), now_iso()))
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row)

def update_last_login(uid):
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (now_iso(), uid))

def get_brand_profile(uid):
    with get_conn() as conn:
        row = conn.execute("SELECT brand_profile FROM users WHERE id=?", (uid,)).fetchone()
    if not row: return {}
    return safe_json_loads(row["brand_profile"], {})

def save_brand_profile(uid, profile):
    with get_conn() as conn:
        conn.execute("UPDATE users SET brand_profile=? WHERE id=?",
                     (json.dumps(profile, ensure_ascii=False), uid))


# ── User Settings ─────────────────────────────────────────────
_SETTINGS_DEFAULTS = {
    "gemini_key": "", "openrouter_key": "", "aiml_key": "",
    "llm_provider": "google", "llm_model": "gemini-2.5-flash",
    "image_model": "gemini-3.1-flash-image-preview",
    "video_model": "google/veo-3.1-i2v",
}

def get_user_settings(uid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM user_settings WHERE user_id=?", (uid,)).fetchone()
    return dict(row) if row else dict(_SETTINGS_DEFAULTS)

def save_user_settings(uid, s):
    with get_conn() as conn:
        conn.execute("""INSERT INTO user_settings
               (user_id,gemini_key,openrouter_key,aiml_key,
                llm_provider,llm_model,image_model,video_model,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET
               gemini_key=excluded.gemini_key,
               openrouter_key=excluded.openrouter_key,
               aiml_key=excluded.aiml_key,
               llm_provider=excluded.llm_provider,
               llm_model=excluded.llm_model,
               image_model=excluded.image_model,
               video_model=excluded.video_model,
               updated_at=excluded.updated_at""",
            (uid, s.get("gemini_key",""), s.get("openrouter_key",""),
             s.get("aiml_key",""), s.get("llm_provider","google"),
             s.get("llm_model","gemini-2.5-flash"),
             s.get("image_model","gemini-3.1-flash-image-preview"),
             s.get("video_model","google/veo-3.1-i2v"), now_iso()))


# ── Brand CRUD ────────────────────────────────────────────────
def _parse_brand(row):
    d = dict(row)
    d["profile"] = safe_json_loads(d.get("profile_json"), {})
    return d

def get_brands(uid):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM brands WHERE user_id=? ORDER BY is_default DESC, created_at ASC",
                            (uid,)).fetchall()
    return [_parse_brand(r) for r in rows]

def get_brand(brand_id, uid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM brands WHERE id=? AND user_id=?",
                           (brand_id, uid)).fetchone()
    return _parse_brand(row) if row else None

def get_default_brand(uid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM brands WHERE user_id=? AND is_default=1 LIMIT 1",
                           (uid,)).fetchone()
    if not row:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM brands WHERE user_id=? ORDER BY created_at ASC LIMIT 1",
                               (uid,)).fetchone()
    return _parse_brand(row) if row else None

def create_brand(uid, name, profile):
    bid = str(uuid.uuid4())
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM brands WHERE user_id=?", (uid,)).fetchone()[0]
        conn.execute("INSERT INTO brands (id,user_id,name,profile_json,is_default,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (bid, uid, name, json.dumps(profile, ensure_ascii=False),
                      1 if count == 0 else 0, now_iso(), now_iso()))
    return bid

def update_brand(brand_id, uid, name, profile):
    with get_conn() as conn:
        conn.execute("UPDATE brands SET name=?,profile_json=?,updated_at=? WHERE id=? AND user_id=?",
                     (name, json.dumps(profile, ensure_ascii=False), now_iso(), brand_id, uid))

def delete_brand(brand_id, uid):
    with get_conn() as conn:
        conn.execute("DELETE FROM brands WHERE id=? AND user_id=?", (brand_id, uid))

def set_default_brand(brand_id, uid):
    with get_conn() as conn:
        conn.execute("UPDATE brands SET is_default=0 WHERE user_id=?", (uid,))
        conn.execute("UPDATE brands SET is_default=1 WHERE id=? AND user_id=?", (brand_id, uid))


# ── Strategy CRUD ─────────────────────────────────────────────
def create_strategy(uid, brand_id, title, topic, duration_days=30):
    sid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute("INSERT INTO strategies (id,user_id,brand_id,title,topic,duration_days,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (sid, uid, brand_id, title, topic, duration_days, "generating", now_iso()))
    return sid

def update_strategy(sid, status, plan=None):
    with get_conn() as conn:
        conn.execute("UPDATE strategies SET status=?,plan_json=?,approved_at=? WHERE id=?",
                     (status, json.dumps(plan) if plan else None,
                      now_iso() if status == "approved" else None, sid))

def get_strategy(sid, uid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM strategies WHERE id=? AND user_id=?", (sid, uid)).fetchone()
    if not row: return None
    d = dict(row)
    if d.get("plan_json"):
        d["plan"] = safe_json_loads(d["plan_json"], {})
    return d

def get_user_strategies(uid, limit=20):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM strategies WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                            (uid, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("plan_json"):
            d["plan"] = safe_json_loads(d["plan_json"], {})
        out.append(d)
    return out


# ── Calendar CRUD ─────────────────────────────────────────────
def add_calendar_items(uid, items):
    if not items: return []
    ids = [str(uuid.uuid4()) for _ in items]
    ts  = now_iso()
    rows = [
        (ids[i], uid, item.get("strategy_id"), item.get("generation_id"),
         item.get("brand_id"), item.get("title",""), item.get("platform",""),
         item.get("content_type","static"), item.get("publish_date",""),
         item.get("publish_time","09:00"), item.get("status","scheduled"),
         item.get("notes",""), json.dumps(item.get("idea",{})), ts)
        for i, item in enumerate(items)
    ]
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO calendar_items "
            "(id,user_id,strategy_id,generation_id,brand_id,title,platform,"
            "content_type,publish_date,publish_time,status,notes,idea_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return ids

def get_calendar_item(cid, uid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM calendar_items WHERE id=? AND user_id=?",
                           (cid, uid)).fetchone()
    if not row: return None
    d = dict(row)
    d["idea"] = safe_json_loads(d.get("idea_json"), {})
    return d

def get_calendar_items(uid, year, month):
    prefix = f"{year}-{month:02d}"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM calendar_items WHERE user_id=? AND publish_date LIKE ? ORDER BY publish_date ASC",
            (uid, f"{prefix}%")).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["idea"] = safe_json_loads(d.get("idea_json"), {})
        out.append(d)
    return out

def update_calendar_item_status(cid, uid, status):
    with get_conn() as conn:
        conn.execute("UPDATE calendar_items SET status=? WHERE id=? AND user_id=?",
                     (status, cid, uid))

def delete_calendar_item(cid, uid):
    with get_conn() as conn:
        conn.execute("DELETE FROM calendar_items WHERE id=? AND user_id=?", (cid, uid))


# ── Quota & Usage ─────────────────────────────────────────────
def get_usage_this_month(uid):
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM usage WHERE user_id=? AND month=?",
                           (uid, current_month())).fetchone()
    return row["cnt"] if row else 0

def record_usage(uid, gid):
    with get_conn() as conn:
        conn.execute("INSERT INTO usage (id,user_id,gen_id,month,created_at) VALUES (?,?,?,?,?)",
                     (str(uuid.uuid4()), uid, gid, current_month(), now_iso()))

def quota_ok(user):
    return get_usage_this_month(user["id"]) < PLAN_QUOTAS.get(user.get("plan","free"), 10)

def quota_status(user):
    plan  = user.get("plan","free")
    limit = PLAN_QUOTAS.get(plan, 10)
    used  = get_usage_this_month(user["id"])
    return {"used": used, "limit": limit, "plan": plan, "remaining": max(0, limit - used)}


# ── Generation CRUD ───────────────────────────────────────────
NICHE_KW = {
    "tech":    ["ai","ml","llm","software","dev","code","saas","cloud"],
    "fashion": ["fashion","beauty","style","makeup","skincare"],
    "fitness": ["fitness","workout","gym","sport","nutrition"],
    "food":    ["food","recipe","meal","cook","restaurant","chef"],
    "finance": ["finance","invest","crypto","stock","money","fintech"],
    "health":  ["health","medical","wellness","doctor","pharma"],
}

def detect_niche(topic):
    t = topic.lower()
    for niche, kws in NICHE_KW.items():
        if any(k in t for k in kws): return niche
    return "marketing"

def _sanitise_for_json(obj):
    """Convert result dict to JSON-safe Python natives. Strips raw_ranked."""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(obj) and not isinstance(obj, type):
        return _sanitise_for_json(asdict(obj))
    if isinstance(obj, dict):
        return {k: _sanitise_for_json(v) for k, v in obj.items() if k != "raw_ranked"}
    if isinstance(obj, (list, tuple)):
        return [_sanitise_for_json(i) for i in obj]
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    try:
        import numpy as _np
        if isinstance(obj, _np.integer):  return int(obj)
        if isinstance(obj, _np.floating): return float(obj)
        if isinstance(obj, _np.bool_):    return bool(obj)
        if isinstance(obj, _np.ndarray):  return obj.tolist()
    except ImportError:
        pass
    if not isinstance(obj, (str, int, float, bool, type(None))):
        try:    json.dumps(obj)
        except: return str(obj)
    return obj

def create_generation(uid, topic, content_type, platforms, language, config=None):
    gid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO generations (id,user_id,topic,content_type,platforms,language,status,config_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (gid, uid, topic, content_type, json.dumps(platforms), language,
             "pending", json.dumps(config or {}), now_iso()))
    return gid

def create_scheduled_generation(uid, topic, content_type, platforms, language,
                                 scheduled_at, cfg=None):
    gid = str(uuid.uuid4())
    cfg = dict(cfg or {})
    cfg["scheduled_at"] = scheduled_at
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO generations "
            "(id,user_id,topic,content_type,platforms,language,status,config_json,scheduled_at,created_at) "
            "VALUES (?,?,?,?,?,?,'scheduled',?,?,?)",
            (gid, uid, topic, content_type, json.dumps(platforms), language,
             json.dumps(cfg, ensure_ascii=False), scheduled_at, now_iso()))
    return gid

def update_generation(gid, status, result=None, error=None):
    if result is not None:
        try:
            result = _sanitise_for_json(result)
            json.dumps(result)
        except Exception as e:
            logger.warning("update_generation: not serialisable: %s", e)
            error = error or str(e); result = None; status = "failed"
    with get_conn() as conn:
        conn.execute(
            "UPDATE generations SET status=?,result_json=?,error=?,completed_at=? WHERE id=?",
            (status, json.dumps(result) if result else None, error,
             now_iso() if status in ("completed","failed","awaiting_approval") else None, gid))

def get_generation(gid, uid):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM generations WHERE id=? AND user_id=?",
                           (gid, uid)).fetchone()
    if not row: return None
    d = dict(row)
    d["platforms"] = safe_json_loads(d.get("platforms"), [])
    d["config"]    = safe_json_loads(d.get("config_json"), {})
    if d.get("result_json"):
        d["result"] = safe_json_loads(d["result_json"], {})
    return d

def get_user_generations(uid, limit=100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,topic,content_type,platforms,language,status,created_at,completed_at,scheduled_at "
            "FROM generations WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (uid, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["platforms"] = safe_json_loads(d.get("platforms"), [])
        out.append(d)
    return out

def cancel_scheduled_generation(gid, uid):
    with get_conn() as conn:
        conn.execute(
            "UPDATE generations SET status='cancelled' "
            "WHERE id=? AND user_id=? AND status='scheduled'", (gid, uid))

def get_scheduled_generations(uid, strategy_id=None):
    with get_conn() as conn:
        if strategy_id:
            rows = conn.execute(
                "SELECT * FROM generations WHERE user_id=? AND status='scheduled' "
                "AND json_extract(config_json,'$.strategy_id')=? ORDER BY scheduled_at ASC",
                (uid, strategy_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM generations WHERE user_id=? AND status='scheduled' "
                "ORDER BY scheduled_at ASC", (uid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["config"] = safe_json_loads(d.get("config_json"), {})
        out.append(d)
    return out
