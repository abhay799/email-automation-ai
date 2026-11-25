import os
import random
import re
import time
from datetime import datetime
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from google import genai
from google.genai import types
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="AI Email Triage, Task & Reply Automation")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
FALLBACK_MODELS = list(dict.fromkeys([PRIMARY_MODEL, "gemini-3.8-flash-lite"]))

client = genai.Client(api_key=GEMINI_API_KEY)

# --- MOCK INDIAN B2B / D2C OPERATIONS INBOX (ZERO-DB INSTANT DEMO) ---
INBOX_DB: dict[str, dict[str, Any]] = {
    "EML-201": {
        "email_id": "EML-201",
        "sender_name": "Rakesh Sharma",
        "sender_email": "rakesh.sharma@metromartretail.in",
        "company": "MetroMart Retail Pvt Ltd",
        "subject": "Urgent Quote: 500 Corrugated Boxes + 200 Thermal Label Rolls",
        "body": (
            "Hi Team, We need an urgent wholesale quote for 500 units of 5-ply corrugated shipping boxes "
            "and 200 thermal barcode label rolls delivered to our Peenya, Bengaluru warehouse by Monday. "
            "Please share GST-inclusive pricing and payment terms."
        ),
        "received_at": "10:12 AM",
        "category": "Unprocessed",
        "priority": "Pending",
        "status": "Unread",
    },
    "EML-202": {
        "email_id": "EML-202",
        "sender_name": "Priya Menon",
        "sender_email": "priya@freshkartd2c.com",
        "company": "FreshKart D2C",
        "subject": "Escalation: B2B Pallet AWB-BLR-8821 Stuck at Nelamangala Hub",
        "body": (
            "Hello Ops, Our priority B2B replenishment shipment (AWB-BLR-8821, 45 cartons) has shown no scan "
            "movement at the Nelamangala hub since yesterday evening. Our dark-store launch is tomorrow morning. "
            "Confirm exact delivery ETA before 4:00 PM today."
        ),
        "received_at": "11:05 AM",
        "category": "Unprocessed",
        "priority": "Pending",
        "status": "Unread",
    },
    "EML-203": {
        "email_id": "EML-203",
        "sender_name": "Accounts Desk",
        "sender_email": "accounts@shreebalajipackaging.in",
        "company": "Shree Balaji Packaging",
        "subject": "GST Invoice INV-2026-884 (₹1,42,500) - Payment Reminder",
        "body": (
            "Dear Finance Team, Please find attached GST Invoice INV-2026-884 dated 20-Nov-2026 for ₹1,42,500 "
            "(Base ₹1,20,763 + 18% GST ₹21,737) against PO-7741. Kindly confirm NEFT/RTGS release date before Friday."
        ),
        "received_at": "12:30 PM",
        "category": "Unprocessed",
        "priority": "Pending",
        "status": "Unread",
    },
}

DRAFTS_DB: list[dict[str, Any]] = []
TASKS_DB: list[dict[str, Any]] = []
CHAT_SESSIONS: dict[str, list[types.Content]] = {}
_current_request_tools: list[str] = []


# --- EMAIL AUTOMATION TOOLS ---
def get_inbox_emails() -> dict[str, Any]:
    """Fetch all emails currently in the business inbox along with their triage and reply status."""
    _current_request_tools.append("get_inbox_emails()")
    return {"emails": list(INBOX_DB.values())}


def triage_and_route_email(
    email_id: str,
    category: str,
    priority: str,
    routed_team: str,
    summary: str,
) -> dict[str, Any]:
    """Classify an incoming email, set priority, route it to the right internal team, and create an action task.

    Args:
        email_id: Email ID like EML-201, or 'CUSTOM' for a newly pasted email.
        category: Category ('Sales Quote', 'Urgent Escalation', 'Finance / GST Invoice', 'Support', or 'Spam').
        priority: Priority level ('High', 'Medium', or 'Low').
        routed_team: Internal team ('Sales Desk', 'Logistics Ops', 'Accounts Payable', or 'Customer Success').
        summary: One-line actionable summary of what needs to be done.
    """
    _current_request_tools.append(f"triage_and_route_email({email_id} -> {category})")
    clean_id = email_id.strip().upper()
    if clean_id in INBOX_DB:
        INBOX_DB[clean_id]["category"] = category.strip()
        INBOX_DB[clean_id]["priority"] = priority.strip()
        INBOX_DB[clean_id]["status"] = "Triaged"

    task_id = f"TSK-{random.randint(301, 999)}"
    task = {
        "task_id": task_id,
        "source_email": clean_id,
        "category": category.strip(),
        "priority": priority.strip(),
        "routed_team": routed_team.strip(),
        "summary": summary.strip(),
        "created_at": datetime.now().strftime("%I:%M %p"),
    }
    TASKS_DB.insert(0, task)
    return {"triaged": True, **task}


