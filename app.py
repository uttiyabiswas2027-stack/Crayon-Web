import json, os, time, uuid, asyncio
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import llm

BASE = Path(__file__).parent
MAX_MSG = 4000
MAX_TURNS = 16            # kept per visitor
MAX_SESSIONS = 2000
PER_IP_PER_MIN = int(os.environ.get("RATE_PER_MIN", "12"))
GLOBAL_PER_DAY = int(os.environ.get("RATE_GLOBAL_DAY", "1500"))

app = FastAPI(title="Crayon", docs_url=None, redoc_url=None)
import shutil
_S = BASE / "static"
if not _S.exists():
    _S.mkdir()
    for _n in ("index.html", "style.css", "app.js", "crayon.svg", "favicon.svg"):
        if (BASE / _n).exists():
            shutil.copy(BASE / _n, _S / _n)
app.mount("/static", StaticFiles(directory=_S), name="static")

sessions: "OrderedDict[str, list]" = OrderedDict()
hits = defaultdict(deque)
day = {"d": time.strftime("%Y-%m-%d"), "n": 0}
_model = None


def model():
    global _model
    if _model is None:
        _model = llm.build_model()
    return _model


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
    if day["n"] >= GLOBAL_PER_DAY:
        return "Crayon is out of free capacity for today. Try again tomorrow."
    q.append(now)
    day["n"] += 1
    return None


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "provider": llm.provider()}


@app.post("/api/chat")
async def chat(req: Request):
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

    hist = sessions.get(sid, [])
    sessions[sid] = hist
    sessions.move_to_end(sid)
    while len(sessions) > MAX_SESSIONS:
        sessions.popitem(last=False)

    async def gen():
        out = []
        try:
            m = model()
            async for ch in m.astream(llm.to_messages(hist[-MAX_TURNS * 2:], text)):
                t = llm.chunk_text(ch)
                if t:
                    out.append(t)
                    yield f"data: {json.dumps({'t': t})}\n\n"
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
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/reset")
async def reset(req: Request):
    try:
        sid = str((await req.json()).get("session_id", ""))
    except Exception:
        sid = ""
    sessions.pop(sid, None)
    return {"ok": True}
