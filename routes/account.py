"""routes/account.py — Account, brands, pricing, history."""

from __future__ import annotations
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

# ── Brand form helpers ────────────────────────────────────────────────────────
def _brand_form_html(b=None, error=""):
    p   = b.get("profile", {}) if b else {}
    err = f'<div class="alert alert-error">✕ {error}</div>' if error else ""
    def val(k, default=""):
        return p.get(k, default) or default
    def checked(k, v):
        return "selected" if p.get(k) == v else ""
    languages = [
        ("English","English"), ("Arabic","Arabic"),
        ("Egyptian Arabic","Egyptian Arabic"), ("Gulf Arabic","Gulf Arabic"),
        ("French","French"), ("Spanish","Spanish"), ("German","German"),
    ]
    lang_opts = "".join(
        f'<option value="{lv}" {checked("language", lv)}>{ll}</option>'
        for ll, lv in languages
    )
    return f"""{err}
    <div class="form-group"><label class="form-label">Brand Name *</label>
      <input class="form-input" type="text" name="name"
             value="{b["name"] if b else ""}" required
             placeholder="e.g. TechAI Pro"/></div>
    <div class="grid-2" style="gap:12px;">
      <div class="form-group"><label class="form-label">Tagline</label>
        <input class="form-input" type="text" name="tagline"
               value="{val("tagline")}" placeholder="Build faster."/></div>
      <div class="form-group"><label class="form-label">Industry</label>
        <input class="form-input" type="text" name="industry"
               value="{val("industry")}" placeholder="SaaS / Fitness / Fashion"/></div>
    </div>
    <div class="form-group"><label class="form-label">Target Audience</label>
      <input class="form-input" type="text" name="target_audience"
             value="{val("target_audience")}" placeholder="Startup founders, 25–40"/></div>
    <div class="form-group"><label class="form-label">Voice & Tone Description</label>
      <textarea class="form-textarea" name="voice_desc" rows="3"
                placeholder="Confident but approachable...">{val("voice_desc")}</textarea></div>
    <div class="form-group"><label class="form-label">USPs (one per line)</label>
      <textarea class="form-textarea" name="usps" rows="4"
                placeholder="✦ 48-hour response&#10;✦ AI-powered">{val("usps")}</textarea></div>
    <div class="form-group"><label class="form-label">Sample Post</label>
      <textarea class="form-textarea" name="sample_post" rows="4"
                placeholder="Paste a real post that captures your voice.">{val("sample_post")}</textarea></div>
    <div class="grid-2" style="gap:12px;">
      <div class="form-group"><label class="form-label">Signature Words</label>
        <input class="form-input" type="text" name="signature_words"
               value="{val("signature_words")}" placeholder="Unlock, Empower"/></div>
      <div class="form-group"><label class="form-label">Banned Words</label>
        <input class="form-input" type="text" name="banned_words"
               value="{val("banned_words")}" placeholder="leverage, synergy"/></div>
    </div>
    <div class="grid-2" style="gap:12px;">
      <div class="form-group"><label class="form-label">Emoji Style</label>
        <select class="form-select" name="emoji_style">
          <option value="none" {checked("emoji_style","none")}>None</option>
          <option value="minimal" {checked("emoji_style","minimal")}>Minimal (1-2)</option>
          <option value="moderate" {"selected" if not p.get("emoji_style") else checked("emoji_style","moderate")}>Moderate</option>
          <option value="heavy" {checked("emoji_style","heavy")}>Heavy (Gen Z)</option>
        </select></div>
      <div class="form-group"><label class="form-label">CTA Style</label>
        <input class="form-input" type="text" name="cta_style"
               value="{val("cta_style")}" placeholder="Save this. Follow for more."/></div>
    </div>
    <div class="form-group"><label class="form-label">Visual Style</label>
      <input class="form-input" type="text" name="visual_style"
             value="{val("visual_style")}" placeholder="Minimalist dark mode, cyan accents"/></div>
    <div class="form-group"><label class="form-label">Default Language</label>
      <select class="form-select" name="language">{lang_opts}</select></div>"""


def _extract_brand_profile(form_data):
    keys = ["tagline","industry","target_audience","voice_desc","usps",
            "sample_post","signature_words","banned_words","emoji_style",
            "cta_style","visual_style","language"]
    return {k: str(form_data.get(k, "") or "").strip() for k in keys}


