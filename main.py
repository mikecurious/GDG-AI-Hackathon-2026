"""FastAPI entry point for the County Budget Watchdog agent."""
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_sessions: dict[str, object] = {}

NAIROBI_WARDS = [
    "Westlands", "Parklands/Highridge", "Karura", "Kangemi", "Mountain View",
    "Kilimani", "Kawangware", "Gatina", "Kileleshwa", "Kabiro",
    "Woodley/Kenyatta Golf Course", "Sarang'ombe", "Makina", "Langa'ta", "Nyayo Highrise",
    "Karen", "Nairobi West", "Mugumu-ini", "South C", "Nyayo Highrise",
    "Madaraka", "Matopeni/Spring Valley", "Imara Daima", "Kayole North",
    "Embakasi", "Utawala", "Mihang'o", "Ruai", "Kasarani",
    "Clay City", "Mwiki", "Kasarani", "Njiru", "Zimmerman", "Roysambu",
    "Lucky Summer", "Baba Dogo", "Utalii", "Mathare North", "Hospital",
    "Parklands", "Ngara", "Nairobi Central", "Pumwani", "Eastleigh North",
    "Eastleigh South", "Airbase", "California", "Makadara", "Maringo/Hamza",
    "Viwandani", "Harambee", "Makongeni", "Starehe", "Pangani",
    "Ziwani/Kariokor", "Kamkunji", "Gikomba", "Bahati",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("County Budget Watchdog starting...")
    yield
    log.info("Shutting down.")


app = FastAPI(
    title="County Budget Watchdog",
    description="AI agent for Nairobi County budget transparency.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    phone_number: str | None = None  # if set, reply is also sent via SMS


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[str] = []
    sms_sent: bool = False


class SubscribeRequest(BaseModel):
    name: str
    phone: str
    ward: str


class SubscribeResponse(BaseModel):
    success: bool
    message: str
    subscriber_id: str | None = None


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "project_id": os.getenv("GCP_PROJECT_ID", "gdg-ai-2026-496507"),
        "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    }


@app.get("/wards")
async def get_wards():
    return {"wards": sorted(set(NAIROBI_WARDS))}


@app.post("/subscribe", response_model=SubscribeResponse)
async def subscribe(req: SubscribeRequest):
    from agent.tools import _get_bq, PROJECT_ID, DATASET_ID, _format_phone

    if not req.phone or not req.ward:
        raise HTTPException(status_code=400, detail="Phone and ward are required.")

    mobile = _format_phone(req.phone)
    if len(mobile) < 9:
        raise HTTPException(status_code=400, detail="Invalid phone number.")

    bq = _get_bq()
    sub_id = str(uuid.uuid4())
    row = {
        "id": sub_id,
        "name": req.name or "Anonymous",
        "phone": mobile,
        "ward": req.ward,
        "county": "Nairobi",
        "subscribed_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    errors = bq.insert_rows_json(f"{PROJECT_ID}.{DATASET_ID}.subscribers", [row])
    if errors:
        log.error("Subscriber insert error: %s", errors)
        raise HTTPException(status_code=500, detail="Could not save subscription.")

    log.info("New subscriber: %s (%s) → %s", req.name, mobile, req.ward)
    return SubscribeResponse(
        success=True,
        message=f"Subscribed! You'll receive budget digests for {req.ward} ward.",
        subscriber_id=sub_id,
    )


@app.get("/subscribers/count")
async def subscriber_count():
    from agent.tools import _get_bq, PROJECT_ID, DATASET_ID
    bq = _get_bq()
    try:
        rows = list(bq.query(
            f"SELECT COUNT(*) AS cnt FROM `{PROJECT_ID}.{DATASET_ID}.subscribers` WHERE active = TRUE"
        ).result())
        return {"count": rows[0].cnt if rows else 0}
    except Exception:
        return {"count": 0}


@app.get("/stats")
async def budget_stats():
    from agent.tools import _get_bq, PROJECT_ID, DATASET_ID
    bq = _get_bq()
    try:
        rows = list(bq.query(f"""
            SELECT
                SUM(amount_approved) AS total,
                COUNT(DISTINCT department) AS departments,
                COUNT(*) AS line_items,
                COUNT(DISTINCT programme) AS programmes
            FROM `{PROJECT_ID}.{DATASET_ID}.budget_line_items`
            WHERE fiscal_year = '2025/2026' AND county = 'Nairobi'
        """).result())
        r = rows[0] if rows else None
        return {
            "total_budget_kes": r.total or 0,
            "departments": r.departments or 0,
            "line_items": r.line_items or 0,
            "programmes": r.programmes or 0,
            "fiscal_year": "2025/2026",
        }
    except Exception:
        return {"total_budget_kes": 48986600000, "departments": 12, "line_items": 0, "programmes": 0, "fiscal_year": "2025/2026"}


def _sms_summary(reply: str, question: str) -> str:
    """Trim agent reply to 155 chars for SMS, preserving whole sentences."""
    prefix = "BudgetWatchdog: "
    budget = 160 - len(prefix)
    text = reply.strip().replace("\n", " ")
    if len(text) <= budget:
        return prefix + text
    # Cut at the last sentence boundary within budget
    cut = text[:budget]
    last_stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if last_stop > 60:
        cut = cut[: last_stop + 1]
    else:
        cut = cut[:budget - 3] + "..."
    return prefix + cut


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types
    from agent.adk_setup import root_agent
    from agent.tools import send_quick_sms

    session_id = req.session_id or str(uuid.uuid4())

    if session_id not in _sessions:
        session_service = InMemorySessionService()
        runner = Runner(
            agent=root_agent,
            app_name="county_budget_watchdog",
            session_service=session_service,
        )
        await session_service.create_session(
            app_name="county_budget_watchdog",
            user_id="citizen",
            session_id=session_id,
        )
        _sessions[session_id] = (runner, session_service)

    runner, session_service = _sessions[session_id]
    user_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=req.message)],
    )

    tool_calls_made = []
    final_reply = ""

    try:
        async for event in runner.run_async(
            user_id="citizen",
            session_id=session_id,
            new_message=user_message,
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if part.text:
                        final_reply += part.text
            elif hasattr(event, "get_function_calls") and event.get_function_calls():
                for fc in event.get_function_calls():
                    tool_calls_made.append(fc.name)
    except Exception as exc:
        log.error("Agent error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    final_reply = final_reply or "I could not generate a response. Please try again."

    # Auto-send SMS if caller provided a phone number
    sms_sent = False
    if req.phone_number and final_reply:
        sms_text = _sms_summary(final_reply, req.message)
        sms_sent = send_quick_sms(req.phone_number, sms_text)

    return ChatResponse(
        session_id=session_id,
        reply=final_reply,
        tool_calls=tool_calls_made,
        sms_sent=sms_sent,
    )


@app.get("/", response_class=HTMLResponse)
async def root():
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>County Budget Watchdog · Nairobi</title>
<style>
:root {
  --green-900: #062a14;
  --green-800: #0a4020;
  --green-700: #0f5c2e;
  --green-600: #166534;
  --green-500: #16a34a;
  --green-400: #22c55e;
  --green-100: #dcfce7;
  --green-50:  #f0fdf4;
  --gold:      #f59e0b;
  --gold-light:#fef3c7;
  --gray-900:  #111827;
  --gray-700:  #374151;
  --gray-500:  #6b7280;
  --gray-200:  #e5e7eb;
  --gray-100:  #f3f4f6;
  --white:     #ffffff;
  --radius:    12px;
  --shadow:    0 4px 24px rgba(0,0,0,.08);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--gray-100); color: var(--gray-900); min-height: 100vh; }

/* ── NAV ── */
nav {
  background: var(--green-900);
  padding: 0 1.5rem;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky; top: 0; z-index: 100;
  box-shadow: 0 2px 12px rgba(0,0,0,.3);
}
.nav-brand { display: flex; align-items: center; gap: .75rem; }
.nav-logo {
  width: 36px; height: 36px; background: var(--green-600);
  border-radius: 8px; display: flex; align-items: center; justify-content: center;
  font-size: 1.2rem;
}
.nav-title { color: var(--white); font-size: 1rem; font-weight: 700; letter-spacing: -.01em; }
.nav-sub { color: rgba(255,255,255,.5); font-size: .7rem; }
.nav-badge {
  background: var(--gold); color: var(--gray-900); font-size: .65rem;
  font-weight: 700; padding: .15rem .5rem; border-radius: 20px; letter-spacing: .04em;
}
.nav-right { display: flex; align-items: center; gap: .75rem; }
#sub-btn {
  background: var(--green-600); color: var(--white); border: none;
  padding: .4rem 1rem; border-radius: 8px; font-size: .82rem; font-weight: 600;
  cursor: pointer; transition: background .2s;
}
#sub-btn:hover { background: var(--green-500); }

