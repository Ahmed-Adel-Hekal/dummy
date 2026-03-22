"""routes/insights.py — Dashboard and insights pages."""

from __future__ import annotations
import logging

from fastapi import Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from db import (get_conn, get_user_settings, save_user_settings,
                get_brands, get_brand, get_default_brand,
                create_brand, update_brand, delete_brand, set_default_brand,
                create_strategy, update_strategy, get_strategy, get_user_strategies,
                add_calendar_items, get_calendar_item, get_calendar_items,
                update_calendar_item_status, delete_calendar_item,
                get_usage_this_month, record_usage, quota_ok, quota_status,
                create_generation, update_generation, get_generation,
                get_user_generations, detect_niche, create_scheduled_generation,
                cancel_scheduled_generation, get_scheduled_generations,
                safe_json_loads, now_iso, current_month, LANGUAGE_CHOICES,
                PLATFORM_CHOICES, PLAN_QUOTAS, PLAN_PRICES, OUTPUT_ROOT)
from auth import get_current_user, require_user, create_token, verify_password, hash_password
from ui import (_page, _auth_page, _sidebar_html, _build_ideas_html,
                _build_competitor_report_html, _media_display_html,
                _get_latest_insights, _render_competitor_panel, _render_trend_panel)
from pipelines import (_run_pipeline, _run_media_approval, _run_single_idea_media,
                       _run_strategy_post, _run_strategy_pipeline, _bulk_progress)

from fastapi import APIRouter
router = APIRouter()

import datetime
import json
import os
import uuid
from db import get_brand_profile, save_brand_profile

logger = logging.getLogger("SignalMind.insights")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _all_completed_generations(uid: str, limit: int = 100) -> list[dict]:
    """
    Return every completed generation for the user, lightweight — just the
    fields needed to populate the selector and decide which tabs have data.
    Queries result_json only once; no Python-level filtering on insight presence
    so the selector always shows all completed runs.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, user_id, topic, result_json, created_at, platforms
               FROM generations
               WHERE user_id = ? AND status = 'completed'
               ORDER BY created_at DESC
               LIMIT ?""",
            (uid, limit),
        ).fetchall()

    out = []
    for r in rows:
        result = safe_json_loads(r["result_json"], {})
        comp   = result.get("competitor_insight") or {}
        trend  = result.get("trend_insight")      or {}

        has_comp  = bool(
            comp and isinstance(comp, dict) and
            any(comp.get(k) for k in ("top_hooks", "brand_overview",
                                      "content_patterns", "gap_opportunities"))
        )
        has_trend = bool(
            trend and isinstance(trend, dict) and
            (trend.get("top_trends") or trend.get("keywords"))
        )

        out.append({
            "id":         r["id"],
            "user_id":    r["user_id"],
            "topic":      r["topic"],
            "created_at": r["created_at"],
            "platforms":  safe_json_loads(r["platforms"], []),
            "competitor": comp,
            "trend":      trend,
            "has_comp":   has_comp,
            "has_trend":  has_trend,
        })
    return out