import datetime
import json
import os
import uuid
from db import get_brand_profile, save_brand_profile


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    status_filter = request.query_params.get("status","")
    type_filter   = request.query_params.get("type","")
    all_gens      = get_user_generations(user["id"], limit=100)
    gens          = [g for g in all_gens
                     if (not status_filter or g["status"] == status_filter)
                     and (not type_filter   or g["content_type"] == type_filter)]
    if not gens:
        body='<div class="empty-state"><div class="empty-icon">◈</div><div class="empty-text">No generations yet</div><a class="btn btn-ghost btn-sm mt-3" href="/generate">✦ Generate now</a></div>'
    else:
        sb={"completed":"badge-green","running":"badge-amber","generating_media":"badge-amber","awaiting_approval":"badge-amber","pending":"badge-gray","failed":"badge-red","scheduled":"badge-blue","cancelled":"badge-gray"}
        rows="".join(f'''<tr>
          <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600;">{g["topic"]}</td>
          <td>{"🎬" if g["content_type"]=="video" else "📸"} {g["content_type"]}</td>
          <td>{", ".join(g["platforms"][:2])+("…" if len(g["platforms"])>2 else "")}</td>
          <td><span class="badge {sb.get(g["status"],"badge-gray")}">{g["status"].replace("_"," ")}</span></td>
          <td style="font-family:var(--mono);font-size:10px;color:var(--text3);">
            {("📅 "+g["scheduled_at"][:16].replace("T"," ")) if g.get("scheduled_at") and g["status"]=="scheduled" else g["created_at"][:16].replace("T"," ")}
          </td>
          <td>
            <div class="flex gap-2">
              <a class="btn btn-ghost btn-sm" href="/result/{g["id"]}">View →</a>
              {f'<a class="btn btn-ghost btn-sm" href="/download/{g["id"]}" title="Download Pack">⬇</a>' if g["status"]=="completed" else ""}
              {'<form method="post" action="/strategy/cancel-generation/'+g["id"]+'" style="display:inline;" onsubmit="return confirm(\'Cancel this scheduled generation?\')"><button class="btn btn-danger btn-sm" type="submit" style="padding:4px 10px;">✕</button></form>' if g["status"]=="scheduled" else ""}
            </div>
          </td>
        </tr>''' for g in gens)
        body=f'<table class="hist-table"><thead><tr><th>Topic</th><th>Type</th><th>Platforms</th><th>Status</th><th>Date / Scheduled</th><th></th></tr></thead><tbody>{rows}</tbody></table>'
    _active_filters = bool(status_filter or type_filter)
    filter_bar = f'''<div class="flex gap-2 mb-3 items-center" style="flex-wrap:wrap;">
      <span style="font-family:var(--mono);font-size:10px;color:var(--text3);">FILTER:</span>
      <a class="btn btn-sm {"btn-ghost" if not status_filter else "btn-primary"}" href="/history">All</a>
      <a class="btn btn-sm {"btn-primary" if status_filter=="completed" else "btn-ghost"}" href="/history?status=completed">✅ Completed</a>
      <a class="btn btn-sm {"btn-primary" if status_filter=="scheduled" else "btn-ghost"}" href="/history?status=scheduled">📅 Scheduled</a>
      <a class="btn btn-sm {"btn-primary" if status_filter=="failed" else "btn-ghost"}" href="/history?status=failed">❌ Failed</a>
      <a class="btn btn-sm {"btn-primary" if type_filter=="video" else "btn-ghost"}" href="/history?type=video">🎬 Video</a>
      <a class="btn btn-sm {"btn-primary" if type_filter=="static" else "btn-ghost"}" href="/history?type=static">📸 Static</a>
      <span style="font-family:var(--mono);font-size:10px;color:var(--text3);margin-left:auto;">{len(gens)} results</span>
    </div>'''
    content=f'''<div class="topbar"><div><div class="topbar-title">◈ History</div></div>
      <a class="btn btn-primary btn-sm" href="/generate">+ New Generation</a></div>
    <div class="content">{filter_bar}<div class="card" style="overflow:hidden;padding:0;">{body}</div></div>'''
    return HTMLResponse(_page(content,user,"History","history"))