/* ── HERO STATS ── */
.stats-bar {
  background: linear-gradient(135deg, var(--green-800) 0%, var(--green-700) 100%);
  padding: 1.25rem 1.5rem;
  display: flex; gap: 1rem; flex-wrap: wrap; justify-content: center;
}
.stat-card {
  background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.12);
  border-radius: 10px; padding: .75rem 1.25rem; text-align: center; min-width: 140px;
  flex: 1; max-width: 200px;
}
.stat-value { color: var(--gold); font-size: 1.4rem; font-weight: 800; line-height: 1; }
.stat-label { color: rgba(255,255,255,.7); font-size: .7rem; margin-top: .3rem; text-transform: uppercase; letter-spacing: .06em; }

/* ── LAYOUT ── */
.app-layout {
  max-width: 1100px; margin: 0 auto;
  display: grid;
  grid-template-columns: 1fr 340px;
  gap: 1.25rem;
  padding: 1.25rem;
}
@media (max-width: 768px) {
  .app-layout { grid-template-columns: 1fr; }
  .sidebar { display: none; }
}

/* ── CHAT PANEL ── */
.chat-panel {
  background: var(--white); border-radius: var(--radius);
  box-shadow: var(--shadow); display: flex; flex-direction: column;
  height: calc(100vh - 180px); min-height: 500px;
}
.chat-header {
  padding: .875rem 1.25rem;
  border-bottom: 1px solid var(--gray-200);
  display: flex; align-items: center; gap: .75rem;
}
.agent-avatar {
  width: 38px; height: 38px; background: var(--green-600);
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  color: var(--white); font-size: 1.1rem; flex-shrink: 0;
}
.agent-info { flex: 1; }
.agent-name { font-weight: 700; font-size: .9rem; }
.agent-status { font-size: .72rem; color: var(--green-500); display: flex; align-items: center; gap: .3rem; }
.dot { width: 6px; height: 6px; background: var(--green-400); border-radius: 50%; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

#messages {
  flex: 1; overflow-y: auto; padding: 1.25rem;
  display: flex; flex-direction: column; gap: .875rem;
  scroll-behavior: smooth;
}
.msg { display: flex; gap: .5rem; max-width: 88%; }
.msg.user { align-self: flex-end; flex-direction: row-reverse; }
.msg.agent { align-self: flex-start; }
.msg-avatar {
  width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; font-size: .8rem;
  margin-top: .1rem;
}
.msg.agent .msg-avatar { background: var(--green-100); color: var(--green-700); }
.msg.user  .msg-avatar { background: var(--green-600); color: var(--white); }
.msg-body { display: flex; flex-direction: column; gap: .2rem; }
.msg-bubble {
  padding: .65rem 1rem; border-radius: 16px; font-size: .875rem;
  line-height: 1.55; white-space: pre-wrap; word-break: break-word;
}
.msg.agent .msg-bubble {
  background: var(--gray-100); color: var(--gray-900);
  border-bottom-left-radius: 4px;
}
.msg.user .msg-bubble {
  background: var(--green-600); color: var(--white);
  border-bottom-right-radius: 4px;
}
.msg-meta { font-size: .68rem; color: var(--gray-500); padding: 0 .25rem; }
.msg.user .msg-meta { text-align: right; }
.tool-pill {
  display: inline-flex; align-items: center; gap: .25rem;
  background: var(--green-50); border: 1px solid var(--green-100);
  color: var(--green-700); font-size: .67rem; font-weight: 600;
  padding: .15rem .5rem; border-radius: 20px; margin-top: .2rem;
}