def draft_email_reply(
    email_id: str,
    recipient_name: str,
    recipient_email: str,
    subject: str,
    reply_body: str,
) -> dict[str, Any]:
    """Generate and queue an executive email reply draft ready for one-click approval or sending.

    Args:
        email_id: Related Email ID (e.g., EML-201 or CUSTOM).
        recipient_name: Name of the recipient.
        recipient_email: Email address of the recipient.
        subject: Reply email subject line (usually starting with Re:).
        reply_body: Complete, polite, ready-to-send email body.
    """
    _current_request_tools.append(f"draft_email_reply({recipient_email})")
    clean_id = email_id.strip().upper()
    if clean_id in INBOX_DB:
        INBOX_DB[clean_id]["status"] = "Reply Drafted"

    draft_id = f"DRF-{random.randint(401, 999)}"
    draft = {
        "draft_id": draft_id,
        "email_id": clean_id,
        "recipient_name": recipient_name.strip(),
        "recipient_email": recipient_email.strip(),
        "subject": subject.strip(),
        "reply_body": reply_body.strip(),
        "status": "Ready to Send",
        "created_at": datetime.now().strftime("%I:%M %p"),
    }
    DRAFTS_DB.insert(0, draft)
    return {"drafted": True, **draft}


def backup_email_engine(user_msg: str) -> str:
    """Zero-downtime backup engine when Gemini cloud models experience 503 high-demand spikes."""
    text = user_msg.strip()
    lower = text.lower()

    if any(k in lower for k in ("list inbox", "show inbox", "all emails", "unread")):
        items = get_inbox_emails()["emails"]
        lines = [
            f"• **{e['email_id']}** [{e['status']}] — {e['sender_name']} ({e['company']}): *{e['subject']}*"
            for e in items
        ]
        return "Here is your current B2B Operations Inbox:\n" + "\n".join(lines)

    # EML-201: Wholesale Quote
    if "eml-201" in lower or ("500" in lower and ("box" in lower or "quote" in lower)):
        em = INBOX_DB["EML-201"]
        t = triage_and_route_email(
            "EML-201",
            "Sales Quote",
            "High",
            "Sales Desk",
            "Send GST-inclusive wholesale quote for 500 5-ply boxes + 200 thermal rolls (Peenya delivery).",
        )
        body = (
            f"Dear {em['sender_name']},\n\n"
            "Thank you for reaching out to ApexTrade Supply Chain. We have confirmed stock availability at our Bengaluru fulfillment center for:\n"
            "• 500x 5-Ply Corrugated Boxes @ ₹42/unit = ₹21,000\n"
            "• 200x Thermal Barcode Label Rolls @ ₹115/roll = ₹23,000\n"
            "• Subtotal: ₹44,000 + 18% GST (₹7,920) = ₹51,920 (Free Peenya warehouse delivery by Monday).\n\n"
            "Please confirm if we should lock this batch for dispatch.\n\n"
            "Warm regards,\nSales Desk | ApexTrade Supply Chain"
        )
        d = draft_email_reply("EML-201", em["sender_name"], em["sender_email"], f"Re: {em['subject']}", body)
        return (
            f"Processed **EML-201** automatically:\n"
            f"• Classification: **{t['category']}** (Priority: **{t['priority']}**)\n"
            f"• Task Routed ({t['task_id']}): {t['routed_team']} — {t['summary']}\n"
            f"• Reply Draft Queued (**{d['draft_id']}**) to `{d['recipient_email']}`:\n\n"
            f"{body}"
        )

    # EML-202: Shipment Escalation
    if "eml-202" in lower or "8821" in lower or "nelamangala" in lower or "escalat" in lower:
        em = INBOX_DB["EML-202"]
        t = triage_and_route_email(
            "EML-202",
            "Urgent Escalation",
            "High",
            "Logistics Ops",
            "Expedite stuck pallet AWB-BLR-8821 (45 cartons) at Nelamangala hub before 4:00 PM.",
        )
        body = (
            f"Hi {em['sender_name']},\n\n"
            "We have flagged shipment AWB-BLR-8821 (45 cartons) as a P1 priority with our Nelamangala hub supervisor. "
            "Your pallet has been cleared for the evening linehaul wave and is scheduled for dark-store delivery tomorrow by 9:30 AM.\n\n"
            "We will share live vehicle driver details before 4:00 PM today.\n\n"
            "Best regards,\nLogistics Control Tower | ApexTrade"
        )
        d = draft_email_reply("EML-202", em["sender_name"], em["sender_email"], f"Re: {em['subject']}", body)
        return (
            f"Escalated **EML-202** & drafted response:\n"
            f"• Classification: **{t['category']}** (Priority: **{t['priority']}**)\n"
            f"• Task Routed ({t['task_id']}): {t['routed_team']} — {t['summary']}\n"
            f"• Reply Draft Queued (**{d['draft_id']}**) to `{d['recipient_email']}`:\n\n"
            f"{body}"
        )

    # EML-203: GST Invoice
    if "eml-203" in lower or "884" in lower or "gst" in lower or "invoice" in lower:
        em = INBOX_DB["EML-203"]
        t = triage_and_route_email(
            "EML-203",
            "Finance / GST Invoice",
            "Medium",
            "Accounts Payable",
            "Verify PO-7741 GRN and schedule NEFT of ₹1,42,500 for INV-2026-884 before Friday.",
        )
        body = (
            "Dear Accounts Team,\n\n"
            "We acknowledge receipt of GST Invoice INV-2026-884 for ₹1,42,500 against PO-7741. "
            "Three-way GRN verification is complete, and your invoice has been queued in Friday's NEFT/RTGS payout batch.\n\n"
            "UTR confirmation will be shared automatically upon release.\n\n"
            "Regards,\nAccounts Payable | ApexTrade"
        )
        d = draft_email_reply("EML-203", em["sender_name"], em["sender_email"], f"Re: {em['subject']}", body)
        return (
            f"Processed Vendor Invoice **EML-203**:\n"
            f"• Classification: **{t['category']}** (Priority: **{t['priority']}**)\n"
            f"• Task Routed ({t['task_id']}): {t['routed_team']} — {t['summary']}\n"
            f"• Reply Draft Queued (**{d['draft_id']}**) to `{d['recipient_email']}`:\n\n"
            f"{body}"
        )

    # Custom Pasted Email
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    recipient = email_match.group(0) if email_match else "client@partnercompany.in"
    t = triage_and_route_email(
        "CUSTOM",
        "Inbound Business Inquiry",
        "High",
        "Operations Desk",
        f"Review & respond to custom email: {text[:75]}...",
    )
    body = (
        "Hello,\n\n"
        "Thank you for your email. We have logged your request with our operations team and prioritized it for immediate action. "
        "A specialist will follow up with exact details within 2 business hours.\n\n"
        "Best regards,\nOperations Desk | ApexTrade"
    )
    d = draft_email_reply("CUSTOM", "Client", recipient, "Re: Your Recent Inquiry", body)
    return (
        f"Triaged your custom email & generated a reply draft:\n"
        f"• Task Created (**{t['task_id']}**): Routed to **{t['routed_team']}** ({t['priority']} Priority)\n"
        f"• Draft Queued (**{d['draft_id']}**):\n\n"
        f"{body}"
    )


