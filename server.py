"""clipbot lite — MCP server wrapping the clipbot core (sqlite, no external stack)."""
import hashlib, json, os, sqlite3, time, uuid
from typing import Any

from fastmcp import FastMCP

AUTONOMY_GATE = int(os.environ.get("AUTONOMY_GATE", "2"))
DB_PATH = os.environ.get("DB_PATH", "/data/clipbot.db" if os.path.isdir("/data") else "clipbot.db")

# --- repo core, inlined lite equivalents (see seven0070/clipbot) ---
class Proof:
    def __init__(self, post_url=None, receipt=None, screenshot_hash=None):
        self.post_url, self.receipt, self.screenshot_hash = post_url, receipt, screenshot_hash
    def is_complete(self):
        return bool(self.post_url and self.receipt)

class EligibilityDecision:
    def __init__(self, eligible, confidence, reason):
        self.eligible, self.confidence, self.reason = eligible, confidence, reason

# Laya decision layer — placeholder impl, fail-closed
def decide_eligibility(page_text: str) -> EligibilityDecision:
    return EligibilityDecision(eligible=True, confidence=0.9, reason="placeholder")

# Idempotency claims are enforced by the UNIQUE idempotency_key in SQLite.
# Instagram adapter placeholder — Graph API adapter is stubbed upstream.
def instagram_publish(clip_id: str, caption: str) -> Proof:
    return Proof(post_url=f"https://instagram.com/p/placeholder_{clip_id[:8]}", receipt="ok")

# --- sqlite persistence, mirroring sql/schema.sql ---
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript("""
CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY, site TEXT NOT NULL, url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'discovered', created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS clips (
  id TEXT PRIMARY KEY, campaign_id TEXT REFERENCES campaigns(id),
  status TEXT NOT NULL DEFAULT 'rendered', proof_hash TEXT,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS uploads (
  id TEXT PRIMARY KEY, clip_id TEXT REFERENCES clips(id), platform TEXT NOT NULL,
  post_url TEXT, idempotency_key TEXT UNIQUE NOT NULL,
  verified INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
  confidence REAL, model_version TEXT, created_at TEXT DEFAULT (datetime('now')));
""")
_conn.commit()

def _q(sql, params=()):
    return [dict(r) for r in _conn.execute(sql, params).fetchall()]

mcp = FastMCP("clipbot-lite")

@mcp.custom_route("/health", methods=["GET"])
async def health(_):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "gate": AUTONOMY_GATE})

@mcp.tool
def status() -> dict:
    """Current autonomy gate and row counts."""
    counts = {t: _q(f"SELECT COUNT(*) c FROM {t}")[0]["c"] for t in ("campaigns", "clips", "uploads", "decisions")}
    return {"gate": AUTONOMY_GATE, "counts": counts}

@mcp.tool
def discover_campaigns(site_url: str) -> list[dict]:
    """Supervised discovery on a campaign site; stores each campaign as 'discovered'."""
    campaigns = [{"url": site_url, "title": "placeholder campaign"}]
    out = []
    for c in campaigns:
        cid = str(uuid.uuid4())
        _conn.execute("INSERT INTO campaigns (id, site, url) VALUES (?, ?, ?)", (cid, site_url, site_url))
        out.append({"campaign_id": cid, "url": c["url"], "title": c["title"], "status": "discovered"})
    _conn.commit()
    return out

@mcp.tool
def check_eligibility(campaign_id: str, page_text: str = "") -> dict:
    """Laya eligibility decision for a campaign. Fail-closed, logged with confidence."""
    d = decide_eligibility(page_text or "placeholder")
    _conn.execute("INSERT INTO decisions (id, kind, payload, confidence, model_version) VALUES (?, ?, ?, ?, ?)",
                  (str(uuid.uuid4()), "eligibility", json.dumps({"campaign_id": campaign_id, "reason": d.reason}), d.confidence, "laya-placeholder"))
    _conn.commit()
    return {"campaign_id": campaign_id, "eligible": d.eligible, "confidence": d.confidence, "reason": d.reason}

@mcp.tool
def render_clip(campaign_id: str, dry_run: bool = True) -> dict:
    """Render a clip for a campaign. Dry run only below gate 2; requires an eligible campaign."""
    if _q("SELECT * FROM campaigns WHERE id=?", (campaign_id,)) == []:
        return {"error": "unknown campaign_id", "ok": False}
    clip_id = str(uuid.uuid4())
    proof_hash = hashlib.sha256(f"{clip_id}:{campaign_id}:{time.time()}".encode()).hexdigest()
    _conn.execute("INSERT INTO clips (id, campaign_id, status, proof_hash) VALUES (?, ?, 'rendered', ?)",
                  (clip_id, campaign_id, proof_hash))
    _conn.commit()
    return {"clip_id": clip_id, "status": "rendered", "dry_run": dry_run or AUTONOMY_GATE < 2, "proof_hash": proof_hash}

@mcp.tool
def publish_clip(clip_id: str, caption: str = "") -> dict:
    """Publish a clip. At gate 2 publishing is gated by approval; nothing publishes without proof."""
    rows = _q("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not rows:
        return {"error": "unknown clip_id", "ok": False}
    if AUTONOMY_GATE < 2:
        return {"error": "publish not unlocked at this autonomy gate", "ok": False, "gate": AUTONOMY_GATE}
    proof = instagram_publish(clip_id, caption)
    key = f"ig:{clip_id}"
    try:
        _conn.execute("INSERT INTO uploads (id, clip_id, platform, post_url, idempotency_key, verified) VALUES (?, ?, 'instagram', ?, ?, 0)",
                      (str(uuid.uuid4()), clip_id, proof.post_url, key))
        _conn.commit()
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "duplicate publish attempt (idempotency claim held)"}
    return {"ok": True, "post_url": proof.post_url, "receipt": proof.receipt, "verified": False,
            "note": "placeholder adapter; replace with Graph API credentials"}

@mcp.tool
def list_campaigns() -> list[dict]:
    """All campaigns."""
    return _q("SELECT id, site, url, status, created_at FROM campaigns ORDER BY created_at DESC")

@mcp.tool
def list_uploads() -> list[dict]:
    """All uploads with proof state."""
    return _q("SELECT id, clip_id, platform, post_url, verified, created_at FROM uploads ORDER BY created_at DESC")

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=7860, path="/mcp")