/* typing indicator */
.typing-bubble { display: flex; align-items: center; gap: 4px; padding: .65rem 1rem; }
.typing-dot {
  width: 6px; height: 6px; background: var(--gray-500); border-radius: 50%;
  animation: bounce .9s infinite;
}
.typing-dot:nth-child(2) { animation-delay: .15s; }
.typing-dot:nth-child(3) { animation-delay: .3s; }
@keyframes bounce { 0%,80%,100%{transform:translateY(0)} 40%{transform:translateY(-6px)} }

/* chips */
.chips { display: flex; flex-wrap: wrap; gap: .45rem; padding: 0 1.25rem .75rem; }
.chip {
  background: var(--green-50); border: 1px solid var(--green-100);
  color: var(--green-700); font-size: .75rem; font-weight: 500;
  padding: .3rem .75rem; border-radius: 20px; cursor: pointer;
  transition: all .15s; white-space: nowrap;
}
.chip:hover { background: var(--green-100); border-color: var(--green-400); }

/* phone bar */
.chat-footer { padding: .875rem 1.25rem; border-top: 1px solid var(--gray-200); display: flex; flex-direction: column; gap: .5rem; }
.phone-bar { display: flex; align-items: center; gap: .5rem; background: var(--green-50); border: 1px solid var(--green-100); border-radius: 8px; padding: .35rem .75rem; }
.phone-icon { font-size: .95rem; flex-shrink: 0; }
.phone-bar input { flex: 1; border: none; background: transparent; font-size: .8rem; outline: none; color: var(--gray-700); font-family: inherit; }
.phone-bar input::placeholder { color: var(--gray-500); }
.sms-ind { font-size: .72rem; color: var(--green-600); font-weight: 700; white-space: nowrap; }