@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, msg: str=""):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    q=quota_status(user); pct=round((q["used"]/max(q["limit"],1))*100)
    msg_html=f'<div class="alert alert-success">✓ {msg}</div>' if msg else ""
    acct_settings=get_user_settings(user["id"])
    details="".join(f'<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);font-size:13px;"><span style="color:var(--text2);">{k}</span><span style="font-weight:700;">{v}</span></div>' for k,v in [("Plan",user["plan"].title()),("Member since",user["created_at"][:10]),("Last login",(user.get("last_login") or "—")[:10]),("User ID",user["id"][:8]+"…")])
    _gk_saved=bool(acct_settings.get("gemini_key","")); _or_saved=bool(acct_settings.get("openrouter_key","")); _ai_saved=bool(acct_settings.get("aiml_key",""))
    def _key_status(saved): return '<span style="font-size:10px;font-family:var(--mono);color:var(--green);">✓ saved</span>' if saved else '<span style="font-size:10px;font-family:var(--mono);color:var(--text3);">not set</span>'
    # Pre-compute scheduled gens for account page
    _acct_scheduled = get_scheduled_generations(user["id"])
    _sched_section = ""
    if _acct_scheduled:
        _sched_rows = "".join(
            f'<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);">' +
            f'<div style="flex:1;min-width:0;">' +
            f'<div style="font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{g["topic"][:70]}</div>' +
            f'<div style="font-family:var(--mono);font-size:10px;color:var(--text3);">' +
            f'{(g.get("config") or {}).get("platforms",["?"])[0]} · ' +
            f'{(g.get("config") or {}).get("content_type","static")} · ' +
            f'📅 {(g.get("scheduled_at") or "")[:16]}</div></div>' +
            f'<a class="btn btn-ghost btn-sm" href="/result/{g["id"]}" style="font-size:11px;">View</a> ' +
            f'<form method="post" action="/strategy/cancel-generation/{g["id"]}" style="display:inline;" onsubmit="return confirm(\'Cancel?\')">' +
            f'<button class="btn btn-danger btn-sm" type="submit" style="padding:4px 10px;font-size:11px;">✕</button></form></div>'
            for g in _acct_scheduled
        )
        _sched_section = f'<div class="card mt-4"><div class="card-title mb-3">📅 Scheduled Generations ({len(_acct_scheduled)})</div>{_sched_rows}</div>'
    api_keys_section=f'''<div class="card mt-4" id="api-keys">
      <div class="card-title mb-2">🔑 Saved API Keys</div>
      <div style="font-size:12px;color:var(--text2);margin-bottom:16px;line-height:1.6;">
        Save your keys once — the <strong>Generate</strong> form will auto-fill them whenever a key field is left blank.
        Keys are stored locally in your database.
      </div>
      <form method="post" action="/account/api-keys">
        <div class="grid-2" style="gap:12px;">
          <div class="form-group"><label class="form-label">🔵 Gemini API Key {_key_status(_gk_saved)} <span class="form-hint" style="display:inline;margin-left:6px;">text + image gen</span></label>
            <input class="form-input" type="password" name="gemini_key" value="{acct_settings.get("gemini_key","")}" placeholder="AIza…"/></div>
          <div class="form-group"><label class="form-label">🟣 OpenRouter API Key {_key_status(_or_saved)} <span class="form-hint" style="display:inline;margin-left:6px;">alternative LLMs</span></label>
            <input class="form-input" type="password" name="openrouter_key" value="{acct_settings.get("openrouter_key","")}" placeholder="sk-or-…"/></div>
          <div class="form-group" style="margin-bottom:0"><label class="form-label">🎬 AIML API Key {_key_status(_ai_saved)} <span class="form-hint" style="display:inline;margin-left:6px;">Veo 3 video gen</span></label>
            <input class="form-input" type="password" name="aiml_key" value="{acct_settings.get("aiml_key","")}" placeholder="leave blank to clear"/></div>
        </div>
        <div class="section-divider mt-3">Default Models <span style="font-weight:400;color:var(--text3);font-size:10px;">(pre-fill the Generate form)</span></div>
        <div class="grid-2" style="gap:12px;">
          <div class="form-group"><label class="form-label">Default Provider</label>
            <select class="form-select" name="llm_provider">
              <option value="google" {"selected" if acct_settings.get("llm_provider","google")=="google" else ""}>Google (Gemini)</option>
              <option value="openrouter" {"selected" if acct_settings.get("llm_provider","google")=="openrouter" else ""}>OpenRouter</option>
            </select></div>
          <div class="form-group"><label class="form-label">✍️ Text Model</label>
            <input class="form-input" type="text" name="llm_model" value="{acct_settings.get("llm_model","gemini-2.5-flash")}" placeholder="gemini-2.5-flash"/></div>
          <div class="form-group" style="margin-bottom:0"><label class="form-label">🎨 Image Model</label>
            <input class="form-input" type="text" name="image_model" value="{acct_settings.get("image_model","gemini-3.1-flash-image-preview")}" placeholder="gemini-3.1-flash-image-preview"/></div>
          <div class="form-group" style="margin-bottom:0"><label class="form-label">🎬 Video Model</label>
            <input class="form-input" type="text" name="video_model" value="{acct_settings.get("video_model","google/veo-3.1-i2v")}" placeholder="google/veo-3.1-i2v"/></div>
        </div>
        <div class="flex gap-3 items-center mt-3">
          <button class="btn btn-primary" type="submit">💾 Save Keys &amp; Defaults</button>
          <span style="font-size:11px;font-family:var(--mono);color:var(--text3);">
            {"✓ Gemini " if _gk_saved else ""}{"✓ OpenRouter " if _or_saved else ""}{"✓ AIML" if _ai_saved else "No keys saved yet"}
          </span>
        </div>
      </form>
    </div>'''
    content=f"""
    <div class="topbar"><div class="topbar-title">◉ Account</div></div>
    <div class="content">
      {msg_html}
      <div class="grid-2" style="align-items:start;">
        <div class="card">
          <div class="card-title mb-4">Profile</div>
          <form method="post" action="/account/update">
            <div class="form-group"><label class="form-label">Full Name</label>
              <input class="form-input" type="text" name="name" value="{user["name"]}" required/></div>
            <div class="form-group"><label class="form-label">Email</label>
              <input class="form-input" type="email" value="{user["email"]}" disabled style="opacity:0.5;cursor:not-allowed;"/></div>
            <div class="form-group"><label class="form-label">New Password <span style="color:var(--text3);">(leave blank to keep)</span></label>
              <input class="form-input" type="password" name="password" placeholder="••••••••" minlength="8"/></div>
            <button class="btn btn-primary" type="submit">Save Changes</button>
          </form>
        </div>
        <div>
          <div class="card mb-4">
            <div class="card-title mb-3">Usage This Month</div>
            <div class="grid-2" style="gap:12px;">
              <div class="stat-card" style="background:var(--surface2);border:1px solid var(--border);border-radius:var(--r);padding:18px;"><div class="stat-label">Used</div><div class="stat-value">{q["used"]}</div></div>
              <div class="stat-card" style="background:var(--surface2);border:1px solid var(--border);border-radius:var(--r);padding:18px;"><div class="stat-label">Remaining</div><div class="stat-value">{q["remaining"]}</div></div>
            </div>
            <div class="progress mt-3"><div class="progress-bar" style="width:{pct}%;{"background:var(--red)" if pct>=90 else ""}"></div></div>
            <div style="display:flex;justify-content:space-between;margin-top:6px;font-family:var(--mono);font-size:11px;color:var(--text3);">
              <span>{q["used"]} / {q["limit"]} generations</span><span>{pct}% used</span>
            </div>
            <a class="btn btn-ghost btn-sm mt-3 btn-full" href="/pricing">Upgrade Plan →</a>
          </div>
          <div class="card"><div class="card-title mb-3">Account Details</div>{details}</div>
        </div>
      </div>
      {api_keys_section}
      {_sched_section}
    </div>"""
    return HTMLResponse(_page(content,user,"Account","account"))

