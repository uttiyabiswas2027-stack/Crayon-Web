"""One-time secret intake: lets the owner (or an agent filling from a password vault) store the
OpenRouter key without it ever passing through chat or the repo. Encrypted in Postgres."""
import hmac, os, time
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
import gmail_auth

router = APIRouter()
_tries = {"n": 0}
PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Crayon setup</title>
<body style="font:16px system-ui;max-width:420px;margin:40px auto;padding:0 16px"><h3>Crayon key setup</h3>
<input id=k type=password placeholder="OpenRouter API key" style="width:100%;padding:10px"><br><br>
<button id=b style="padding:10px 16px">Save</button> <span id=m></span>
<script>b.onclick=async()=>{const r=await fetch('/setup/key',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'crayon'},body:JSON.stringify({t:location.hash.slice(1),k:k.value})});m.textContent=r.ok?'saved':'failed';k.value=''}</script>"""


def _init():
    gmail_auth.db("CREATE TABLE IF NOT EXISTS cw_secrets (name TEXT PRIMARY KEY, val TEXT NOT NULL)")


def load_into_env():
    try:
        _init()
        rows = gmail_auth.db("SELECT name, val FROM cw_secrets", (), True) or []
        for n, v in rows:
            if n == "OPENROUTER_API_KEY" and not os.environ.get(n):
                os.environ[n] = gmail_auth._fernet().decrypt(v.encode()).decode()
    except Exception as e:
        print("keystore load:", type(e).__name__)


@router.get("/setup/key")
def page():
    if not os.environ.get("SETUP_TOKEN"):
        return JSONResponse({"error": "not found"}, status_code=404)
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


@router.post("/setup/key")
async def save(req: Request):
    tok = os.environ.get("SETUP_TOKEN", "")
    if not tok or _tries["n"] > 20 or req.headers.get("x-requested-with") != "crayon":
        return JSONResponse({"error": "no"}, status_code=403)
    _tries["n"] += 1
    try:
        b = await req.json()
        t, k = str(b.get("t", "")), str(b.get("k", "")).strip()
    except Exception:
        return JSONResponse({"error": "bad"}, status_code=400)
    if not hmac.compare_digest(t, tok) or not k.startswith("sk-or-") or len(k) > 300:
        return JSONResponse({"error": "no"}, status_code=403)
    _init()
    enc = gmail_auth._fernet().encrypt(k.encode()).decode()
    gmail_auth.db("DELETE FROM cw_secrets WHERE name=%s", ("OPENROUTER_API_KEY",))
    gmail_auth.db("INSERT INTO cw_secrets (name, val) VALUES (%s, %s)", ("OPENROUTER_API_KEY", enc))
    os.environ["OPENROUTER_API_KEY"] = k
    os.environ.pop("SETUP_TOKEN", None)
    return {"ok": True}