/* input row */
.input-row { display: flex; gap: .5rem; }
.input-row input {
  flex: 1; padding: .65rem 1rem;
  border: 1.5px solid var(--gray-200); border-radius: 10px;
  font-size: .9rem; outline: none; transition: border-color .2s;
  font-family: inherit;
}
.input-row input:focus { border-color: var(--green-500); }
.input-row button {
  background: var(--green-600); color: var(--white); border: none;
  padding: .65rem 1.1rem; border-radius: 10px; cursor: pointer;
  font-size: .9rem; font-weight: 600; transition: background .2s;
  display: flex; align-items: center; gap: .35rem;
}
.input-row button:hover { background: var(--green-500); }
.input-row button:disabled { opacity: .5; cursor: not-allowed; }

/* ── SIDEBAR ── */
.sidebar { display: flex; flex-direction: column; gap: 1rem; }
.side-card {
  background: var(--white); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 1.1rem;
}
.side-card h3 { font-size: .82rem; font-weight: 700; color: var(--gray-700); text-transform: uppercase; letter-spacing: .06em; margin-bottom: .875rem; }
.quick-link {
  display: flex; align-items: center; gap: .6rem;
  padding: .5rem .625rem; border-radius: 8px; cursor: pointer;
  font-size: .82rem; color: var(--gray-700); transition: background .15s;
  border: none; background: transparent; width: 100%; text-align: left;
}
.quick-link:hover { background: var(--green-50); color: var(--green-700); }
.quick-link .ql-icon { font-size: 1rem; width: 20px; text-align: center; }

.sub-form { display: flex; flex-direction: column; gap: .6rem; }
.sub-form input, .sub-form select {
  padding: .55rem .8rem; border: 1.5px solid var(--gray-200); border-radius: 8px;
  font-size: .82rem; outline: none; font-family: inherit; transition: border-color .2s;
}
.sub-form input:focus, .sub-form select:focus { border-color: var(--green-500); }
.sub-form button {
  background: var(--green-600); color: var(--white); border: none;
  padding: .6rem; border-radius: 8px; font-size: .82rem; font-weight: 700;
  cursor: pointer; transition: background .2s;
}
.sub-form button:hover { background: var(--green-500); }
.sub-msg { font-size: .75rem; padding: .4rem .6rem; border-radius: 6px; text-align: center; }
.sub-msg.ok  { background: var(--green-100); color: var(--green-700); }
.sub-msg.err { background: #fef2f2; color: #b91c1c; }

.sub-count-box {
  background: var(--green-50); border: 1px solid var(--green-100);
  border-radius: 8px; padding: .6rem .8rem;
  display: flex; align-items: center; justify-content: space-between;
}
.sub-count-num { font-size: 1.4rem; font-weight: 800; color: var(--green-700); }
.sub-count-label { font-size: .7rem; color: var(--gray-500); }

/* ── MODAL ── */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.5);
  z-index: 200; display: none; align-items: center; justify-content: center; padding: 1rem;
}
.modal-overlay.open { display: flex; }
.modal {
  background: var(--white); border-radius: 16px; padding: 1.75rem;
  max-width: 400px; width: 100%; box-shadow: 0 20px 60px rgba(0,0,0,.2);
  animation: slideUp .25s ease;
}
@keyframes slideUp { from{transform:translateY(20px);opacity:0} to{transform:translateY(0);opacity:1} }
.modal h2 { font-size: 1.1rem; font-weight: 800; margin-bottom: .25rem; }
.modal p  { font-size: .82rem; color: var(--gray-500); margin-bottom: 1rem; }
.modal-close {
  float: right; background: none; border: none; font-size: 1.2rem;
  cursor: pointer; color: var(--gray-500); margin-top: -.25rem;
}
</style>
</head>
<body>