@router.post("/account/api-keys", response_class=HTMLResponse)
async def account_api_keys_post(
    request: Request,
    gemini_key: str=Form(""), openrouter_key: str=Form(""), aiml_key: str=Form(""),
    llm_provider: str=Form("google"), llm_model: str=Form("gemini-2.5-flash"),
    image_model: str=Form("gemini-3.1-flash-image-preview"), video_model: str=Form("google/veo-3.1-i2v"),
):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    save_user_settings(user["id"],{"gemini_key":gemini_key.strip(),"openrouter_key":openrouter_key.strip(),"aiml_key":aiml_key.strip(),"llm_provider":llm_provider,"llm_model":llm_model.strip() or "gemini-2.5-flash","image_model":image_model.strip() or "gemini-3.1-flash-image-preview","video_model":video_model.strip() or "google/veo-3.1-i2v"})
    return RedirectResponse("/account?msg=API+keys+and+defaults+saved#api-keys",status_code=303)

@router.post("/account/update", response_class=HTMLResponse)
async def account_update(request: Request, name: str=Form(...), password: str=Form("")):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    with get_conn() as conn:
        if password and len(password)>=8:
            conn.execute("UPDATE users SET name=?,password_hash=? WHERE id=?",(name.strip(),hash_password(password),user["id"]))
        else:
            conn.execute("UPDATE users SET name=? WHERE id=?",(name.strip(),user["id"]))
    return RedirectResponse("/account?msg=Profile+updated",status_code=303)