def _comp_empty_reason(comp: dict) -> str:
    """Human-readable reason why competitor data is absent."""
    if not comp:
        return "no_data"
    err = comp.get("error", "")
    if "no data provided" in err or "all URLs timed out" in err:
        return "no_urls"
    if err:
        return "scrape_error"
    return "no_data"


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    q = quota_status(user)

    with get_conn() as conn:
        total_gens   = conn.execute("SELECT COUNT(*) FROM generations WHERE user_id=?",
                                    (user["id"],)).fetchone()[0]
        completed    = conn.execute(
            "SELECT COUNT(*) FROM generations WHERE user_id=? AND status='completed'",
            (user["id"],)).fetchone()[0]
        scheduled_n  = conn.execute(
            "SELECT COUNT(*) FROM generations WHERE user_id=? AND status='scheduled'",
            (user["id"],)).fetchone()[0]
        total_brands = conn.execute("SELECT COUNT(*) FROM brands WHERE user_id=?",
                                    (user["id"],)).fetchone()[0]
        total_strats = conn.execute("SELECT COUNT(*) FROM strategies WHERE user_id=?",
                                    (user["id"],)).fetchone()[0]
        recent_gens  = conn.execute(
            "SELECT id,topic,content_type,status,created_at,scheduled_at "
            "FROM generations WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
            (user["id"],),
        ).fetchall()
        upcoming = conn.execute(
            "SELECT g.id, g.topic, g.content_type, g.scheduled_at, "
            "       json_extract(g.config_json,'$.platforms') as platforms "
            "FROM generations g "
            "WHERE g.user_id=? AND g.status='scheduled' "
            "ORDER BY g.scheduled_at ASC LIMIT 5",
            (user["id"],),
        ).fetchall()

    pct = round((q["used"] / max(q["limit"], 1)) * 100)
    bar_color = ("var(--red)" if pct >= 90 else
                 "var(--amber)" if pct >= 70 else
                 "linear-gradient(90deg,var(--accent),var(--accent2))")

    stat_cards = "".join(
        f'''<div class="stat-card">
          <div class="stat-label">{label}</div>
          <div class="stat-value" style="color:{color};">{value}</div>
          {f'<div style="font-size:11px;color:var(--text3);margin-top:4px;">{sub}</div>' if sub else ""}
        </div>'''
        for label, value, color, sub in [
            ("Total Generations", total_gens,   "var(--text)",    None),
            ("Completed",         completed,    "var(--green)",   None),
            ("Scheduled",         scheduled_n,  "var(--accent)",  "waiting to fire"),
            ("Brands",            total_brands, "var(--accent2)", f"{total_strats} strategies"),
        ]
    )

    sb = {
        "completed":         "badge-green",
        "running":           "badge-amber",
        "generating_media":  "badge-amber",
        "awaiting_approval": "badge-amber",
        "pending":           "badge-gray",
        "failed":            "badge-red",
        "scheduled":         "badge-blue",
        "cancelled":         "badge-gray",
    }

    recent_rows = "".join(
        f'''<tr>
          <td style="font-weight:600;max-width:200px;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap;">{g["topic"]}</td>
          <td>{"🎬" if g["content_type"]=="video" else "📸"}</td>
          <td><span class="badge {sb.get(g["status"],"badge-gray")}"
               style="font-size:9px;">{g["status"].replace("_"," ")}</span></td>
          <td style="font-family:var(--mono);font-size:10px;color:var(--text3);">
            {g["created_at"][:10]}</td>
          <td><a class="btn btn-ghost btn-sm" href="/result/{g["id"]}"
               style="padding:3px 10px;font-size:11px;">View</a></td>
        </tr>'''
        for g in recent_gens
    ) or '<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px;">No generations yet</td></tr>'

    upcoming_rows = "".join(
        f'''<tr>
          <td style="font-weight:600;max-width:200px;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap;">{u["topic"]}</td>
          <td>{"🎬" if u["content_type"]=="video" else "📸"}</td>
          <td style="font-family:var(--mono);font-size:10px;color:var(--accent);">
            📅 {(u["scheduled_at"] or "")[:16].replace("T"," ")}</td>
          <td><a class="btn btn-ghost btn-sm" href="/result/{u["id"]}"
               style="padding:3px 10px;font-size:11px;">View</a></td>
        </tr>'''
        for u in upcoming
    ) or '<tr><td colspan="4" style="text-align:center;color:var(--text3);padding:20px;">Nothing scheduled</td></tr>'

    quick_links = [
        ("✦ Generate",    "/generate",  "btn-primary", "Create new content"),
        ("◐ New Strategy","/strategy",  "btn-ghost",   "Plan a campaign"),
        ("◆ Brands",      "/brands",    "btn-ghost",   "Manage brand voices"),
        ("◫ Calendar",    "/calendar",  "btn-ghost",   "View schedule"),
    ]
    quick_html = "".join(
        f'<a class="btn {cls}" href="{href}" '
        f'style="flex-direction:column;gap:2px;padding:14px;text-align:center;">'
        f'<span>{label}</span>'
        f'<span style="font-size:10px;font-weight:400;opacity:0.7;">{desc}</span></a>'
        for label, href, cls, desc in quick_links
    )

    content = f"""
    <div class="topbar">
      <div><div class="topbar-title">⚡ Dashboard</div>
        <div class="topbar-sub">Welcome back, {user["name"].split()[0]}</div></div>
      <span class="badge badge-blue">{q["plan"].title()} plan</span>
    </div>
    <div class="content">
      <div class="stats-grid mb-4">{stat_cards}</div>
      <div class="card mb-4 card-sm">
        <div class="flex items-center justify-between mb-2">
          <span style="font-size:13px;font-weight:600;">Monthly Quota</span>
          <span style="font-family:var(--mono);font-size:11px;color:var(--text3);">
            {q["used"]} / {q["limit"]} · {q["remaining"]} remaining</span>
        </div>
        <div class="progress">
          <div class="progress-bar" style="width:{pct}%;background:{bar_color};"></div>
        </div>
        {'<div class="alert alert-warn mt-3" style="margin-bottom:0;font-size:12px;">⚠ Running low. <a class="auth-link" href="/pricing">Upgrade →</a></div>' if pct >= 80 else ""}
      </div>
      <div class="grid-2" style="gap:20px;align-items:start;">
        <div class="card" style="padding:0;overflow:hidden;">
          <div style="padding:16px 20px;border-bottom:1px solid var(--border);
                      display:flex;justify-content:space-between;align-items:center;">
            <span style="font-weight:700;font-size:14px;">Recent Generations</span>
            <a class="btn btn-ghost btn-sm" href="/history"
               style="padding:4px 12px;font-size:11px;">All →</a>
          </div>
          <table class="hist-table">
            <thead><tr><th>Topic</th><th>Type</th><th>Status</th><th>Date</th><th></th></tr></thead>
            <tbody>{recent_rows}</tbody>
          </table>
        </div>
        <div style="display:flex;flex-direction:column;gap:16px;">
          <div class="card card-sm">
            <div style="font-weight:700;font-size:14px;margin-bottom:12px;">Quick Actions</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">{quick_html}</div>
          </div>
          <div class="card" style="padding:0;overflow:hidden;">
            <div style="padding:14px 20px;border-bottom:1px solid var(--border);
                        display:flex;justify-content:space-between;align-items:center;">
              <span style="font-weight:700;font-size:14px;">📅 Upcoming Scheduled</span>
              <a class="btn btn-ghost btn-sm" href="/history"
                 style="padding:4px 12px;font-size:11px;">All →</a>
            </div>
            <table class="hist-table">
              <thead><tr><th>Topic</th><th>Type</th><th>Fires at</th><th></th></tr></thead>
              <tbody>{upcoming_rows}</tbody>
            </table>
          </div>
        </div>
      </div>
    </div>"""
    return HTMLResponse(_page(content, user, "Dashboard", "generate"))