<!-- NAV -->
<nav>
  <div class="nav-brand">
    <div class="nav-logo">&#128065;</div>
    <div>
      <div class="nav-title">County Budget Watchdog</div>
      <div class="nav-sub">Nairobi City County · FY 2025/2026</div>
    </div>
  </div>
  <div class="nav-right">
    <span class="nav-badge">LIVE</span>
    <button id="sub-btn" onclick="openModal()">Get SMS Alerts</button>
  </div>
</nav>

<!-- STATS BAR -->
<div class="stats-bar" id="stats-bar">
  <div class="stat-card"><div class="stat-value" id="s-total">KES 49B</div><div class="stat-label">Total Budget</div></div>
  <div class="stat-card"><div class="stat-value" id="s-depts">–</div><div class="stat-label">Departments</div></div>
  <div class="stat-card"><div class="stat-value" id="s-items">–</div><div class="stat-label">Line Items Indexed</div></div>
  <div class="stat-card"><div class="stat-value" id="s-subs">–</div><div class="stat-label">SMS Subscribers</div></div>
</div>

<!-- MAIN LAYOUT -->
<div class="app-layout">

  <!-- CHAT -->
  <div class="chat-panel">
    <div class="chat-header">
      <div class="agent-avatar">&#128065;</div>
      <div class="agent-info">
        <div class="agent-name">Budget Watchdog AI</div>
        <div class="agent-status"><span class="dot"></span> Powered by Gemini 2.5 · Nairobi County Budget FY 2025/26</div>
      </div>
    </div>

    <div id="messages">
      <div class="msg agent">
        <div class="msg-avatar">&#128065;</div>
        <div class="msg-body">
          <div class="msg-bubble">Habari! I'm the County Budget Watchdog.

I can answer any question about the Nairobi County FY 2025/2026 budget — allocations by department, ward spending, gazette amendments, and more.