@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request, error: str=""):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    err_html=f'<div class="alert alert-warn">⚠ {error.replace("quota","Monthly quota reached. Upgrade to continue.")}</div>' if error else ""
    current_plan=user.get("plan","free")
    plan_defs=[
        ("free","Free","$0","10",False,["10 gens/month","Static posts","Trend intelligence","Community support"]),
        ("starter","Starter","$19","50",False,["50 gens/month","Static + Video","Competitor scraping","Email support"]),
        ("pro","Pro","$49","200",True,["200 gens/month","All output types","Per-model API keys","Slack support"]),
        ("agency","Agency","$149","1000",False,["1000 gens/month","Everything in Pro","White-label","Dedicated support"]),
    ]
    cards=""
    for pid,pname,price,quota,popular,features in plan_defs:
        is_cur=pid==current_plan
        btn=('<span class="badge badge-green" style="display:inline-flex;padding:8px 16px;font-size:12px;">✓ Current Plan</span>' if is_cur else f'<button class="btn btn-primary btn-full" onclick="selectPlan(\'{pid}\')">Upgrade to {pname}</button>')
        feats="".join(f'<div class="plan-feature">{f}</div>' for f in features)
        cards+=f'<div class="plan-card {"popular" if popular else ""} {"current" if is_cur else ""}"><div class="plan-name">{pname}</div><div class="plan-price">{price}<span class="plan-period">/mo</span></div><div style="font-family:var(--mono);font-size:11px;color:var(--text2);margin:10px 0 16px;">{quota} generations/month</div>{feats}<div style="margin-top:18px;">{btn}</div></div>'
    content=f'''<div class="topbar"><div><div class="topbar-title">⬡ Upgrade Plan</div></div><span class="badge badge-blue">Current: {current_plan.title()}</span></div>
    <div class="content">{err_html}<div class="plan-grid">{cards}</div>
    <div class="card mt-4" style="text-align:center;padding:32px;"><div style="font-size:14px;color:var(--text2);">🔒 Stripe integration coming soon · Cancel anytime</div></div></div>
    <script>function selectPlan(plan){{toast('Stripe integration coming soon! Contact us to upgrade.','info');}}</script>'''
    return HTMLResponse(_page(content,user,"Pricing","pricing"))

@router.get("/brands", response_class=HTMLResponse)
async def brands_page(request: Request, msg: str="", error: str=""):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    all_brands=get_brands(user["id"])
    msg_html=(f'<div class="alert alert-success">✓ {msg}</div>' if msg else "")+(f'<div class="alert alert-error">✕ {error}</div>' if error else "")
    if all_brands:
        brand_cards=""
        for b in all_brands:
            p=b.get("profile",{})
            brand_cards+=f'''<div class="card mb-3">
              <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-3">
                  <div class="user-avatar" style="width:40px;height:40px;font-size:16px;">{b["name"][0].upper()}</div>
                  <div><div style="font-weight:700;">{b["name"]}</div>
                    <div style="font-family:var(--mono);font-size:10px;color:var(--text3);">{p.get("industry","—")} · {p.get("target_audience","—")[:40] if p.get("target_audience") else "—"}</div>
                  </div>
                </div>
                <div class="flex gap-2">
                  {'<span class="badge badge-green">✓ Default</span>' if b["is_default"] else '<form method="post" action="/brands/'+b["id"]+'/set-default"><button class="btn btn-ghost btn-sm" type="submit">Set default</button></form>'}
                  <a class="btn btn-ghost btn-sm" href="/brands/{b["id"]}/edit">Edit</a>
                  <form method="post" action="/brands/{b["id"]}/delete" onsubmit="return confirm('Delete brand?')">
                    <button class="btn btn-danger btn-sm" type="submit">Delete</button>
                  </form>
                </div>
              </div>
              {f'<div style="font-size:12px;color:var(--text2);margin-bottom:8px;">{p.get("voice_desc","")[:200]}</div>' if p.get("voice_desc") else ""}
              <div style="display:flex;gap:8px;flex-wrap:wrap;">
                {f'<span class="badge badge-blue">{p.get("emoji_style","—")} emoji</span>' if p.get("emoji_style") else ""}
                {f'<span class="badge badge-purple">{p.get("cta_style","")[:30]}</span>' if p.get("cta_style") else ""}
              </div>
            </div>'''
    else:
        brand_cards='<div class="empty-state"><div class="empty-icon">◆</div><div class="empty-text">No brands yet</div><div class="empty-sub">Create a brand to auto-inject voice into every generation</div></div>'
    content=f"""
    <div class="topbar"><div class="topbar-title">◆ Brand Voices</div>
      <a class="btn btn-primary btn-sm" href="/brands/new">+ New Brand</a></div>
    <div class="content">{msg_html}<div style="max-width:800px;">{brand_cards}</div></div>"""
    return HTMLResponse(_page(content,user,"Brands","brands"))