# ─────────────────────────────────────────────────────────────────────────────
# Insights
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/insights", response_class=HTMLResponse)
async def insights_page(request: Request, gen_id: str = "", tab: str = "trend"):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    # ── Load all completed generations ────────────────────────────────────────
    all_gens = _all_completed_generations(user["id"], limit=50)

    # ── Resolve which generation is "selected" ────────────────────────────────
    selected = None
    if gen_id:
        selected = next((g for g in all_gens if g["id"] == gen_id), None)
    if not selected and all_gens:
        # Default: prefer one that has trend data; fall back to most recent
        selected = (
            next((g for g in all_gens if g["has_trend"]), None) or
            all_gens[0]
        )

    # ── Build selector dropdown ───────────────────────────────────────────────
    def _opt_label(g: dict) -> str:
        tags = []
        if g["has_comp"]:  tags.append("🔍")
        if g["has_trend"]: tags.append("📈")
        tag_str = " ".join(tags)
        return f"{g['topic'][:50]} — {g['created_at'][:10]}{('  ' + tag_str) if tag_str else ''}"

    gen_options = "".join(
        f'<option value="{g["id"]}" '
        f'{"selected" if selected and g["id"] == selected["id"] else ""}>'
        f'{_opt_label(g)}</option>'
        for g in all_gens
    )

    # ── Panels ────────────────────────────────────────────────────────────────
    comp_data  = selected["competitor"] if selected else {}
    trend_data = selected["trend"]      if selected else {}

    comp_html  = _render_competitor_panel_with_context(comp_data, selected)
    trend_html = _render_trend_panel(trend_data)

    # ── No-generations state ──────────────────────────────────────────────────
    if not all_gens:
        no_data_banner = """
        <div class="card" style="text-align:center;padding:48px 32px;">
          <div style="font-size:36px;margin-bottom:16px;">◎</div>
          <div style="font-size:18px;font-weight:700;margin-bottom:8px;">No completed generations yet</div>
          <div style="font-size:13px;color:var(--text2);margin-bottom:24px;">
            Run your first generation to start capturing competitor intelligence
            and trend signals here automatically.
          </div>
          <a class="btn btn-primary" href="/generate">✦ Generate content now</a>
        </div>"""
    else:
        no_data_banner = ""

    # ── Tab pills ─────────────────────────────────────────────────────────────
    def _tab_btn(key: str, label: str, has_data: bool) -> str:
        active  = "btn-primary" if tab == key else "btn-ghost"
        dot     = (' <span style="display:inline-block;width:6px;height:6px;border-radius:50%;'
                   'background:var(--green);margin-left:4px;vertical-align:middle;"></span>'
                   if has_data else "")
        gen_seg = f"gen_id={selected['id']}&" if selected else ""
        return (f'<a class="btn {active}" href="/insights?{gen_seg}tab={key}" '
                f'style="gap:6px;">{label}{dot}</a>')

    tab_html = "".join([
        _tab_btn("trend",      "📈 Trend Intelligence",    bool(selected and selected["has_trend"])),
        _tab_btn("competitor", "🔍 Competitor Analysis",   bool(selected and selected["has_comp"])),
    ])

    # ── Selected generation info banner ───────────────────────────────────────
    sel_banner = ""
    if selected:
        platforms_str = ", ".join(selected.get("platforms", []))
        comp_tag  = ('  <span class="badge badge-green" style="font-size:9px;">🔍 Competitor data</span>'
                     if selected["has_comp"] else
                     '  <span class="badge badge-gray" style="font-size:9px;">No competitor data</span>')
        trend_tag = ('  <span class="badge badge-green" style="font-size:9px;">📈 Trend data</span>'
                     if selected["has_trend"] else
                     '  <span class="badge badge-gray" style="font-size:9px;">No trend data</span>')
        sel_banner = f"""
        <div class="card card-sm mb-4" style="background:var(--surface2);">
          <div class="flex items-center gap-3" style="flex-wrap:wrap;">
            <div style="flex:1;min-width:0;">
              <div style="font-weight:700;font-size:14px;overflow:hidden;
                          text-overflow:ellipsis;white-space:nowrap;">{selected["topic"]}</div>
              <div style="font-family:var(--mono);font-size:10px;color:var(--text3);">
                {selected["created_at"][:16].replace("T"," ")}
                {("  ·  " + platforms_str) if platforms_str else ""}
              </div>
            </div>
            {comp_tag}{trend_tag}
            <a class="btn btn-ghost btn-sm" href="/result/{selected["id"]}"
               style="font-size:11px;">View generation →</a>
          </div>
        </div>"""

    content = f"""
    <div class="topbar">
      <div>
        <div class="topbar-title">◎ Insights</div>
        <div class="topbar-sub">Competitor intelligence · Trend signals</div>
      </div>
      <div class="flex gap-3 items-center">
        {"" if not all_gens else f'''
        <select class="form-select" style="width:320px;padding:7px 12px;font-size:12px;"
                onchange="window.location='/insights?gen_id='+this.value+'&tab={tab}'">
          <option value="">— select a generation —</option>
          {gen_options}
        </select>
        <span class="badge badge-blue">{len(all_gens)} generation(s)</span>
        '''}
      </div>
    </div>

    <div class="content" style="max-width:none;">

      {no_data_banner}

      {sel_banner}

      {"" if not all_gens else f'''
      <!-- Tab switcher -->
      <div class="flex gap-2 mb-4">{tab_html}</div>

      <!-- Competitor tab -->
      <div style="display:{"block" if tab=="competitor" else "none"};">
        {comp_html}
      </div>

      <!-- Trend tab -->
      <div style="display:{"block" if tab=="trend" else "none"};">
        {trend_html}
      </div>
      '''}

      <!-- Refresh / actions card -->
      {"" if not all_gens else f'''
      <div class="card mt-4" style="background:var(--surface2);border-style:dashed;">
        <div class="flex items-center justify-between" style="flex-wrap:wrap;gap:12px;">
          <div>
            <div style="font-weight:700;font-size:14px;margin-bottom:4px;">
              🔄 Want fresher data?
            </div>
            <div style="font-size:12px;color:var(--text2);">
              Add competitor URLs in a new generation to capture intelligence,
              or clear the trend cache to force a fresh scrape.
            </div>
          </div>
          <div class="flex gap-3">
            <a class="btn btn-ghost btn-sm" href="/generate">✦ New generation</a>
            <form method="post" action="/api/cache/clear-trends" style="display:inline;">
              <button class="btn btn-ghost btn-sm" type="submit"
                      onclick="return confirm(\'Clear trend cache and re-scrape on next generation?\')">
                ⟳ Clear trend cache
              </button>
            </form>
          </div>
        </div>
      </div>
      '''}

    </div>"""
    return HTMLResponse(_page(content, user, "Insights", "insights"))