Try one of the suggestions below, or ask your own question in English or Swahili.</div>
          <div class="msg-meta">Watchdog AI · now</div>
        </div>
      </div>
    </div>

    <div class="chips" id="chips">
      <span class="chip" onclick="ask(this.textContent)">Total budget breakdown?</span>
      <span class="chip" onclick="ask(this.textContent)">Health department allocation</span>
      <span class="chip" onclick="ask(this.textContent)">Roads &amp; infrastructure spending</span>
      <span class="chip" onclick="ask(this.textContent)">Education budget 2025/2026</span>
      <span class="chip" onclick="ask(this.textContent)">Gazette amendments for supplementary</span>
      <span class="chip" onclick="ask(this.textContent)">Westlands ward allocation</span>
      <span class="chip" onclick="ask(this.textContent)">Bajeti ya afya ni ngapi?</span>
    </div>

    <div class="chat-footer">
      <div class="phone-bar">
        <span class="phone-icon">&#128241;</span>
        <input id="phone-input" type="tel" placeholder="Your phone (e.g. 0712345678) — get answers via SMS too">
        <span id="sms-indicator" class="sms-ind" style="display:none">SMS sent &#10003;</span>
      </div>
      <div class="input-row">
        <input id="msg-input" type="text" placeholder="Ask about the Nairobi County budget..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){sendMsg();event.preventDefault()}">
        <button id="send-btn" onclick="sendMsg()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          Send
        </button>
      </div>
    </div>
  </div>

  <!-- SIDEBAR -->
  <div class="sidebar">

    <!-- SMS Subscription -->
    <div class="side-card">
      <h3>&#128241; SMS Alerts</h3>
      <div class="sub-count-box" style="margin-bottom:.875rem">
        <div>
          <div class="sub-count-num" id="side-sub-count">–</div>
          <div class="sub-count-label">Active subscribers</div>
        </div>
        <div style="font-size:1.5rem">&#128241;</div>
      </div>
      <div class="sub-form" id="side-form">
        <input id="sf-name"  type="text"  placeholder="Your name (optional)">
        <input id="sf-phone" type="tel"   placeholder="Phone e.g. 0712345678" required>
        <select id="sf-ward">
          <option value="">Select your ward…</option>
        </select>
        <button onclick="subscribeSide()">Subscribe to Budget Alerts</button>
        <div id="sf-msg" class="sub-msg" style="display:none"></div>
      </div>
    </div>

    <!-- Quick Questions -->
    <div class="side-card">
      <h3>&#128270; Quick Queries</h3>
      <button class="quick-link" onclick="ask('What is the total recurrent budget for 2025/2026?')">
        <span class="ql-icon">&#128202;</span> Recurrent vs development split
      </button>
      <button class="quick-link" onclick="ask('Which department has the highest budget allocation?')">
        <span class="ql-icon">&#127942;</span> Highest funded department
      </button>
      <button class="quick-link" onclick="ask('How much is allocated for water and sanitation?')">
        <span class="ql-icon">&#128167;</span> Water &amp; sanitation budget
      </button>
      <button class="quick-link" onclick="ask('What is allocated for bursaries and scholarships?')">
        <span class="ql-icon">&#127891;</span> Bursaries &amp; scholarships
      </button>
      <button class="quick-link" onclick="ask('How much goes to county assembly operations?')">
        <span class="ql-icon">&#127963;</span> County Assembly budget
      </button>
      <button class="quick-link" onclick="ask('Check for any gazette amendments to the Nairobi budget')">
        <span class="ql-icon">&#128240;</span> Gazette amendments
      </button>
    </div>

    <!-- About -->
    <div class="side-card">
      <h3>&#8505; About</h3>
      <p style="font-size:.78rem;color:var(--gray-500);line-height:1.6">
        Every county publishes a budget. Almost nobody reads it.
        This agent indexes the full 400-page Nairobi FY 2025/2026 budget and gives citizens
        plain-language answers — in real time.
        <br><br>
        Built at <strong>GDG Nairobi Agentathon 2026</strong> using Google ADK,
        Gemini 2.5, BigQuery, and Cloud Run.
      </p>
    </div>

  </div>
</div>

<!-- SUBSCRIBE MODAL -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal()">&#x2715;</button>
    <h2>&#128241; Get Budget SMS Alerts</h2>
    <p>Receive plain-language budget digests for your ward directly to your phone.</p>
    <div class="sub-form">
      <input id="m-name"  type="text" placeholder="Your name (optional)">
      <input id="m-phone" type="tel" placeholder="Phone e.g. 0712345678" required>
      <select id="m-ward"><option value="">Select your ward…</option></select>
      <button onclick="subscribeModal()">Subscribe Now — It's Free</button>
      <div id="m-msg" class="sub-msg" style="display:none"></div>
    </div>
  </div>
</div>

<script>
let sessionId = null;
let sending = false;

// ── Load stats ──
async function loadStats() {
  try {
    const [stats, subs] = await Promise.all([
      fetch('/stats').then(r=>r.json()),
      fetch('/subscribers/count').then(r=>r.json())
    ]);
    const total = stats.total_budget_kes || 0;
    document.getElementById('s-total').textContent =
      total >= 1e9 ? `KES ${(total/1e9).toFixed(1)}B` : `KES ${(total/1e6).toFixed(0)}M`;
    document.getElementById('s-depts').textContent = stats.departments || '–';
    document.getElementById('s-items').textContent = (stats.line_items||0).toLocaleString();
    document.getElementById('s-subs').textContent  = subs.count || '0';
    document.getElementById('side-sub-count').textContent = subs.count || '0';
  } catch(e) { /* silently degrade */ }
}

// ── Load wards ──
async function loadWards() {
  try {
    const d = await fetch('/wards').then(r=>r.json());
    const opts = d.wards.map(w=>`<option value="${w}">${w}</option>`).join('');
    document.getElementById('sf-ward').innerHTML = '<option value="">Select your ward…</option>' + opts;
    document.getElementById('m-ward').innerHTML  = '<option value="">Select your ward…</option>' + opts;
  } catch(e) {}
}

loadStats();
loadWards();
setInterval(loadStats, 30000);

// ── Chat ──
function now() {
  return new Date().toLocaleTimeString('en-KE',{hour:'2-digit',minute:'2-digit'});
}