@router.get("/brands/new", response_class=HTMLResponse)
async def brand_new_page(request: Request, error: str=""):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    content=f"""<div class="topbar"><div class="topbar-title">◆ New Brand</div>
      <a class="btn btn-ghost btn-sm" href="/brands">← Back</a></div>
    <div class="content"><div class="card" style="max-width:720px;">
      <form method="post" action="/brands/new">{_brand_form_html(error=error)}
        <div class="flex gap-3 mt-4">
          <button class="btn btn-primary" type="submit">Create Brand</button>
          <a class="btn btn-ghost" href="/brands">Cancel</a>
        </div>
      </form>
    </div></div>"""
    return HTMLResponse(_page(content,user,"New Brand","brands"))

@router.post("/brands/new", response_class=HTMLResponse)
async def brand_new_post(request: Request):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    form=await request.form()
    data=dict(form); name=data.get("name","").strip()
    if not name: return RedirectResponse("/brands/new?error=Brand+name+required",status_code=303)
    profile=_extract_brand_profile(data)
    create_brand(user["id"],name,profile)
    return RedirectResponse("/brands?msg=Brand+created",status_code=303)

@router.get("/brands/{brand_id}/edit", response_class=HTMLResponse)
async def brand_edit_page(request: Request, brand_id: str, error: str=""):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    b=get_brand(brand_id,user["id"])
    if not b: return RedirectResponse("/brands")
    content=f"""<div class="topbar"><div class="topbar-title">◆ Edit Brand</div>
      <a class="btn btn-ghost btn-sm" href="/brands">← Back</a></div>
    <div class="content"><div class="card" style="max-width:720px;">
      <form method="post" action="/brands/{brand_id}/edit">{_brand_form_html(b,error=error)}
        <div class="flex gap-3 mt-4">
          <button class="btn btn-primary" type="submit">Save Brand</button>
          <a class="btn btn-ghost" href="/brands">Cancel</a>
        </div>
      </form>
    </div></div>"""
    return HTMLResponse(_page(content,user,f"Edit {b['name']}","brands"))

@router.post("/brands/{brand_id}/edit", response_class=HTMLResponse)
async def brand_edit_post(request: Request, brand_id: str):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    form=await request.form(); data=dict(form); name=data.get("name","").strip()
    if not name: return RedirectResponse(f"/brands/{brand_id}/edit?error=Brand+name+required",status_code=303)
    profile=_extract_brand_profile(data)
    update_brand(brand_id,user["id"],name,profile)
    return RedirectResponse("/brands?msg=Brand+updated",status_code=303)

@router.post("/brands/{brand_id}/delete", response_class=HTMLResponse)
async def brand_delete(request: Request, brand_id: str):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    delete_brand(brand_id,user["id"])
    return RedirectResponse("/brands?msg=Brand+deleted",status_code=303)

@router.post("/brands/{brand_id}/set-default", response_class=HTMLResponse)
async def brand_set_default(request: Request, brand_id: str):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    set_default_brand(brand_id,user["id"])
    return RedirectResponse("/brands?msg=Default+brand+updated",status_code=303)