def _render_competitor_panel_with_context(ci: dict, selected: dict | None) -> str:
    """
    Wraps _render_competitor_panel with a clear contextual explanation
    of WHY data is or isn't present, so the user knows what to do.
    """
    # Import here to avoid circular; ui module is already imported at top
    from ui import _render_competitor_panel

    if not selected:
        return _render_competitor_panel({})

    if selected["has_comp"]:
        # We have real data — render it
        return _render_competitor_panel(ci)

    # No competitor data — explain why and what to do
    reason = _comp_empty_reason(ci)

    if reason == "no_urls":
        guidance = """
        <div class="card card-sm">
          <div style="display:flex;align-items:flex-start;gap:16px;">
            <div style="font-size:32px;flex-shrink:0;">🔍</div>
            <div>
              <div style="font-weight:700;font-size:15px;margin-bottom:6px;">
                No competitor URLs were added for this generation
              </div>
              <div style="font-size:13px;color:var(--text2);line-height:1.7;margin-bottom:16px;">
                Competitor analysis only runs when you paste URLs into the
                <strong>Competitor URLs</strong> field on the Generate page.
                Without URLs the agent has nothing to scrape.
              </div>
              <div style="font-size:12px;color:var(--text3);margin-bottom:16px;">
                Supported URL types:
                <ul style="margin:6px 0 0 18px;line-height:2;">
                  <li>Any website or blog (e.g. <code>https://competitor.com</code>)</li>
                  <li>YouTube channels (e.g. <code>https://youtube.com/@channel</code>)</li>
                  <li>Instagram profiles (e.g. <code>https://instagram.com/brand</code>)</li>
                  <li>TikTok profiles, Twitter/X accounts, LinkedIn pages</li>
                </ul>
              </div>
              <a class="btn btn-primary btn-sm" href="/generate">
                ✦ New generation with competitor URLs
              </a>
            </div>
          </div>
        </div>"""
    elif reason == "scrape_error":
        err_msg = ci.get("error", "unknown error")
        guidance = f"""
        <div class="card card-sm">
          <div class="alert alert-warn mb-3">
            ⚠ Competitor scrape ran but encountered an error: <code>{err_msg}</code>
          </div>
          <div style="font-size:13px;color:var(--text2);margin-bottom:12px;">
            This usually means the competitor site blocked the request or the URL
            returned no usable content. Try a different URL or check the URL is accessible.
          </div>
          <a class="btn btn-ghost btn-sm" href="/generate">Try again with different URLs →</a>
        </div>"""
    else:
        guidance = _render_competitor_panel({})

    return guidance