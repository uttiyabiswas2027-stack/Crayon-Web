import json, os, time, uuid, asyncio
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import llm
import gmail_auth

BASE = Path(__file__).parent
MAX_MSG = 9000
MAX_TURNS = 16            # kept per visitor
MAX_SESSIONS = 2000
PER_IP_PER_MIN = int(os.environ.get("RATE_PER_MIN", "24"))
PER_IP_PER_DAY = int(os.environ.get("RATE_IP_DAY", "200"))
MAX_BODY = 30000
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", "8"))
GLOBAL_PER_DAY = int(os.environ.get("RATE_GLOBAL_DAY", "3000"))

app = FastAPI(title="Crayon", docs_url=None, redoc_url=None)
import shutil
_S = BASE / "static"
if not _S.exists():
    _S.mkdir()
    for _n in ("index.html", "style.css", "app.js", "crayon.svg", "favicon.svg", "pyworker.js"):
        if (BASE / _n).exists():
            shutil.copy(BASE / _n, _S / _n)
app.include_router(gmail_auth.router)
app.mount("/static", StaticFiles(directory=_S), name="static")

sessions: "OrderedDict[str, list]" = OrderedDict()
hits = defaultdict(deque)
ip_day: dict = {}
active = {"n": 0}
day = {"d": time.strftime("%Y-%m-%d"), "n": 0}
_model = None


_models: dict = {}


def model(name=None):
    key = name or "_default"
    if key not in _models:
        _models[key] = llm.build_model(name=name) if name else llm.build_model()
    return _models[key]


def client_ip(req: Request) -> str:
    xf = req.headers.get("x-forwarded-for", "")
    return (xf.split(",")[0].strip() if xf else (req.client.host if req.client else "?"))


def limited(ip: str) -> str | None:
    now = time.time()
    q = hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= PER_IP_PER_MIN:
        return "You're sending messages fast. Give it a few seconds."
    today = time.strftime("%Y-%m-%d")
    if day["d"] != today:
        day["d"], day["n"] = today, 0
    rec = ip_day.get(ip)
    if not rec or rec[0] != today:
        if len(ip_day) > 20000:
            ip_day.clear()
        rec = ip_day[ip] = [today, 0]
    if rec[1] >= PER_IP_PER_DAY:
        return "You've hit today's free limit for one visitor. Come back tomorrow."
    if day["n"] >= GLOBAL_PER_DAY:
        return "Crayon is out of free capacity for today. Try again tomorrow."
    q.append(now)
    day["n"] += 1
    rec[1] += 1
    return None


@app.middleware("http")
async def sec_headers(request: Request, call_next):
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "no-referrer"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return r


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "provider": llm.provider()}


@app.post("/api/chat")
async def chat(req: Request):
    if int(req.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"error": "Request too large."}, status_code=413)
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    text = str(body.get("message", "")).strip()
    sid = str(body.get("session_id") or "")[:64] or uuid.uuid4().hex
    if not text:
        return JSONResponse({"error": "empty message"}, status_code=400)
    if len(text) > MAX_MSG:
        return JSONResponse({"error": f"Message too long (max {MAX_MSG} characters)."}, status_code=400)
    msg = limited(client_ip(req))
    if msg:
        return JSONResponse({"error": msg}, status_code=429)

    if active["n"] >= MAX_STREAMS:
        return JSONResponse({"error": "Crayon is busy right now. Try again in a few seconds."}, status_code=503)
    mail_on = False
    try:
        mail_on = bool(body.get("mail")) and gmail_auth.current_user(req) is not None
    except Exception:
        mail_on = False
    hist = sessions.get(sid, [])
    sessions[sid] = hist
    sessions.move_to_end(sid)
    while len(sessions) > MAX_SESSIONS:
        sessions.popitem(last=False)

    async def gen():
        out = []
        active["n"] += 1
        try:
            names = [None] if llm.provider() != "gemini" else llm.model_names()
            last = None
            for nm in names:
                try:
                    async for ch in model(nm).astream(llm.to_messages(hist[-MAX_TURNS * 2:], text, mail_on)):
                        tt = llm.chunk_text(ch)
                        if tt:
                            out.append(tt)
                            yield f"data: {json.dumps({'t': tt})}\n\n"
                    last = None
                    break
                except Exception as e2:
                    last = e2
                    if out:
                        break
            if last is not None and not out:
                raise last
            full = "".join(out).strip()
            if not full:
                yield f"data: {json.dumps({'t': 'I got an empty answer. Try rephrasing?'})}\n\n"
            else:
                hist.append(("user", text))
                hist.append(("ai", full))
                del hist[:-MAX_TURNS * 2]
        except Exception as e:  # never leak keys or internals
            print("llm error:", type(e).__name__, str(e)[:200].replace(os.environ.get("GEMINI_API_KEY", "#"), "***"))
            yield f"data: {json.dumps({'err': 'Crayon hit a snag talking to its brain. Please try again in a moment.'})}\n\n"
        finally:
            active["n"] = max(0, active["n"] - 1)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_scache: dict = {}


def _do_search(q: str):
    from concurrent.futures import ThreadPoolExecutor
    from ddgs import DDGS

    def get_news():
        for _ in range(2):
            try:
                return [{"title": str(r.get("title", ""))[:140], "url": str(r.get("url", ""))[:300], "snippet": str(r.get("body", ""))[:300], "date": str(r.get("date", ""))[:10]}
                        for r in DDGS(timeout=8).news(q, max_results=6, safesearch="moderate")]
            except Exception as e:
                print("search news error:", type(e).__name__)
        return None

    def get_text():
        for _ in range(2):
            try:
                return [{"title": str(r.get("title", ""))[:140], "url": str(r.get("href", ""))[:300], "snippet": str(r.get("body", ""))[:300], "date": ""}
                        for r in DDGS(timeout=8).text(q, max_results=6, region="wt-wt", safesearch="moderate")]
            except Exception as e:
                print("search text error:", type(e).__name__)
        return None

    with ThreadPoolExecutor(2) as ex:
        fn, ft = ex.submit(get_news), ex.submit(get_text)
        news, text = fn.result(), ft.result()
    ok = news is not None and text is not None
    news, text = news or [], text or []
    seen, res = set(), []
    for r in news[:5] + text + news[5:]:
        if r["url"].startswith(("http://", "https://")) and r["url"] not in seen:
            seen.add(r["url"])
            res.append(r)
    return res[:9], ok


@app.post("/api/search")
async def search(req: Request):
    if int(req.headers.get("content-length") or 0) > 2000:
        return JSONResponse({"error": "Request too large."}, status_code=413)
    try:
        q = str((await req.json()).get("q", "")).strip()[:200]
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    if not q:
        return JSONResponse({"error": "empty query"}, status_code=400)
    msg = limited(client_ip(req))
    if msg:
        return JSONResponse({"error": msg}, status_code=429)
    now = time.time()
    hit = _scache.get(q.lower())
    if hit and now - hit[0] < 300:
        return {"results": hit[1]}
    try:
        res, ok = await asyncio.wait_for(asyncio.to_thread(_do_search, q), timeout=30)
    except Exception:
        return JSONResponse({"error": "Search is unavailable right now."}, status_code=503)
    if ok and len(res) >= 3:
        if len(_scache) > 500:
            _scache.clear()
        _scache[q.lower()] = (now, res)
    return {"results": res}


@app.post("/api/reset")
async def reset(req: Request):
    try:
        sid = str((await req.json()).get("session_id", ""))
    except Exception:
        sid = ""
    sessions.pop(sid, None)
    return {"ok": True}