function addMsg(role, text, tools) {
  const msgs = document.getElementById('messages');
  const div  = document.createElement('div');
  div.className = 'msg ' + role;

  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar';
  avatar.textContent = role === 'agent' ? '👁' : '👤';

  const body   = document.createElement('div');
  body.className = 'msg-body';

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;
  body.appendChild(bubble);

  if (tools && tools.length) {
    tools.forEach(t => {
      const pill = document.createElement('span');
      pill.className = 'tool-pill';
      pill.textContent = '⚙ ' + t.replace(/_/g,' ');
      body.appendChild(pill);
    });
  }

  const meta = document.createElement('div');
  meta.className = 'msg-meta';
  meta.textContent = (role === 'agent' ? 'Watchdog AI' : 'You') + ' · ' + now();
  body.appendChild(meta);

  div.appendChild(avatar);
  div.appendChild(body);
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return bubble;
}

function addTyping() {
  const msgs = document.getElementById('messages');
  const div  = document.createElement('div');
  div.className = 'msg agent'; div.id = 'typing-indicator';

  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar'; avatar.textContent = '👁';

  const body = document.createElement('div'); body.className = 'msg-body';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble typing-bubble';
  bubble.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  body.appendChild(bubble);
  div.appendChild(avatar); div.appendChild(body);
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return div;
}

async function sendMsg() {
  if (sending) return;
  const input = document.getElementById('msg-input');
  const btn   = document.getElementById('send-btn');
  const phone = document.getElementById('phone-input').value.trim();
  const smsInd = document.getElementById('sms-indicator');
  const text  = input.value.trim();
  if (!text) return;

  input.value = '';
  smsInd.style.display = 'none';
  sending = true;
  btn.disabled = true;

  addMsg('user', text);
  document.getElementById('chips').style.display = 'none';
  const typing = addTyping();

  try {
    const body = {message: text, session_id: sessionId};
    if (phone) body.phone_number = phone;

    const res  = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      const err = await res.json().catch(()=>({detail:'Server error'}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    sessionId = data.session_id;
    typing.remove();
    addMsg('agent', data.reply, data.tool_calls);
    if (data.sms_sent) {
      smsInd.style.display = 'inline';
      setTimeout(()=>{ smsInd.style.display='none'; }, 4000);
    }
    loadStats();
  } catch(e) {
    typing.remove();
    addMsg('agent', 'Error: ' + e.message + '\n\nPlease try again.');
  } finally {
    sending = false;
    btn.disabled = false;
    input.focus();
  }
}

function ask(text) {
  document.getElementById('msg-input').value = text;
  sendMsg();
}

// ── Subscribe (sidebar) ──
async function subscribeSide() {
  const name  = document.getElementById('sf-name').value.trim();
  const phone = document.getElementById('sf-phone').value.trim();
  const ward  = document.getElementById('sf-ward').value;
  const msg   = document.getElementById('sf-msg');
  await doSubscribe(name, phone, ward, msg);
}

async function subscribeModal() {
  const name  = document.getElementById('m-name').value.trim();
  const phone = document.getElementById('m-phone').value.trim();
  const ward  = document.getElementById('m-ward').value;
  const msg   = document.getElementById('m-msg');
  await doSubscribe(name, phone, ward, msg);
}

async function doSubscribe(name, phone, ward, msgEl) {
  msgEl.style.display = 'none';
  if (!phone || !ward) {
    showMsg(msgEl, 'Please enter your phone number and select a ward.', 'err');
    return;
  }
  try {
    const res  = await fetch('/subscribe', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name, phone, ward})
    });
    const data = await res.json();
    if (data.success) {
      showMsg(msgEl, data.message, 'ok');
      loadStats();
    } else {
      showMsg(msgEl, data.detail || 'Subscription failed.', 'err');
    }
  } catch(e) {
    showMsg(msgEl, 'Network error. Please try again.', 'err');
  }
}

function showMsg(el, text, type) {
  el.textContent = text;
  el.className = 'sub-msg ' + type;
  el.style.display = 'block';
}

// ── Modal ──
function openModal()  { document.getElementById('modal').classList.add('open'); }
function closeModal() { document.getElementById('modal').classList.remove('open'); }
document.getElementById('modal').addEventListener('click', e => {
  if (e.target === document.getElementById('modal')) closeModal();
});
</script>
</body>
</html>"""
