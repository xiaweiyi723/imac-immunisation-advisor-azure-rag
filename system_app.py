"""
IMAC Immunisation Guidance Advisor System.

A lightweight clinical advisor workspace backed by the existing Azure AI
Foundry Agent integration in backend.py. This is intentionally built as a
system prototype rather than a Streamlit page: it includes sign-in, case
management, persisted consultations, evidence review, classification, and a
CRM-ready export view.
"""

import html
import json
import os
import re
import secrets
import sqlite3
import sys
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from backend import AzureAgentClient


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "advisor_system.db"
HOST = "127.0.0.1"
PORT = int(os.getenv("ADVISOR_PORT", "8600"))

SESSIONS = {}

USERS = {
    "advisor@imac.local": {
        "password": "advisor123",
        "name": "Clinical Advisor",
        "role": "Clinical Advisor",
    },
    "lead@imac.local": {
        "password": "lead123",
        "name": "Clinical Lead",
        "role": "Clinical Lead",
    },
    "systems@imac.local": {
        "password": "systems123",
        "name": "Systems Reviewer",
        "role": "Systems / Public Health Guidance",
    },
}


def now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def h(value):
    return html.escape(str(value or ""), quote=True)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                advisor_email TEXT NOT NULL,
                advisor_name TEXT NOT NULL,
                caller_name TEXT,
                caller_org TEXT,
                channel TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                category TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                source_count INTEGER DEFAULT 0,
                crm_reference TEXT,
                safety_note TEXT
            );

            CREATE TABLE IF NOT EXISTS case_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                file_name TEXT,
                file_id TEXT,
                score TEXT,
                quote TEXT,
                FOREIGN KEY(case_id) REFERENCES cases(id)
            );
            """
        )
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(cases)").fetchall()
        }
        new_columns = {
            "submitted_at": "TEXT",
            "reviewed_at": "TEXT",
            "reviewed_by": "TEXT",
            "approval_note": "TEXT",
            "source_review_status": "TEXT DEFAULT 'Not reviewed'",
            "source_reviewed_at": "TEXT",
            "source_reviewed_by": "TEXT",
            "source_note": "TEXT",
            "pii_redacted_count": "INTEGER DEFAULT 0",
            "pii_redaction_summary": "TEXT",
            "original_question_preview": "TEXT",
        }
        for name, definition in new_columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE cases ADD COLUMN {name} {definition}")
        conn.execute(
            "UPDATE cases SET status = 'Draft' WHERE status = 'Draft for review'"
        )
        conn.execute(
            "UPDATE cases SET source_review_status = 'Not reviewed' WHERE source_review_status IS NULL"
        )


def classify_question(question):
    text = question.lower()
    if any(term in text for term in ["influenza", "flu"]):
        return "Influenza"
    if any(term in text for term in ["mmr", "measles", "mumps", "rubella"]):
        return "MMR"
    if any(term in text for term in ["covid", "sars-cov-2"]):
        return "COVID-19"
    if any(term in text for term in ["pregnan", "maternal"]):
        return "Pregnancy / Maternal"
    if any(term in text for term in ["infant", "baby", "child", "children"]):
        return "Infants / Children"
    return "General Immunisation"


def redact_pii(text):
    """Redact common PII before retrieval, generation, and storage."""
    patterns = [
        ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]"),
        ("phone", r"(?<!\d)(?:\+?64|0)\s?\d{1,3}(?:[\s-]?\d){5,9}(?!\d)", "[REDACTED_PHONE]"),
        ("nhi", r"\b[A-HJ-NP-Z]{3}\d{4}\b", "[REDACTED_NHI]"),
        ("date_of_birth", r"\b(?:DOB|date of birth)\s*[:\-]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "[REDACTED_DOB]"),
    ]
    redacted = text or ""
    findings = []
    for label, pattern, replacement in patterns:
        redacted, count = re.subn(pattern, replacement, redacted, flags=re.IGNORECASE)
        if count:
            findings.append(f"{label}:{count}")
    return redacted.strip(), findings


def load_evaluation_samples(limit=12):
    path = Path(r"C:\Users\惠普\Desktop\evaluation_cases.txt")
    if not path.exists():
        return []
    samples = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if 45 <= len(cleaned) <= 260 and "?" in cleaned:
            samples.append(cleaned)
        if len(samples) >= limit:
            break
    if samples:
        return samples
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(line.strip()) > 45
    ][:limit]


def is_clinical_advisor(user):
    return user.get("role") == "Clinical Advisor"


def is_clinical_lead(user):
    return user.get("role") == "Clinical Lead"


def is_systems_reviewer(user):
    return "Systems" in user.get("role", "")


def role_home_title(user):
    if is_clinical_lead(user):
        return "Clinical lead review queue"
    if is_systems_reviewer(user):
        return "Source and system review queue"
    return "Advisor dashboard"


def role_home_subtitle(user):
    if is_clinical_lead(user):
        return "Approve advisor drafts, request changes, and close reviewed immunisation consultation cases."
    if is_systems_reviewer(user):
        return "Review evidence quality, source status, and knowledge base readiness without making clinical approvals."
    return "Create consultations, submit draft answers for clinical approval, and track feedback."


def render_layout(title, user, body, active="dashboard"):
    nav_items = [
        ("dashboard", "/", "Dashboard"),
        ("cases", "/cases", "Case register"),
        ("updates", "/updates", "Clinical updates"),
        ("requests", "/common-requests", "Common requests"),
        ("knowledge", "/knowledge-base", "Knowledge base"),
        ("evaluation", "/evaluation", "Evaluation"),
        ("pii", "/pii-tools", "PII tools"),
    ]
    if not (user and is_systems_reviewer(user)):
        nav_items.insert(1, ("new", "/cases/new", "New consultation"))
    if user and is_clinical_lead(user):
        nav_items.insert(2, ("approvals", "/approvals", "Approvals"))
    nav = "".join(
        f"<a class='nav-item {'active' if key == active else ''}' href='{href}'>{label}</a>"
        for key, href, label in nav_items
    )
    user_block = ""
    if user:
        user_block = f"""
        <div class="user-card">
            <div class="user-name">{h(user['name'])}</div>
            <div class="user-role">{h(user['role'])}</div>
            <a class="muted-link" href="/logout">Sign out</a>
        </div>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{h(title)}</title>
  <style>
    :root {{
      --ink: #17212f;
      --muted: #627386;
      --line: #d7dde5;
      --panel: #ffffff;
      --bg: #f4f7fa;
      --brand: #19547a;
      --brand-2: #247b63;
      --warn: #a15c00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    .shell {{
      display: grid;
      grid-template-columns: 270px 1fr;
      min-height: 100vh;
    }}
    .sidebar {{
      background: #102f46;
      color: white;
      padding: 24px 18px;
    }}
    .brand {{
      font-size: 20px;
      font-weight: 700;
      line-height: 1.25;
      margin-bottom: 8px;
    }}
    .brand-sub {{
      color: #c4d4df;
      font-size: 13px;
      margin-bottom: 24px;
    }}
    .nav-item {{
      display: block;
      color: #d9e5ed;
      text-decoration: none;
      padding: 11px 12px;
      border-radius: 6px;
      margin: 4px 0;
      font-size: 14px;
    }}
    .nav-item.active, .nav-item:hover {{
      background: rgba(255,255,255,0.12);
      color: white;
    }}
    .user-card {{
      border-top: 1px solid rgba(255,255,255,0.18);
      margin-top: 28px;
      padding-top: 16px;
      font-size: 13px;
    }}
    .user-name {{ font-weight: 700; margin-bottom: 3px; }}
    .user-role {{ color: #c4d4df; margin-bottom: 10px; }}
    .muted-link {{ color: #cce5ff; text-decoration: none; }}
    main {{ padding: 26px 34px 42px; }}
    .topline {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 20px;
      margin-bottom: 20px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 26px; }}
    h2 {{ font-size: 18px; margin: 0 0 12px; }}
    .subtitle {{ color: var(--muted); max-width: 780px; }}
    .grid {{ display: grid; gap: 16px; }}
    .grid-3 {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .grid-2 {{ grid-template-columns: minmax(0, 1.25fr) minmax(340px, .75fr); }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }}
    .metric {{
      font-size: 30px;
      font-weight: 700;
      color: var(--brand);
      margin-bottom: 4px;
    }}
    .metric-label {{ color: var(--muted); font-size: 13px; }}
    .hero {{
      background: linear-gradient(135deg, #102f46 0%, #19547a 55%, #247b63 100%);
      color: white;
      border-radius: 8px;
      padding: 26px;
      margin-bottom: 16px;
    }}
    .hero h1 {{ color: white; font-size: 30px; margin-bottom: 10px; }}
    .hero .subtitle {{ color: #d7e7ef; max-width: 900px; }}
    .pill-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }}
    .pill {{
      border: 1px solid rgba(255,255,255,0.35);
      border-radius: 999px;
      padding: 6px 10px;
      color: white;
      font-size: 12px;
      background: rgba(255,255,255,0.08);
    }}
    .mini-title {{
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .6px;
      margin-bottom: 8px;
    }}
    .process {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }}
    .step {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 130px;
    }}
    .step-number {{
      width: 26px;
      height: 26px;
      border-radius: 50%;
      background: #e9f3ee;
      color: var(--brand-2);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      margin-bottom: 10px;
    }}
    .list-clean {{
      margin: 0;
      padding-left: 18px;
      color: #314256;
      line-height: 1.55;
      font-size: 14px;
    }}
    .source-note {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 12px;
    }}
    .preview-box {{
      border: 1px dashed #b6c3cf;
      background: #f8fbfd;
      border-radius: 8px;
      padding: 12px;
      color: #314256;
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      text-align: left;
      padding: 12px 14px;
      border-bottom: 1px solid #e7ebf0;
      vertical-align: top;
      font-size: 14px;
    }}
    th {{ background: #edf3f7; color: #334155; font-size: 12px; text-transform: uppercase; }}
    tr:last-child td {{ border-bottom: 0; }}
    .tag {{
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      background: #e9f3ee;
      color: #20583b;
      font-size: 12px;
      white-space: nowrap;
    }}
    .tag.warn {{ background: #fff3dd; color: var(--warn); }}
    .tag.neutral {{ background: #edf1f5; color: #526070; }}
    .button, button {{
      display: inline-flex;
      border: 0;
      background: var(--brand);
      color: white;
      padding: 10px 14px;
      border-radius: 6px;
      text-decoration: none;
      font-weight: 700;
      cursor: pointer;
      font-size: 14px;
    }}
    .button.secondary, button.secondary {{ background: #e7edf2; color: #1f3345; }}
    label {{ display: block; font-size: 13px; font-weight: 700; margin-bottom: 6px; }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 11px;
      font: inherit;
      background: white;
    }}
    textarea {{ min-height: 160px; resize: vertical; }}
    .form-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .full {{ grid-column: 1 / -1; }}
    .answer {{
      line-height: 1.58;
      white-space: pre-wrap;
    }}
    .evidence {{
      border-left: 3px solid var(--brand-2);
      padding-left: 12px;
      margin-bottom: 14px;
      color: #314256;
      font-size: 14px;
      line-height: 1.45;
    }}
    .evidence-title {{ font-weight: 700; color: var(--ink); margin-bottom: 4px; }}
    .evidence-meta {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
    .notice {{
      background: #fff8e8;
      border: 1px solid #f1d99e;
      color: #684400;
      padding: 12px 14px;
      border-radius: 6px;
      font-size: 14px;
      margin-bottom: 16px;
    }}
    .login {{
      max-width: 920px;
      margin: 8vh auto;
    }}
    .login .card {{ padding: 26px; }}
    .login-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      align-items: start;
    }}
    .help {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }}
    @media (max-width: 900px) {{
      .shell {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: static; }}
      .grid-2, .grid-3, .form-grid, .process, .login-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">IMAC Guidance Advisor</div>
      <div class="brand-sub">Clinical consultation workspace</div>
      {nav}
      {user_block}
    </aside>
    <main>{body}</main>
  </div>
</body>
</html>"""


def login_page(error=""):
    error_html = f"<div class='notice'>{h(error)}</div>" if error else ""
    body = f"""
    <div class="login">
      <div class="hero">
        <h1>IMAC Guidance Advisor</h1>
        <div class="subtitle">A source-grounded clinical advisor workspace for common immunisation guidance questions, case classification, and CRM-ready consultation records.</div>
        <div class="pill-row">
          <span class="pill">Advisor prompt</span>
          <span class="pill">Approved guidance retrieval</span>
          <span class="pill">Cited draft answer</span>
          <span class="pill">Human clinical review</span>
        </div>
      </div>
      <div class="login-grid">
        <div class="card">
          <h1>Sign in</h1>
          <p class="subtitle">Use a demo clinical advisor account to access the consultation workspace.</p>
          {error_html}
          <form method="post" action="/login">
            <p>
              <label>Email</label>
              <input name="email" value="advisor@imac.local" autocomplete="username">
            </p>
            <p>
              <label>Password</label>
              <input name="password" type="password" value="advisor123" autocomplete="current-password">
            </p>
            <button type="submit">Sign in</button>
          </form>
          <p class="help">Demo accounts: advisor@imac.local / advisor123, lead@imac.local / lead123, systems@imac.local / systems123.</p>
        </div>
        <div class="card">
          <h1>Register demo user</h1>
          <p class="subtitle">Create a temporary PoC account for presentation walkthroughs. Production would use IMAC/UoA SSO and role-based access.</p>
          <form method="post" action="/register">
            <p>
              <label>Name</label>
              <input name="name" placeholder="e.g. Demo Advisor">
            </p>
            <p>
              <label>Email</label>
              <input name="email" placeholder="demo@imac.local">
            </p>
            <p>
              <label>Role</label>
              <select name="role">
                <option>Clinical Advisor</option>
                <option>Clinical Lead</option>
                <option>Systems / Public Health Guidance</option>
              </select>
            </p>
            <p>
              <label>Password</label>
              <input name="password" type="password" placeholder="Minimum 6 characters">
            </p>
            <button type="submit">Create demo account</button>
          </form>
        </div>
      </div>
    </div>
    """
    return render_layout("Sign in | IMAC Guidance Advisor", None, body, active="")


def clinical_updates_page(user):
    updates = [
        ("Today", "Source review queue", "3 guidance snippets were returned with low confidence and should be reviewed before reuse.", "Review"),
        ("This week", "Influenza season queries", "Influenza vaccine eligibility and infant-related questions are trending in recent consultations.", "Trend"),
        ("This week", "Handbook source check", "Immunisation Handbook references are available in the evidence panel for generated case answers.", "Source"),
        ("Reminder", "Clinical safety", "Generated answers remain advisor draft material. Confirm the evidence before communicating advice.", "Safety"),
    ]
    rows = "".join(
        f"<tr><td>{h(date)}</td><td>{h(title)}</td><td>{h(text)}</td><td><span class='tag neutral'>{h(kind)}</span></td></tr>"
        for date, title, text, kind in updates
    )
    body = f"""
    <div class="topline">
      <div>
        <h1>Clinical updates</h1>
        <div class="subtitle">A daily view of guidance signals, query trends, source review items, and safety reminders for the advisor team.</div>
      </div>
      <a class="button" href="/cases/new">Start consultation</a>
    </div>
    <div class="grid grid-3">
      <div class="card"><div class="metric">4</div><div class="metric-label">Active update items</div></div>
      <div class="card"><div class="metric">3</div><div class="metric-label">Source review prompts</div></div>
      <div class="card"><div class="metric">1</div><div class="metric-label">Safety reminder</div></div>
    </div>
    <div style="height:16px"></div>
    <div class="card">
      <h2>Advisor bulletin</h2>
      <table>
        <thead><tr><th>Date</th><th>Item</th><th>Summary</th><th>Type</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="source-note">PoC note: this page uses local system signals and curated reminder text. Production could connect to IMAC content updates and Health NZ guidance feeds.</div>
    </div>
    """
    return render_layout("Clinical updates | IMAC Guidance Advisor", user, body, "updates")


def common_requests_page(user):
    requests = [
        ("Influenza vaccine for infants", "Can infants under 6 months receive influenza vaccine?", "Influenza", "High"),
        ("MMR catch-up", "What is the recommended approach for delayed MMR vaccination?", "MMR", "High"),
        ("Pregnancy vaccination", "Which immunisations are recommended or contraindicated during pregnancy?", "Pregnancy / Maternal", "Medium"),
        ("COVID-19 booster timing", "When should a patient receive a booster after infection or previous dose?", "COVID-19", "Medium"),
        ("Travel-related vaccination", "Which sources should be checked for travel vaccine advice?", "General Immunisation", "Low"),
        ("Adverse event follow-up", "What information should be captured when a caller reports a possible vaccine reaction?", "General Immunisation", "Medium"),
    ]
    rows = "".join(
        f"""
        <tr>
          <td>{h(topic)}</td>
          <td>{h(question)}</td>
          <td>{h(category)}</td>
          <td><span class="tag {'warn' if priority == 'High' else 'neutral'}">{h(priority)}</span></td>
          <td><a href="/cases/new">Use prompt</a></td>
        </tr>
        """
        for topic, question, category, priority in requests
    )
    body = f"""
    <div class="topline">
      <div>
        <h1>Common requests</h1>
        <div class="subtitle">Frequently handled immunisation enquiry patterns that advisors can use as a starting point for source-backed consultations.</div>
      </div>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>Topic</th><th>Example advisor prompt</th><th>Category</th><th>Priority</th><th>Action</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <div style="height:16px"></div>
    <div class="grid grid-3">
      <div class="card"><div class="mini-title">How to use</div><p>Copy the prompt idea into a new consultation and add caller-specific context.</p></div>
      <div class="card"><div class="mini-title">Source expectation</div><p>Answers should show evidence from approved guidance before advisor communication.</p></div>
      <div class="card"><div class="mini-title">Escalation</div><p>Ambiguous, complex, or unsupported questions should move to clinical lead review.</p></div>
    </div>
    """
    return render_layout("Common requests | IMAC Guidance Advisor", user, body, "requests")


def knowledge_base_page(user):
    sources = [
        ("Health NZ Immunisation Handbook", "Official guidance", "Indexed in Azure AI Search", "Primary source for New Zealand immunisation clinical guidance"),
        ("IMAC website", "Public clinical resource", "Indexed in Azure AI Search", "Vaccine FAQs, summaries, and advisor-facing guidance"),
        ("Medsafe resources", "Regulatory source", "Indexed in Azure AI Search", "Approved product and safety information where relevant"),
        ("Pharmac vaccine funding", "Funding source", "Indexed in Azure AI Search", "Funding decisions and funded vaccine context"),
        ("evaluation_cases.txt", "Anonymised call/question set", "Indexed in Azure AI Search", "Used for common-question patterns and evaluation, not as clinical authority"),
        ("Foundry Agent File Search", "Attached agent files", "Connected", "Existing 3-file retrieval tool used by the Foundry Agent"),
    ]
    rows = "".join(
        f"<tr><td>{h(name)}</td><td>{h(kind)}</td><td><span class='tag {'warn' if status == 'Draft' else 'neutral'}'>{h(status)}</span></td><td>{h(note)}</td></tr>"
        for name, kind, status, note in sources
    )
    body = f"""
    <div class="topline">
      <div>
        <h1>Knowledge base</h1>
        <div class="subtitle">A real Azure AI Search knowledge index plus the existing Foundry Agent File Search attachments. Official sources are used for citations; evaluation_cases supports common-question discovery and testing.</div>
      </div>
    </div>
    <div class="grid grid-3">
      <div class="card"><div class="metric">2795</div><div class="metric-label">Azure Search chunks indexed</div></div>
      <div class="card"><div class="metric">6</div><div class="metric-label">Source groups available</div></div>
      <div class="card"><div class="metric">3</div><div class="metric-label">Foundry File Search attachments</div></div>
    </div>
    <div style="height:16px"></div>
    <div class="card">
      <h2>Source catalogue</h2>
      <table>
        <thead><tr><th>Source</th><th>Type</th><th>Status</th><th>Use in system</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="source-note">Azure AI Search-backed evidence retrieval is enabled when configured. No new web hosting service is used.</div>
    </div>
    """
    return render_layout("Knowledge base | IMAC Guidance Advisor", user, body, "knowledge")


def evaluation_page(user, query="", results=None, error=""):
    samples = load_evaluation_samples()
    sample_rows = "".join(
        f"<tr><td>{h(sample)}</td><td><form method='post' action='/evaluation'><input type='hidden' name='query' value='{h(sample)}'><button type='submit'>Run search</button></form></td></tr>"
        for sample in samples[:8]
    )
    result_html = ""
    if error:
        result_html = f"<div class='notice'>{h(error)}</div>"
    elif results is not None:
        if results:
            rows = "".join(
                f"""
                <tr>
                  <td>{h(src.get('file_name'))}</td>
                  <td><span class="tag neutral">{h(src.get('type'))}</span></td>
                  <td>{h(src.get('score'))}</td>
                  <td>{h(src.get('quote'))[:360]}</td>
                </tr>
                """
                for src in results
            )
            result_html = f"""
            <div class="card">
              <h2>Azure Search retrieval result</h2>
              <table>
                <thead><tr><th>Source</th><th>Type</th><th>Score</th><th>Snippet</th></tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </div>
            """
        else:
            result_html = "<div class='notice'>No Azure Search source was returned for this evaluation query.</div>"

    body = f"""
    <div class="topline">
      <div>
        <h1>Evaluation</h1>
        <div class="subtitle">Run real retrieval checks against Azure AI Search using anonymised evaluation cases. This tests source coverage without calling the language model.</div>
      </div>
    </div>
    <form method="post" action="/evaluation" class="card">
      <p>
        <label>Evaluation question</label>
        <textarea name="query" placeholder="Paste an anonymised call question or use one sample below">{h(query)}</textarea>
      </p>
      <button type="submit">Run Azure Search retrieval</button>
    </form>
    <div style="height:16px"></div>
    {result_html}
    <div style="height:16px"></div>
    <div class="card">
      <h2>Samples from evaluation_cases.txt</h2>
      <table>
        <thead><tr><th>Anonymised question sample</th><th>Action</th></tr></thead>
        <tbody>{sample_rows}</tbody>
      </table>
    </div>
    """
    return render_layout("Evaluation | IMAC Guidance Advisor", user, body, "evaluation")


def pii_tools_page(user, text="", redacted="", findings=None):
    findings = findings or []
    result_html = ""
    if text:
        result_html = f"""
        <div class="card">
          <h2>Server-side redaction result</h2>
          <p><strong>Detected:</strong> {h(', '.join(findings) if findings else 'No PII patterns detected')}</p>
          <div class="preview-box">{h(redacted)}</div>
        </div>
        <div style="height:16px"></div>
        """
    body = f"""
    <div class="topline">
      <div>
        <h1>PII redaction test</h1>
        <div class="subtitle">Test the same server-side redaction used before Azure Search and Foundry Agent calls.</div>
      </div>
    </div>
    {result_html}
    <form method="post" action="/pii-tools" class="card">
      <p>
        <label>Text to test</label>
        <textarea name="text" placeholder="Example: Caller email test@example.com, phone 021 123 4567, NHI ABC1234, DOB 12/03/1980.">{h(text)}</textarea>
      </p>
      <button type="submit">Run PII redaction</button>
    </form>
    """
    return render_layout("PII redaction test | IMAC Guidance Advisor", user, body, "evaluation")


def dashboard_page(user):
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        open_count = conn.execute("SELECT COUNT(*) FROM cases WHERE status != 'Closed'").fetchone()[0]
        evidence_count = conn.execute("SELECT COALESCE(SUM(source_count), 0) FROM cases").fetchone()[0]
        pending_approval = conn.execute("SELECT COUNT(*) FROM cases WHERE status = 'Pending clinical approval'").fetchone()[0]
        source_review = conn.execute("SELECT COUNT(*) FROM cases WHERE COALESCE(source_review_status, 'Not reviewed') != 'Reviewed'").fetchone()[0]
        if is_clinical_lead(user):
            rows = conn.execute(
                "SELECT * FROM cases WHERE status IN ('Pending clinical approval', 'Changes requested', 'Approved') ORDER BY updated_at DESC LIMIT 8"
            ).fetchall()
        elif is_systems_reviewer(user):
            rows = conn.execute(
                "SELECT * FROM cases WHERE source_count > 0 ORDER BY updated_at DESC LIMIT 8"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cases WHERE advisor_email = ? ORDER BY id DESC LIMIT 8",
                (user["email"],),
            ).fetchall()

    recent = render_case_table(rows, user)
    lead_metric = pending_approval if is_clinical_lead(user) else open_count
    source_metric = source_review if is_systems_reviewer(user) else evidence_count
    action_label = "Review approvals" if is_clinical_lead(user) else ("Review sources" if is_systems_reviewer(user) else "New consultation")
    action_href = "/approvals" if is_clinical_lead(user) else ("/cases" if is_systems_reviewer(user) else "/cases/new")
    body = f"""
    <div class="topline">
      <div>
        <h1>{h(role_home_title(user))}</h1>
        <div class="subtitle">{h(role_home_subtitle(user))}</div>
      </div>
      <a class="button" href="{action_href}">{action_label}</a>
    </div>

    <div class="grid grid-3">
      <div class="card"><div class="metric">{total}</div><div class="metric-label">Total consultations</div></div>
      <div class="card"><div class="metric">{lead_metric}</div><div class="metric-label">{'Pending approvals' if is_clinical_lead(user) else 'Open / review cases'}</div></div>
      <div class="card"><div class="metric">{source_metric}</div><div class="metric-label">{'Source reviews due' if is_systems_reviewer(user) else 'Evidence snippets captured'}</div></div>
    </div>

    <div style="height:16px"></div>
    <div class="card">
      <h2>{'Approval queue' if is_clinical_lead(user) else ('Source review queue' if is_systems_reviewer(user) else 'My consultations')}</h2>
      {recent}
    </div>
    """
    return render_layout("Dashboard | IMAC Guidance Advisor", user, body, "dashboard")


def render_case_table(rows, user=None):
    if not rows:
        return "<p class='help'>No consultations have been created yet.</p>"
    body = ""
    for row in rows:
        action = f"<a href='/cases/{row['id']}'>Open</a>"
        if user and is_clinical_lead(user) and row["status"] == "Pending clinical approval":
            action = f"<a class='button' href='/cases/{row['id']}'>Review now</a>"
        elif user and is_systems_reviewer(user) and (row["source_review_status"] or "Not reviewed") != "Reviewed":
            action = f"<a class='button secondary' href='/cases/{row['id']}'>Review source</a>"
        body += f"""
        <tr>
          <td><a href="/cases/{row['id']}">IMAC-{row['id']:04d}</a></td>
          <td>{h(row['category'])}</td>
          <td>{h(row['caller_org']) or '-'}</td>
          <td>{h(row['question'])[:120]}</td>
          <td><span class="tag {'warn' if row['priority'] == 'High' else 'neutral'}">{h(row['priority'])}</span></td>
          <td><span class="tag">{h(row['status'])}</span></td>
          <td>{h(row['updated_at'][:10])}</td>
          <td>{action}</td>
        </tr>
        """
    return f"""
    <table>
      <thead><tr><th>Case</th><th>Category</th><th>Organisation</th><th>Question</th><th>Priority</th><th>Status</th><th>Updated</th><th>Action</th></tr></thead>
      <tbody>{body}</tbody>
    </table>
    """


def cases_page(user):
    with db() as conn:
        if is_clinical_lead(user):
            rows = conn.execute(
                "SELECT * FROM cases ORDER BY CASE status WHEN 'Pending clinical approval' THEN 0 WHEN 'Changes requested' THEN 1 WHEN 'Approved' THEN 2 ELSE 3 END, updated_at DESC"
            ).fetchall()
            subtitle = "Clinical lead queue for approving, returning, or closing advisor draft answers."
        elif is_systems_reviewer(user):
            rows = conn.execute(
                "SELECT * FROM cases WHERE source_count > 0 ORDER BY CASE COALESCE(source_review_status, 'Not reviewed') WHEN 'Issue flagged' THEN 0 WHEN 'Not reviewed' THEN 1 ELSE 2 END, updated_at DESC"
            ).fetchall()
            subtitle = "Source review queue for checking evidence quality and indexing issues."
        else:
            rows = conn.execute(
                "SELECT * FROM cases WHERE advisor_email = ? ORDER BY id DESC",
                (user["email"],),
            ).fetchall()
            subtitle = "Your consultation records, draft status, review feedback, and CRM exports."
    body = f"""
    <div class="topline">
      <div>
        <h1>Case register</h1>
        <div class="subtitle">{h(subtitle)}</div>
      </div>
      <a class="button" href="/cases/new">New consultation</a>
    </div>
    {render_case_table(rows, user)}
    """
    return render_layout("Case register | IMAC Guidance Advisor", user, body, "cases")


def approvals_page(user):
    if not is_clinical_lead(user):
        body = """
        <div class="card">
          <h1>Approvals unavailable</h1>
          <p class="subtitle">Clinical approvals are restricted to the Clinical Lead role.</p>
          <a class="button" href="/">Back to dashboard</a>
        </div>
        """
        return render_layout("Approvals unavailable | IMAC Guidance Advisor", user, body, "dashboard")

    with db() as conn:
        pending = conn.execute(
            "SELECT * FROM cases WHERE status = 'Pending clinical approval' ORDER BY submitted_at DESC, updated_at DESC"
        ).fetchall()
        returned = conn.execute(
            "SELECT * FROM cases WHERE status = 'Changes requested' ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()

    pending_table = render_case_table(pending, user)
    returned_table = render_case_table(returned, user)
    body = f"""
    <div class="topline">
      <div>
        <h1>Clinical approvals</h1>
        <div class="subtitle">This is where the Clinical Lead approves advisor draft answers or sends them back for revision.</div>
      </div>
    </div>
    <div class="notice">Open a pending case and use the approval panel at the top of the case page: Approve answer or Request changes.</div>
    <div class="card">
      <h2>Pending clinical approval</h2>
      {pending_table}
    </div>
    <div style="height:16px"></div>
    <div class="card">
      <h2>Returned to advisor</h2>
      {returned_table}
    </div>
    """
    return render_layout("Clinical approvals | IMAC Guidance Advisor", user, body, "approvals")


def new_case_page(user, error=""):
    if is_systems_reviewer(user):
        body = """
        <div class="card">
          <h1>New consultation unavailable</h1>
          <p class="subtitle">Systems reviewers manage source quality and knowledge base readiness. Clinical consultations are created by clinical advisors or clinical leads.</p>
          <a class="button" href="/cases">Open source review queue</a>
        </div>
        """
        return render_layout("New consultation unavailable | IMAC Guidance Advisor", user, body, "cases")
    error_html = f"<div class='notice'>{h(error)}</div>" if error else ""
    body = f"""
    <div class="topline">
      <div>
        <h1>New consultation</h1>
        <div class="subtitle">Manually capture a phone or email enquiry, retrieve from Azure AI Search plus Foundry File Search, and retain source evidence for clinical review.</div>
      </div>
    </div>
    {error_html}
    <form method="post" action="/cases/new" class="card">
      <div class="form-grid">
        <p>
          <label>Health professional / caller</label>
          <input name="caller_name" placeholder="Optional">
        </p>
        <p>
          <label>Organisation</label>
          <input name="caller_org" placeholder="Practice, clinic, DHB, pharmacy...">
        </p>
        <p>
          <label>Channel</label>
          <select name="channel">
            <option>Phone</option>
            <option>Email</option>
            <option>CRM</option>
            <option>Internal review</option>
          </select>
        </p>
        <p>
          <label>Priority</label>
          <select name="priority">
            <option>Normal</option>
            <option>High</option>
            <option>Low</option>
          </select>
        </p>
        <p class="full">
          <label>Consultation question</label>
          <textarea id="question-input" name="question" placeholder="Example: Can infants under 6 months receive influenza vaccine? Caller email test@example.com, phone 021 123 4567, NHI ABC1234, DOB 12/03/1980."></textarea>
        </p>
      </div>
      <div class="preview-box" id="pii-preview">PII redaction preview: type an email, NZ phone number, NHI-like ID, or DOB pattern to see it redacted before submission.</div>
      <div style="height:12px"></div>
      <button type="submit">Generate and submit for approval</button>
    </form>
    <div style="height:16px"></div>
    <div class="notice">PoC boundary: this system does not connect to a live phone or mailbox yet. Advisors enter phone/email questions manually. Live call or mailbox ingestion would require approved Microsoft Graph, Azure Communication Services, or Amazon Connect integration.</div>
    <script>
      const q = document.getElementById('question-input');
      const p = document.getElementById('pii-preview');
      function redactPreview(value) {{
        const rules = [
          ['email', /\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{{2,}}\\b/g, '[REDACTED_EMAIL]'],
          ['phone', /(?<!\\d)(?:\\+?64|0)\\s?\\d{{1,3}}(?:[\\s-]?\\d){{5,9}}(?!\\d)/g, '[REDACTED_PHONE]'],
          ['nhi', /\\b[A-HJ-NP-Z]{{3}}\\d{{4}}\\b/gi, '[REDACTED_NHI]'],
          ['date_of_birth', /\\b(?:DOB|date of birth)\\s*[:\\-]?\\s*\\d{{1,2}}[/-]\\d{{1,2}}[/-]\\d{{2,4}}\\b/gi, '[REDACTED_DOB]']
        ];
        let out = value || '';
        const found = [];
        for (const [name, regex, replacement] of rules) {{
          let count = 0;
          out = out.replace(regex, () => {{
            count += 1;
            return replacement;
          }});
          if (count) found.push(`${{name}}:${{count}}`);
        }}
        return {{ out, found }};
      }}
      function updatePreview() {{
        const result = redactPreview(q.value);
        if (!q.value.trim()) {{
          p.textContent = 'PII redaction preview: type an email, NZ phone number, NHI-like ID, or DOB pattern to see it redacted before submission.';
        }} else if (result.found.length) {{
          p.textContent = 'Detected PII patterns: ' + result.found.join(', ') + '\\n\\nRedacted question sent to Azure Search / Foundry Agent:\\n' + result.out;
        }} else {{
          p.textContent = 'No PII pattern detected. The question will be submitted as entered.';
        }}
      }}
      q.addEventListener('input', updatePreview);
    </script>
    """
    return render_layout("New consultation | IMAC Guidance Advisor", user, body, "new")


def create_case(user, form):
    if is_systems_reviewer(user):
        return None, "Systems reviewers cannot create clinical consultation cases."
    question = (form.get("question", [""])[0] or "").strip()
    if not question:
        return None, "Please enter a consultation question."
    redacted_question, pii_findings = redact_pii(question)
    original_preview = question[:220]

    agent = AzureAgentClient()
    result = agent.call_agent(redacted_question)
    if not result.get("success"):
        return None, result.get("error", "Agent call failed.")

    category = classify_question(redacted_question)
    safety_note = "Advisor review required before communicating advice to the caller."
    ts = now_iso()
    sources = result.get("sources", [])
    has_verified_sources = bool(sources)
    initial_status = "Pending clinical approval" if has_verified_sources else "Source review required"
    approval_note = (
        "Automatically submitted for clinical lead approval after verified sources were retrieved."
        if has_verified_sources
        else "No verified Azure Search or File Search source was returned. This draft cannot be approved until sources are reviewed."
    )
    safety_note = (
        "Advisor and clinical lead review required before communicating advice to the caller."
        if has_verified_sources
        else "No verified source returned. This answer cannot be approved or used as advice until source review is completed."
    )

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO cases (
                created_at, updated_at, advisor_email, advisor_name, caller_name,
                caller_org, channel, priority, status, category, question, answer,
                source_count, crm_reference, safety_note, source_review_status,
                submitted_at, approval_note, pii_redacted_count,
                pii_redaction_summary, original_question_preview
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                ts,
                user["email"],
                user["name"],
                form.get("caller_name", [""])[0],
                form.get("caller_org", [""])[0],
                form.get("channel", ["Phone"])[0],
                form.get("priority", ["Normal"])[0],
                initial_status,
                category,
                redacted_question,
                result.get("response", ""),
                len(sources),
                "",
                safety_note,
                "Not reviewed",
                ts,
                approval_note,
                len(pii_findings),
                ", ".join(pii_findings) if pii_findings else "No PII patterns detected",
                original_preview if pii_findings else "",
            ),
        )
        case_id = cur.lastrowid
        for source in sources:
            conn.execute(
                """
                INSERT INTO case_sources (case_id, file_name, file_id, score, quote)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    source.get("file_name", ""),
                    source.get("file_id", ""),
                    source.get("score", ""),
                    source.get("quote", ""),
                ),
            )
    return case_id, None


def case_action_panel(user, case):
    case_id = case["id"]
    note = h(case["approval_note"] or "")
    source_note = h(case["source_note"] or "")

    if is_clinical_advisor(user):
        if case["advisor_email"] != user["email"]:
            return "<div class='card'><h2>Role actions</h2><p class='help'>You can view this case, but only the owning advisor can submit changes.</p></div>"
        if case["status"] in ("Draft", "Changes requested"):
            return f"""
            <div class="card">
              <h2>Advisor actions</h2>
              <p class="help">Submit the draft answer for clinical lead approval once you have checked the evidence panel.</p>
              <form method="post" action="/cases/{case_id}/workflow">
                <input type="hidden" name="action" value="submit">
                <p><label>Advisor note</label><textarea name="note" placeholder="Optional context for the clinical lead">{note}</textarea></p>
                <button type="submit">Submit for approval</button>
              </form>
            </div>
            """
        return "<div class='card'><h2>Advisor actions</h2><p class='help'>This case has already been submitted or reviewed.</p></div>"

    if is_clinical_lead(user):
        if case["status"] == "Pending clinical approval":
            if int(case["source_count"] or 0) <= 0:
                return f"""
                <div class="card">
                  <h2>Clinical lead approval blocked</h2>
                  <p class="help">No verified source was returned for this case. The answer cannot be approved until a systems reviewer resolves the source issue.</p>
                </div>
                """
            return f"""
            <div class="card">
              <h2>Clinical lead approval</h2>
              <p class="help">Approve only after checking the answer and source evidence. Use request changes when the advisor needs to revise or escalate.</p>
              <form method="post" action="/cases/{case_id}/workflow">
                <p><label>Review note</label><textarea name="note" placeholder="Required for request changes; optional for approval">{note}</textarea></p>
                <button name="action" value="approve" type="submit">Approve answer</button>
                <button class="secondary" name="action" value="request_changes" type="submit">Request changes</button>
              </form>
            </div>
            """
        if case["status"] == "Approved":
            return f"""
            <div class="card">
              <h2>Clinical lead approval</h2>
              <p class="help">This answer is approved. Close the consultation after the advice has been communicated or transferred to the next workflow.</p>
              <form method="post" action="/cases/{case_id}/workflow">
                <input type="hidden" name="action" value="close">
                <p><label>Closure note</label><textarea name="note">{note}</textarea></p>
                <button type="submit">Close case</button>
              </form>
            </div>
            """
        return f"""
        <div class="card">
          <h2>Clinical lead approval</h2>
          <p class="help">Waiting for advisor submission. Current status: {h(case['status'])}.</p>
        </div>
        """

    if is_systems_reviewer(user):
        return f"""
        <div class="card">
          <h2>Source review</h2>
          <p class="help">Systems reviewers check whether retrieved source snippets are present, relevant, and ready for CRM/audit use. This does not approve clinical advice.</p>
          <form method="post" action="/cases/{case_id}/workflow">
            <p><label>Source review note</label><textarea name="note" placeholder="Comment on source quality, missing files, or indexing issues">{source_note}</textarea></p>
            <button name="action" value="source_reviewed" type="submit">Mark sources reviewed</button>
            <button class="secondary" name="action" value="source_issue" type="submit">Flag source issue</button>
          </form>
        </div>
        """

    return ""


def update_case_workflow(user, case_id, form):
    action = (form.get("action", [""])[0] or "").strip()
    note = (form.get("note", [""])[0] or "").strip()
    ts = now_iso()

    with db() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not case:
            return "Case not found."

        if action == "submit":
            if not is_clinical_advisor(user) or case["advisor_email"] != user["email"]:
                return "Only the owning clinical advisor can submit this case."
            if case["status"] not in ("Draft", "Changes requested"):
                return "This case cannot be submitted from its current status."
            conn.execute(
                """
                UPDATE cases
                SET status = ?, submitted_at = ?, updated_at = ?, approval_note = ?
                WHERE id = ?
                """,
                ("Pending clinical approval", ts, ts, note, case_id),
            )
            return None

        if action in ("approve", "request_changes", "close"):
            if not is_clinical_lead(user):
                return "Only the clinical lead can approve, request changes, or close cases."
            if action == "approve" and case["status"] != "Pending clinical approval":
                return "Only pending cases can be approved."
            if action == "approve" and int(case["source_count"] or 0) <= 0:
                return "This case has no verified source and cannot be approved."
            if action == "request_changes" and case["status"] != "Pending clinical approval":
                return "Only pending cases can be returned for changes."
            if action == "close" and case["status"] != "Approved":
                return "Only approved cases can be closed."
            new_status = {
                "approve": "Approved",
                "request_changes": "Changes requested",
                "close": "Closed",
            }[action]
            conn.execute(
                """
                UPDATE cases
                SET status = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ?, approval_note = ?
                WHERE id = ?
                """,
                (new_status, ts, user["name"], ts, note, case_id),
            )
            return None

        if action in ("source_reviewed", "source_issue"):
            if not is_systems_reviewer(user):
                return "Only the systems reviewer can update source review status."
            new_status = "Reviewed" if action == "source_reviewed" else "Issue flagged"
            conn.execute(
                """
                UPDATE cases
                SET source_review_status = ?, source_reviewed_at = ?, source_reviewed_by = ?, source_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_status, ts, user["name"], note, ts, case_id),
            )
            return None

    return "Unknown workflow action."


def case_detail_page(user, case_id):
    with db() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        sources = conn.execute(
            "SELECT * FROM case_sources WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()

    if not case:
        return not_found_page(user)

    evidence = ""
    if sources:
        for i, source in enumerate(sources, start=1):
            quote = " ".join((source["quote"] or "").split())
            if len(quote) > 520:
                quote = quote[:520].rstrip() + "..."
            source_kind = (
                "Related anonymised call evidence"
                if str(source["file_id"] or "").startswith("evaluation_cases:")
                else "Clinical guidance source"
            )
            evidence += f"""
            <div class="evidence">
              <div class="evidence-title">Source {i}: {h(source['file_name'])}</div>
              <div class="evidence-meta">{h(source_kind)} | File ID: {h(source['file_id'])} | Score: {h(source['score'])}</div>
              <div>{h(quote)}</div>
            </div>
            """
    else:
        evidence = "<p class='help'>No structured source was returned. Escalate for manual source review.</p>"

    actions = case_action_panel(user, case)
    body = f"""
    <div class="topline">
      <div>
        <h1>Case IMAC-{case['id']:04d}</h1>
        <div class="subtitle">{h(case['category'])} | {h(case['channel'])} | Created {h(case['created_at'][:10])}</div>
      </div>
      <div>
        <a class="button secondary" href="/cases/{case['id']}/crm">CRM export</a>
      </div>
    </div>

    <div class="notice">{h(case['safety_note'])}</div>

    {actions}

    <div style="height:16px"></div>

    <div class="grid grid-2">
      <section class="card">
        <h2>Advisor draft answer</h2>
        <div class="answer">{h(case['answer'])}</div>
      </section>
      <aside class="card">
        <h2>Evidence</h2>
        {evidence}
      </aside>
    </div>

    <div style="height:16px"></div>
    <div class="grid grid-2">
      <section class="card">
        <h2>Consultation details</h2>
        <p><strong>Question:</strong><br>{h(case['question'])}</p>
        <p><strong>Caller:</strong> {h(case['caller_name']) or '-'}<br>
        <strong>Organisation:</strong> {h(case['caller_org']) or '-'}</p>
      </section>
      <section class="card">
        <h2>Operational status</h2>
        <p><strong>Status:</strong> <span class="tag">{h(case['status'])}</span></p>
        <p><strong>Priority:</strong> <span class="tag {'warn' if case['priority'] == 'High' else 'neutral'}">{h(case['priority'])}</span></p>
        <p><strong>Advisor:</strong> {h(case['advisor_name'])}</p>
        <p><strong>PII redaction:</strong> {h(case['pii_redaction_summary'] or 'No PII patterns detected')}</p>
        <p><strong>Submitted:</strong> {h(case['submitted_at'] or '-')}</p>
        <p><strong>Clinical review:</strong> {h(case['reviewed_by'] or '-')} {h(case['reviewed_at'] or '')}</p>
        <p><strong>Source review:</strong> <span class="tag neutral">{h(case['source_review_status'] or 'Not reviewed')}</span><br>
        {h(case['source_reviewed_by'] or '-')} {h(case['source_reviewed_at'] or '')}</p>
      </section>
    </div>

    <div style="height:16px"></div>
    <div class="card">
      <h2>Review notes</h2>
      <p><strong>Clinical note:</strong><br>{h(case['approval_note'] or '-')}</p>
      <p><strong>Source note:</strong><br>{h(case['source_note'] or '-')}</p>
    </div>
    """
    return render_layout(f"Case IMAC-{case['id']:04d} | IMAC Guidance Advisor", user, body, "cases")


def crm_export_page(user, case_id):
    with db() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        sources = conn.execute(
            "SELECT file_name, file_id, score, quote FROM case_sources WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()
    if not case:
        return not_found_page(user)

    payload = {
        "case_reference": f"IMAC-{case['id']:04d}",
        "status": case["status"],
        "priority": case["priority"],
        "category": case["category"],
        "channel": case["channel"],
        "advisor": case["advisor_name"],
        "caller": {
            "name": case["caller_name"],
            "organisation": case["caller_org"],
        },
        "question": case["question"],
        "pii_redaction": {
            "redacted_count": case["pii_redacted_count"],
            "summary": case["pii_redaction_summary"],
            "raw_preview_stored_only_when_redacted": case["original_question_preview"],
        },
        "advisor_draft_answer": case["answer"],
        "source_count": case["source_count"],
        "sources": [dict(row) for row in sources],
        "clinical_review": {
            "submitted_at": case["submitted_at"],
            "reviewed_at": case["reviewed_at"],
            "reviewed_by": case["reviewed_by"],
            "approval_note": case["approval_note"],
        },
        "source_review": {
            "status": case["source_review_status"],
            "reviewed_at": case["source_reviewed_at"],
            "reviewed_by": case["source_reviewed_by"],
            "note": case["source_note"],
        },
        "safety_note": case["safety_note"],
    }
    body = f"""
    <div class="topline">
      <div>
        <h1>CRM export</h1>
        <div class="subtitle">Structured case payload for future CRM integration. This proof of concept does not connect to live production systems.</div>
      </div>
      <a class="button secondary" href="/cases/{case_id}">Back to case</a>
    </div>
    <div class="card">
      <pre>{h(json.dumps(payload, indent=2))}</pre>
    </div>
    """
    return render_layout("CRM export | IMAC Guidance Advisor", user, body, "cases")


def not_found_page(user):
    body = """
    <div class="card">
      <h1>Not found</h1>
      <p class="subtitle">The requested record could not be found.</p>
      <a class="button" href="/">Back to dashboard</a>
    </div>
    """
    return render_layout("Not found | IMAC Guidance Advisor", user, body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def current_user(self):
        cookie = self.headers.get("Cookie", "")
        token = ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "advisor_session":
                token = value
                break
        email = SESSIONS.get(token)
        if not email:
            return None
        user = dict(USERS[email])
        user["email"] = email
        return user

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return parse_qs(raw)

    def redirect(self, path):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.end_headers()

    def send_html(self, html_text, status=HTTPStatus.OK):
        data = html_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/login":
            self.send_html(login_page())
            return

        if path == "/logout":
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Set-Cookie", "advisor_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/login")
            self.end_headers()
            return

        user = self.current_user()
        if not user:
            self.redirect("/login")
            return

        if path == "/":
            self.send_html(dashboard_page(user))
            return
        if path == "/approvals":
            self.send_html(approvals_page(user))
            return
        if path == "/updates":
            self.send_html(clinical_updates_page(user))
            return
        if path == "/common-requests":
            self.send_html(common_requests_page(user))
            return
        if path == "/knowledge-base":
            self.send_html(knowledge_base_page(user))
            return
        if path == "/evaluation":
            self.send_html(evaluation_page(user))
            return
        if path == "/pii-tools":
            self.send_html(pii_tools_page(user))
            return
        if path in ("/brief", "/stakeholders", "/architecture", "/governance"):
            self.redirect("/updates")
            return
        if path == "/cases":
            self.send_html(cases_page(user))
            return
        if path == "/cases/new":
            self.send_html(new_case_page(user))
            return
        if path.startswith("/cases/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[1].isdigit():
                case_id = int(parts[1])
                if len(parts) == 3 and parts[2] == "crm":
                    self.send_html(crm_export_page(user, case_id))
                else:
                    self.send_html(case_detail_page(user, case_id))
                return

        self.send_html(not_found_page(user), HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/login":
            form = self.read_form()
            email = (form.get("email", [""])[0] or "").strip().lower()
            password = form.get("password", [""])[0]
            user = USERS.get(email)
            if not user or user["password"] != password:
                self.send_html(login_page("Invalid email or password."), HTTPStatus.UNAUTHORIZED)
                return

            token = secrets.token_urlsafe(32)
            SESSIONS[token] = email
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Set-Cookie", f"advisor_session={token}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/")
            self.end_headers()
            return

        if path == "/register":
            form = self.read_form()
            email = (form.get("email", [""])[0] or "").strip().lower()
            password = form.get("password", [""])[0]
            name = (form.get("name", [""])[0] or "").strip()
            role = (form.get("role", ["Clinical Advisor"])[0] or "Clinical Advisor").strip()

            if not email or "@" not in email:
                self.send_html(login_page("Please enter a valid email for the demo account."), HTTPStatus.BAD_REQUEST)
                return
            if len(password) < 6:
                self.send_html(login_page("Password must be at least 6 characters."), HTTPStatus.BAD_REQUEST)
                return
            if email in USERS:
                self.send_html(login_page("That email is already registered for this demo."), HTTPStatus.BAD_REQUEST)
                return

            USERS[email] = {
                "password": password,
                "name": name or "Demo Advisor",
                "role": role,
            }
            token = secrets.token_urlsafe(32)
            SESSIONS[token] = email
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Set-Cookie", f"advisor_session={token}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/updates")
            self.end_headers()
            return

        user = self.current_user()
        if not user:
            self.redirect("/login")
            return

        if path == "/cases/new":
            form = self.read_form()
            case_id, error = create_case(user, form)
            if error:
                self.send_html(new_case_page(user, error), HTTPStatus.BAD_REQUEST)
                return
            self.redirect(f"/cases/{case_id}")
            return

        if path == "/evaluation":
            form = self.read_form()
            query = (form.get("query", [""])[0] or "").strip()
            if not query:
                self.send_html(evaluation_page(user, error="Please enter an evaluation question."), HTTPStatus.BAD_REQUEST)
                return
            redacted_query, _ = redact_pii(query)
            try:
                results = AzureAgentClient().query_azure_search(redacted_query, top=6, clinical_only=False)
                self.send_html(evaluation_page(user, query=redacted_query, results=results))
            except Exception as exc:
                self.send_html(evaluation_page(user, query=redacted_query, error=str(exc)), HTTPStatus.BAD_REQUEST)
            return

        if path == "/pii-tools":
            form = self.read_form()
            text = (form.get("text", [""])[0] or "").strip()
            redacted, findings = redact_pii(text)
            self.send_html(pii_tools_page(user, text=text, redacted=redacted, findings=findings))
            return

        if path.startswith("/cases/") and path.endswith("/workflow"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[1].isdigit():
                case_id = int(parts[1])
                form = self.read_form()
                error = update_case_workflow(user, case_id, form)
                if error:
                    self.send_html(case_detail_page(user, case_id).replace("</main>", f"<div class='notice'>{h(error)}</div></main>"), HTTPStatus.BAD_REQUEST)
                    return
                self.redirect(f"/cases/{case_id}")
                return

        self.send_html(not_found_page(user), HTTPStatus.NOT_FOUND)


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"IMAC Guidance Advisor running at http://{HOST}:{PORT}")
    print("Demo login: advisor@imac.local / advisor123")
    server.serve_forever()


if __name__ == "__main__":
    main()