SYSTEM_INSTRUCTION = """
You are **ApexMail AI**, an autonomous **Email Triage, Task Routing & Auto-Reply Agent** for **ApexTrade Logistics & Supply Chain (Bengaluru)**.

WORKFLOW RULES:
1. Whenever asked to check the inbox, call `get_inbox_emails`.
2. Whenever asked to process, triage, or reply to an email (`EML-201`, `EML-202`, `EML-203`, or any custom pasted email):
   - First call `triage_and_route_email` to classify the email, assign `High`/`Medium`/`Low` priority, and route an action task to the right internal team (`Sales Desk`, `Logistics Ops`, or `Accounts Payable`).
   - Second, ALWAYS call `draft_email_reply` with a crisp, executive, Indian-business-appropriate reply (using ₹ for currency and GST details where relevant).
3. Present a clean summary showing the **Category**, **Priority**, **Routed Task ID**, **Draft ID**, and the **Ready-to-Send Email Reply**.
"""


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.get("/health")
async def health():
    return {
        "status": "running",
        "models": FALLBACK_MODELS,
        "tasks_count": len(TASKS_DB),
        "drafts_count": len(DRAFTS_DB),
    }


@app.get("/api/workspace")
async def get_workspace():
    return {
        "inbox": list(INBOX_DB.values()),
        "tasks": TASKS_DB,
        "drafts": DRAFTS_DB,
    }


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    global _current_request_tools

    if not GEMINI_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"reply": "Server configuration error: GEMINI_API_KEY is missing.", "tools": []},
        )

    history = CHAT_SESSIONS.setdefault(req.session_id, [])
    user_turn = types.Content(role="user", parts=[types.Part.from_text(text=req.message)])
    conversation = history[-10:] + [user_turn]

    last_error = ""
    for model_name in FALLBACK_MODELS:
        for attempt in range(2):
            _current_request_tools = []
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=conversation,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        tools=[get_inbox_emails, triage_and_route_email, draft_email_reply],
                        temperature=0.2,
                    ),
                )
                reply_text = response.text or "Email processed."
                history.append(user_turn)
                history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply_text)]))
                if len(history) > 12:
                    CHAT_SESSIONS[req.session_id] = history[-12:]
                return {
                    "reply": reply_text,
                    "tools": list(_current_request_tools),
                    "tasks_count": len(TASKS_DB),
                    "drafts_count": len(DRAFTS_DB),
                }
            except Exception as e:
                last_error = str(e)
                is_transient = any(code in last_error for code in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED"))
                if is_transient and attempt == 0:
                    time.sleep(1.5)
                    continue
                break

    if any(code in last_error for code in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND")):
        _current_request_tools = []
        fallback_reply = backup_email_engine(req.message)
        history.append(user_turn)
        history.append(types.Content(role="model", parts=[types.Part.from_text(text=fallback_reply)]))
        return {
            "reply": fallback_reply,
            "tools": list(_current_request_tools),
            "tasks_count": len(TASKS_DB),
            "drafts_count": len(DRAFTS_DB),
        }

    return JSONResponse(
        status_code=500,
        content={"reply": f"Assistant error: {last_error}", "tools": []},
    )


@app.get("/", response_class=HTMLResponse)
async def chat_ui():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0" />
  <title>ApexMail AI — Email Automation</title>
  <style>
    :root {
      --bg: #f1f5f9;
      --card: #ffffff;
      --primary: #1e1b4b;
      --accent: #4f46e5;
      --bot-bg: #f8fafc;
      --border: #e2e8f0;
      --text: #0f172a;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background: var(--bg); color: var(--text); display: flex; justify-content: center; height: 100dvh; }
    .app { width: 100%; max-width: 560px; background: var(--card); display: flex; flex-direction: column; height: 100dvh; box-shadow: 0 0 30px rgba(0,0,0,0.06); }
    .header { background: var(--primary); color: #fff; padding: 14px 16px; display: flex; justify-content: space-between; align-items: center; gap: 8px; }
    .header-left { display: flex; align-items: center; gap: 10px; }
    .avatar { width: 36px; height: 36px; border-radius: 10px; background: var(--accent); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 14px; }
    .title { font-size: 15px; font-weight: 600; }
    .subtitle { font-size: 11px; color: #c7d2fe; display: flex; align-items: center; gap: 5px; margin-top: 2px; }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: #22c55e; display: inline-block; }
    .board-btn { background: rgba(255,255,255,0.14); color: #fff; border: 1px solid rgba(255,255,255,0.25); border-radius: 8px; padding: 7px 10px; font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap; }
    .messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .msg { max-width: 88%; padding: 11px 14px; border-radius: 14px; font-size: 14px; line-height: 1.48; white-space: pre-wrap; word-wrap: break-word; }
    .msg.bot { align-self: flex-start; background: var(--bot-bg); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
    .msg.user { align-self: flex-end; background: var(--primary); color: #fff; border-bottom-right-radius: 4px; }
    .tool-badge { display: inline-block; font-size: 11px; font-weight: 600; color: #3730a3; background: #e0e7ff; padding: 3px 8px; border-radius: 6px; margin-bottom: 6px; }
    .chips { display: flex; gap: 6px; overflow-x: auto; padding: 8px 12px; border-top: 1px solid var(--border); background: #f8fafc; scrollbar-width: none; }
    .chips::-webkit-scrollbar { display: none; }
    .chip { flex-shrink: 0; background: #fff; border: 1px solid #cbd5e1; border-radius: 999px; padding: 6px 11px; font-size: 12px; color: #334155; cursor: pointer; }
    .chip:active { background: #e2e8f0; }
    .input-bar { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--border); background: #fff; }
    .input-bar input { flex: 1; border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 12px; font-size: 14px; outline: none; }
    .input-bar input:focus { border-color: var(--accent); }
    .input-bar button { background: var(--accent); color: #fff; border: none; border-radius: 10px; padding: 0 16px; font-size: 14px; font-weight: 600; cursor: pointer; }
    .input-bar button:disabled { opacity: 0.5; }
    .modal { position: fixed; inset: 0; background: rgba(15,23,42,0.55); display: none; align-items: center; justify-content: center; padding: 14px; z-index: 50; }
    .modal-card { background: #fff; width: 100%; max-width: 520px; max-height: 84vh; border-radius: 14px; padding: 16px; overflow-y: auto; }
    .modal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
    .sec-title { font-size: 12px; font-weight: 700; text-transform: uppercase; color: #64748b; margin: 12px 0 6px; letter-spacing: 0.04em; }
    .card-item { border: 1px solid var(--border); border-radius: 10px; padding: 10px; margin-bottom: 8px; font-size: 13px; background: #f8fafc; }
    .pill { display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 11px; font-weight: 700; background: #e0e7ff; color: #3730a3; }
    .copy-btn { margin-top: 6px; background: #1e1b4b; color: #fff; border: none; border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: 600; cursor: pointer; }
  </style>
</head>
<body>
  <div class="app">
    <div class="header">
      <div class="header-left">
        <div class="avatar">✉️</div>
        <div>
          <div class="title">ApexMail AI Agent</div>
          <div class="subtitle"><span class="dot"></span> Email Triage &bull; Task Routing &bull; Auto-Reply</div>
        </div>
      </div>
      <button class="board-btn" onclick="openBoard()">Tasks (<span id="tCount">0</span>) &bull; Drafts (<span id="dCount">0</span>)</button>
    </div>

    <div class="messages" id="chatBox">
      <div class="msg bot">Hi! I'm <b>ApexMail AI</b>. I monitor business inboxes, classify priority, route tasks to teams, and draft ready-to-send replies:
• <b>EML-201:</b> Wholesale B2B Quote (MetroMart Retail)
• <b>EML-202:</b> Delayed Pallet Escalation (FreshKart D2C)
• <b>EML-203:</b> Vendor GST Invoice ₹1,42,500 (Shree Balaji)

Tap a demo action below or paste any raw email text!</div>
    </div>

    <div class="chips">
      <button class="chip" onclick="quickSend('Triage and draft a wholesale quote reply for EML-201')">1. Quote Reply (EML-201)</button>
      <button class="chip" onclick="quickSend('Escalate delayed shipment EML-202 and draft an urgent update')">2. Escalate Delay (EML-202)</button>
      <button class="chip" onclick="quickSend('Process GST invoice email EML-203 and draft payment confirmation')">3. GST Invoice (EML-203)</button>
      <button class="chip" onclick="quickSend('Show all emails currently in the inbox')">4. View Inbox</button>
    </div>

    <form class="input-bar" onsubmit="sendMsg(event)">
      <input id="msgInput" type="text" placeholder="Process EML-201, or paste any raw email..." autocomplete="off" required />
      <button id="sendBtn" type="submit">Send</button>
    </form>
  </div>

  <div class="modal" id="boardModal" onclick="if(event.target===this)this.style.display='none'">
    <div class="modal-card">
      <div class="modal-head">
        <strong>Inbox, Routed Tasks &amp; Reply Drafts</strong>
        <button onclick="document.getElementById('boardModal').style.display='none'" style="border:none;background:none;font-size:18px;cursor:pointer;">✕</button>
      </div>
      <div class="sec-title">Queued Reply Drafts</div>
      <div id="draftList">Loading...</div>
      <div class="sec-title">Routed Team Tasks</div>
      <div id="taskList">Loading...</div>
      <div class="sec-title">Live Inbox Threads</div>
      <div id="inboxList">Loading...</div>
    </div>
  </div>

  <script>
    const sessionId = 'eml_' + Math.random().toString(36).slice(2, 10);
    const chatBox = document.getElementById('chatBox');
    const msgInput = document.getElementById('msgInput');
    const sendBtn = document.getElementById('sendBtn');
    let cachedDrafts = [];

    function appendMsg(role, text, tools = []) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      if (tools && tools.length > 0) {
        const badge = document.createElement('div');
        badge.className = 'tool-badge';
        badge.textContent = '⚡ Action: ' + tools.join(' → ');
        div.appendChild(badge);
        div.appendChild(document.createElement('br'));
      }
      const span = document.createElement('span');
      span.textContent = text;
      div.appendChild(span);
      chatBox.appendChild(div);
      chatBox.scrollTop = chatBox.scrollHeight;
      return div;
    }

    function quickSend(text) {
      msgInput.value = text;
      sendMsg(new Event('submit'));
    }

    function copyDraft(idx) {
      if (!cachedDrafts[idx]) return;
      navigator.clipboard.writeText(cachedDrafts[idx].reply_body);
      alert('Copied reply email to clipboard!');
    }

    async function sendMsg(e) {
      e.preventDefault();
      const text = msgInput.value.trim();
      if (!text) return;
      msgInput.value = '';
      appendMsg('user', text);
      sendBtn.disabled = true;
      const typing = appendMsg('bot', 'Processing email...');

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId, message: text })
        });
        const data = await res.json();
        typing.remove();
        appendMsg('bot', data.reply, data.tools || []);
        if (typeof data.tasks_count === 'number') {
          document.getElementById('tCount').textContent = data.tasks_count;
        }
        if (typeof data.drafts_count === 'number') {
          document.getElementById('dCount').textContent = data.drafts_count;
        }
      } catch (err) {
        typing.textContent = 'Network error. Please try again.';
      } finally {
        sendBtn.disabled = false;
      }
    }

    async function openBoard() {
      const modal = document.getElementById('boardModal');
      modal.style.display = 'flex';
      const res = await fetch('/api/workspace');
      const data = await res.json();
      cachedDrafts = data.drafts || [];

      document.getElementById('draftList').innerHTML = cachedDrafts.map((d, idx) =>
        `<div class="card-item"><b>${d.draft_id}</b> &bull; <span class="pill">${d.status}</span><br><b>To:</b> ${d.recipient_name} (${d.recipient_email})<br><b>Subject:</b> ${d.subject}<br><pre style="white-space:pre-wrap;font-family:inherit;margin-top:6px;background:#fff;padding:8px;border-radius:6px;border:1px solid #e2e8f0;">${d.reply_body}</pre><button class="copy-btn" onclick="copyDraft(${idx})">Copy Reply</button></div>`
      ).join('') || '<p style="font-size:13px;color:#64748b;">No reply drafts yet. Tap a chip to process an email!</p>';

      document.getElementById('taskList').innerHTML = (data.tasks || []).map(t =>
        `<div class="card-item"><b>${t.task_id}</b> &bull; <span class="pill">${t.routed_team}</span> &bull; <b>${t.priority}</b><br>${t.summary}</div>`
      ).join('') || '<p style="font-size:13px;color:#64748b;">No routed tasks yet.</p>';

      document.getElementById('inboxList').innerHTML = (data.inbox || []).map(e =>
        `<div class="card-item"><b>${e.email_id}</b> &bull; <span class="pill">${e.status}</span><br><b>From:</b> ${e.sender_name} (${e.company})<br><b>Subject:</b> ${e.subject}</div>`
      ).join('');
    }
  </script>
</body>
</html>"""
