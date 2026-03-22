"""routes/generate.py — Generate content + result page."""

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
import datetime
import json
import os
import uuid
from db import get_brand_profile, save_brand_profile


@router.get("/result/{gid}", response_class=HTMLResponse)
async def result_page(request: Request, gid: str):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    gen=get_generation(gid,user["id"])
    if not gen: return RedirectResponse("/history")
    status=gen["status"]
    status_badge={"completed":"badge-green","running":"badge-amber","generating_media":"badge-amber","awaiting_approval":"badge-amber","pending":"badge-gray","failed":"badge-red","scheduled":"badge-blue","cancelled":"badge-gray"}.get(status,"badge-gray")
    approval_html=""
    if status=="awaiting_approval" and gen.get("result"):
        n_ideas = len(gen.get("result",{}).get("ideas",[]))
        approval_html=f'''<div class="approval-box"><div class="approval-title">👁 Review Required</div>
          <div class="approval-sub">Your ideas are ready. Review and edit below, then approve individually or all at once.</div>
          <div class="flex gap-3 items-center" style="flex-wrap:wrap;">
            <form method="post" action="/approve/{gid}">
              <button class="btn btn-green" type="submit">✓ Approve All {n_ideas} Ideas & Generate Media</button>
            </form>
            <button class="btn btn-ghost btn-sm" onclick="approveAllIndividual('{gid}',{n_ideas})">
              ▶ Generate One-by-One
            </button>
            <a class="btn btn-danger btn-sm" href="/generate" style="align-self:center;">✕ Discard</a>
          </div>
        </div>'''
    comp_html=""; ideas_html=""; trend_block=""
    if gen.get("result"):
        comp_html=_build_competitor_report_html(gen["result"], gid=gid)
        if status in ("completed","awaiting_approval","generating_media"):
            ideas_html=_build_ideas_html(gen)
        ti=gen["result"].get("trend_insight",{})
        if ti and isinstance(ti,dict):
            top_trends=ti.get("top_trends",[])[:6]; keywords=ti.get("keywords",[])[:12]; cs=ti.get("confidence_summary",{})
            strength_badge={"high":("badge-red","🔥 Exploding"),"medium":("badge-amber","📈 Growing"),"low":("badge-gray","〰 Stable")}
            trend_rows="".join(
                f'''<div style="padding:10px 0;border-bottom:1px solid var(--border);display:flex;align-items:flex-start;gap:12px;">
                  <div style="flex:1;min-width:0;">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px;">
                      <span style="font-size:13px;font-weight:600;">{t.get("topic","")[:80]}</span>
                      <span class="badge {strength_badge.get(t.get("trend_strength","low"),("badge-gray","Stable"))[0]}" style="font-size:9px;">{strength_badge.get(t.get("trend_strength","low"),("badge-gray","Stable"))[1]}</span>
                    </div>
                    <div style="font-size:11px;color:var(--text2);margin-bottom:6px;">{t.get("marketing_angle","")}</div>
                    <div style="display:flex;align-items:center;gap:8px;">
                      <div style="flex:1;height:3px;background:var(--surface2);border-radius:2px;">
                        <div style="width:{int(t.get("confidence_score",0))}%;height:3px;background:var(--accent);border-radius:2px;"></div>
                      </div>
                      <span style="font-family:var(--mono);font-size:10px;color:var(--text3);white-space:nowrap;">{t.get("confidence_score",0)}% conf</span>
                    </div>
                  </div>
                </div>'''
                for t in top_trends)
            kw_pills="".join(f'<span style="font-family:var(--mono);font-size:10px;background:rgba(79,142,247,0.08);color:var(--accent);border:1px solid rgba(79,142,247,0.18);padding:2px 9px;border-radius:20px;margin:2px;">{k}</span>' for k in keywords)
            avg=cs.get("average_score",0); total=len(top_trends)
            trend_block=f'''<div class="card mb-4">
              <div class="flex items-center justify-between mb-3">
                <div class="card-title">📈 Trend Intelligence</div>
                <div class="flex gap-2 items-center"><span style="font-family:var(--mono);font-size:10px;color:var(--text3);">{total} trends · avg {avg}%</span><a class="btn btn-ghost btn-sm" href="/insights?gen_id={gid}&tab=trend" style="font-size:11px;padding:3px 10px;">◎ Full Analysis →</a></div>
              </div>
              {trend_rows if trend_rows else '<div style="font-size:12px;color:var(--text3);padding:8px 0;">No live trend data — used cached signals.</div>'}
              {f'<div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:4px;">{kw_pills}</div>' if keywords else ""}
            </div>'''
    poll_html=""
    if status in ("pending","running","generating_media"):
        prog="60" if status in ("running","generating_media") else "10"
        label={"pending":"Starting pipeline…","running":"Running pipeline…","generating_media":"Generating images/videos…"}.get(status,"Working…")
        poll_html=f'''<div class="card mb-4">
          <div class="flex gap-3 items-center mb-3"><div class="spinner"></div>
            <div><div class="fw-bold">{label}</div></div>
          </div>
          <div class="progress"><div class="progress-bar" id="prog-bar" style="width:{prog}%"></div></div>
        </div>
        <script>
        (function(){{
          let prog={prog}; let stopped=false;
          const bar=document.getElementById('prog-bar');
          const iv=setInterval(function(){{if(stopped)return;prog=Math.min(prog+Math.random()*2,92);if(bar)bar.style.width=prog+'%';}},1500);
          window.addEventListener('pagehide',function(){{stopped=true;clearInterval(iv);}});
          function poll(){{
            if(stopped)return;
            fetch('/api/status/{gid}',{{cache:'no-store'}}).then(function(r){{return r.json();}}).then(function(d){{
              if(['completed','awaiting_approval','failed'].includes(d.status)){{stopped=true;clearInterval(iv);window.location.replace('/result/{gid}');}}
              else setTimeout(poll,1800);
            }}).catch(function(){{if(!stopped)setTimeout(poll,6000);}});
          }}
          setTimeout(poll,1800);
        }})();
        </script>'''
    err_html=f'<div class="alert alert-error">✕ {gen.get("error","Generation failed")}</div>' if status=="failed" else ""
    _sched_str = gen.get("scheduled_at","")
    _sched_item = [("Scheduled for", _sched_str[:16].replace("T"," "))] if _sched_str else []
    meta_items = [
        ("Topic",       gen["topic"]),
        ("Type",        "🎬 Video" if gen["content_type"]=="video" else "📸 Static"),
        ("Platforms",   ", ".join(gen["platforms"])),
        ("Language",    gen["language"]),
        ("Text Model",  gen.get("config",{}).get("llm_model","—")),
        ("Created",     gen["created_at"][:16].replace("T"," ")),
    ] + _sched_item
    meta_html="".join(f'<div><div style="font-family:var(--mono);font-size:9px;color:var(--text3);">{k}</div><div style="font-weight:700;font-size:12px;font-family:var(--mono);color:var(--text2);">{v}</div></div>' for k,v in meta_items)
    warn_html=f'<div class="alert alert-warn mb-3">⚠ {gen["result"]["warning"]}</div>' if gen.get("result") and gen["result"].get("warning") else ""
    content=f"""
    <div class="topbar">
      <div><div class="topbar-title">Generation Result</div>
        <div class="topbar-sub" style="font-family:var(--mono);font-size:10px;color:var(--text3);">{gid[:16]}…</div></div>
      <div class="flex gap-3 items-center">
        <span class="badge {status_badge}">{status.replace("_"," ")}</span>
        <a class="btn btn-ghost btn-sm" href="/generate">+ New</a>
        {f'<a class="btn btn-green btn-sm" href="/download/{gid}">⬇ Download Pack</a>' if status=="completed" else ""}
      </div>
    </div>
    <div class="content">
      <div class="card mb-4 card-sm"><div class="flex gap-4 items-center" style="flex-wrap:wrap;">{meta_html}</div></div>
      {err_html}{warn_html}{poll_html}{approval_html}{comp_html}{trend_block}{ideas_html}
    </div>"""
    return HTMLResponse(_page(content,user,"Result","history"),headers={"Cache-Control":"no-store, no-cache, must-revalidate","Pragma":"no-cache"})

@router.api_route("/approve/{gid}",methods=["POST"],response_class=HTMLResponse)
async def approve_generation(request: Request, gid: str, background_tasks: BackgroundTasks):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    gen=get_generation(gid,user["id"])
    if not gen: return RedirectResponse("/history")
    if gen["status"]!="awaiting_approval": return RedirectResponse(f"/result/{gid}")
    ideas_json=gen.get("result",{}).get("raw_json",{"ideas":[]})
    cfg=gen.get("config",{})
    background_tasks.add_task(_run_media_approval,gid,user["id"],cfg,ideas_json)
    return RedirectResponse(f"/result/{gid}",status_code=303)

@router.get("/download/{gid}")
async def download_pack(request: Request, gid: str):
    """Create and stream a zip of all generated images + caption JSON files."""
    import zipfile, io
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    gen = get_generation(gid, user["id"])
    if not gen or gen["status"] != "completed":
        return JSONResponse({"error": "Generation not found or not completed"}, status_code=404)

    result   = gen.get("result", {}) or {}
    ideas    = result.get("ideas", [])
    results  = result.get("results", [])
    out_dir  = OUTPUT_ROOT / user["id"] / gid

    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # README
        topic_slug = gen["topic"][:40].replace(" ","_").replace("/","_")
        readme = f"""SignalMind Content Pack
Topic: {gen["topic"]}
Generated: {gen["created_at"][:16]}
Platform(s): {", ".join(gen["platforms"])}
Type: {gen["content_type"]}
Ideas: {len(ideas)}
"""
        zf.writestr("README.txt", readme)

        for i, idea in enumerate(ideas):
            folder = f"idea_{i+1}"
            # Caption text file
            if gen["content_type"] == "video":
                hook    = (idea.get("hook") or {})
                hook_txt= hook.get("text","") if isinstance(hook, dict) else str(hook)
                caption = idea.get("caption","")
                hashtags= " ".join(f"#{h}" for h in idea.get("hashtags",[]))
                cta     = (idea.get("cta") or {}).get("text","")
                text_parts = [
                    f"HOOK: {hook_txt}",
                    "",
                    "CAPTION:",
                    caption,
                    "",
                    f"CTA: {cta}",
                    "",
                    f"HASHTAGS: {hashtags}",
                    "",
                    "SCRIPT:",
                ]
                for j, s in enumerate(idea.get("script", [])):
                    sc = s.get("scene", j+1)
                    dur = s.get("duration_seconds", 8)
                    text_parts.append(f"Scene {sc} ({dur}s):")
                    text_parts.append(f"  Visuals: {s.get('visuals', '')}")
                    text_parts.append(f"  Voiceover: {s.get('voiceover', '')}")
                text = "\n".join(text_parts)
            else:
                hook      = idea.get("hook", "")
                post_copy = idea.get("post_copy", "")
                hashtags  = " ".join(f"#{h}" for h in idea.get("hashtags", []))
                img_desc  = idea.get("image_description", "")
                vis_dir   = idea.get("visual_direction", "")
                text = "\n".join([
                    f"HOOK: {hook}", "",
                    "POST COPY:", post_copy, "",
                    f"HASHTAGS: {hashtags}", "",
                    f"IMAGE DESCRIPTION: {img_desc}",
                    f"VISUAL DIRECTION: {vis_dir}",
                ])
            # Caption JSON
            zf.writestr(f"{folder}/idea.json",
                        __import__("json").dumps(idea, ensure_ascii=False, indent=2))

            # Find matching media result
            for r in results:
                if not isinstance(r, dict): continue
                if int(r.get("idea_index", -1)) != i: continue
                # Image
                img = r.get("image_path","")
                if img:
                    img_path = Path(img) if Path(img).is_absolute() else out_dir / Path(img).name
                    if img_path.exists():
                        ext = img_path.suffix or ".png"
                        zf.write(img_path, f"{folder}/image{ext}")
                        added += 1
                # Video
                vid = r.get("video_url","") or r.get("video_path","") or r.get("output_path","")
                if vid:
                    # prefer full video
                    full_vid = out_dir / f"idea_{i+1}_full.mp4"
                    vid_path = full_vid if full_vid.exists() else (
                        Path(vid) if Path(vid).is_absolute() else out_dir / Path(vid).name
                    )
                    if vid_path.exists():
                        zf.write(vid_path, f"{folder}/video.mp4")
                        added += 1

    buf.seek(0)
    slug = gen["topic"][:30].replace(" ","_").replace("/","_")
    fname = f"signalmind_{slug}_{gid[:8]}.zip"
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )

@router.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request, topic: str = "", content_type: str = "",
                        platform: str = "", brand_id: str = ""):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    q             = quota_status(user)
    brand_profile = get_brand_profile(user["id"])
    all_brands    = get_brands(user["id"])
    default_brand = get_default_brand(user["id"])
    # Load saved API key settings
    saved_keys    = get_user_settings(user["id"])
    _sk_gemini    = bool(saved_keys.get("gemini_key",""))
    _sk_or        = bool(saved_keys.get("openrouter_key",""))
    _sk_aiml      = bool(saved_keys.get("aiml_key",""))
    _def_provider    = saved_keys.get("llm_provider","google")
    _def_llm_model   = saved_keys.get("llm_model","gemini-2.5-flash")
    _def_image_model = saved_keys.get("image_model","gemini-3.1-flash-image-preview")
    _def_video_model = saved_keys.get("video_model","google/veo-3.1-i2v")

    _bv_set = bool(all_brands) or any(brand_profile.get(k) for k in ("voice_desc","brand_name","usps","visual_style","target_audience","signature_words"))
    if _bv_set:
        _bv_link = "<a class='auth-link' style='font-size:10px;margin-left:8px;' href='/brands'>Manage brands →</a>"
        if default_brand:
            _bv_name = default_brand["name"]
            _bv_desc = (default_brand["profile"].get("voice_desc") or default_brand["profile"].get("usps","").split("\n")[0] or "Brand profile active")[:160]
        else:
            _bv_name = brand_profile.get("brand_name","Your brand") or "Your brand"
            _bv_desc = (brand_profile.get("voice_desc") or brand_profile.get("usps","").split("\n")[0] or "Brand profile saved")[:160]
        _bv_alert = f'<div class="alert alert-info" style="font-size:12px;padding:10px 14px;">🎨 <strong>{_bv_name}</strong> — {_bv_desc}</div>'
    else:
        _bv_link  = "<a class='auth-link' style='font-size:10px;margin-left:8px;' href='/brands'>Set up brand →</a>"
        _bv_alert = '<div class="alert alert-warn" style="font-size:12px;padding:10px 14px;">No brand voice set. <a class="auth-link" href="/brands">Set it up</a> to dramatically improve output quality.</div>'

    if all_brands:
        brand_options = "".join(
            f'<option value="{b["id"]}" {"selected" if b["is_default"] else ""}>{b["name"]}{"  ✓ default" if b["is_default"] else ""}</option>'
            for b in all_brands
        )
        _brand_selector = f"""<div class="form-group"><label class="form-label">Brand Voice {_bv_link}</label>
          <select class="form-select" name="brand_id"><option value="">— no brand voice —</option>{brand_options}</select>{_bv_alert}</div>"""
    else:
        _brand_selector = f"""<div class="form-group"><label class="form-label">Brand Voice {_bv_link}</label>
          {_bv_alert}<input type="hidden" name="brand_id" value=""/></div>"""

    warn = ""
    if q["remaining"] == 0:
        warn = f'<div class="alert alert-error">✕ Quota exhausted. <a href="/pricing" class="auth-link">Upgrade →</a></div>'
    elif q["remaining"] <= 3:
        warn = f'<div class="alert alert-warn">⚠ Only {q["remaining"]} generation(s) left. <a href="/pricing" class="auth-link">Upgrade →</a></div>'

    platform_chips = "".join(
        f'<div class="platform-chip{"  selected" if p in ["Instagram","LinkedIn"] else ""}" data-platform="{p}" onclick="togglePlatform(this)">{p}</div>'
        for p in PLATFORM_CHOICES
    )
    lang_options = "".join(
        f'<option{"  selected" if l=="English" else ""}>{l}</option>' for l in LANGUAGE_CHOICES
    )
    _keys_hint = '<div class="alert alert-success" style="font-size:11px;padding:8px 12px;margin-bottom:12px;">🔑 ' + (f"{sum([_sk_gemini,_sk_or,_sk_aiml])}/3 API keys saved — leave key fields blank to use them. <a class=\"auth-link\" href=\"/account#api-keys\">Manage →</a>" if any([_sk_gemini,_sk_or,_sk_aiml]) else '<a class="auth-link" href="/account#api-keys">Save your API keys to account →</a> so you never paste them again.') + '</div>'

    _prov_g_active = "active" if _def_provider == "google" else ""
    _prov_or_active = "active" if _def_provider == "openrouter" else ""

    content = f"""
    <div class="topbar">
      <div><div class="topbar-title">✦ Generate Content</div>
      <div class="topbar-sub">AI-powered content for any platform</div></div>
      <span class="badge badge-blue">{q["remaining"]} left this month</span>
    </div>
    <div class="content">
      {warn}
      <div class="grid-2" style="align-items:start;gap:20px;">
        <div>
          <form method="post" action="/generate" id="gen-form">
            <div class="form-group">
              <label class="form-label">Output Type</label>
              <div class="type-toggle">
                <button type="button" class="type-btn active" id="btn-static" onclick="setType('static')">
                  <span class="type-icon">📸</span><span class="type-name">Static Post</span><span class="type-desc">image + caption</span></button>
                <button type="button" class="type-btn" id="btn-video" onclick="setType('video')">
                  <span class="type-icon">🎬</span><span class="type-name">Video</span><span class="type-desc">Veo 3 scene-by-scene</span></button>
              </div>
              <input type="hidden" name="content_type" id="content_type" value="static"/>
            </div>
            <div class="form-group"><label class="form-label">Topic / Product</label>
              <input class="form-input" type="text" name="topic" placeholder="e.g. AI-powered customer support, sustainable sneakers…" required/></div>
            <div class="form-group"><label class="form-label">Target Platforms</label>
              <div class="platform-chips" id="platform-chips">{platform_chips}</div>
              <input type="hidden" name="platforms" id="platforms-hidden" value="Instagram,LinkedIn"/></div>
            <div class="grid-3" style="gap:12px;">
              <div class="form-group" style="margin-bottom:0"><label class="form-label">Language</label>
                <select class="form-select" name="language" id="main-lang-select" onchange="handleLangChange(this.value)">{lang_options}</select>
                <div class="form-hint" id="rtl-hint" style="display:none;color:var(--amber);">⚠ Arabic selected — content will be generated in Arabic (RTL)</div></div>
              <div class="form-group" style="margin-bottom:0"><label class="form-label">Ideas Count</label>
                <select class="form-select" name="number_idea">
                  <option value="1">1 idea</option><option value="2">2 ideas</option>
                  <option value="3" selected>3 ideas</option><option value="5">5 ideas</option></select></div>
              <div class="form-group" style="margin-bottom:0"><label class="form-label">Brand Color</label>
                <input class="form-input" type="color" name="brand_color" value="#4f8ef7" style="height:40px;padding:4px 8px;cursor:pointer;"/></div>
            </div>
            <div class="form-group mt-3"><label class="form-label">Competitor URLs <span class="form-hint" style="display:inline;margin-left:6px;">(one per line)</span></label>
              <textarea class="form-textarea" name="competitor_urls" rows="3" placeholder="https://competitor.com&#10;https://youtube.com/@channel"></textarea></div>
            <div class="form-group mt-3"><label class="form-label">Product / Service Features <span class="form-hint" style="display:inline;margin-left:6px;">one per line</span></label>
              <textarea class="form-textarea" name="product_features" rows="4" placeholder="✦ 48-hour battery life&#10;✦ Waterproof to 50m&#10;✦ Ships free, 30-day returns"></textarea></div>
            {_brand_selector}
            <div class="section-divider">Model Configuration</div>
            <div class="form-group"><label class="form-label">Provider</label>
              <div style="display:flex;gap:8px;">
                <button type="button" class="provider-btn {_prov_g_active}" id="prov-google" onclick="setProvider('google')">🔵 Google (Gemini)</button>
                <button type="button" class="provider-btn {_prov_or_active}" id="prov-openrouter" onclick="setProvider('openrouter')">🟣 OpenRouter</button>
              </div>
              <input type="hidden" name="llm_provider" id="llm_provider" value="{_def_provider}"/>
            </div>
            <div class="model-block" id="block-google">
              <div class="form-group"><label class="form-label">✍️ Text Model</label>
                <div class="model-picker-row">
                  <select class="form-select" id="google-text-preset" onchange="applyPreset('google_text',this.value)">
                    <option value="">— pick a preset —</option>
                    <option value="gemini-2.5-flash">⚡ Gemini 2.5 Flash (recommended)</option>
                    <option value="gemini-2.5-pro">🧠 Gemini 2.5 Pro (best quality)</option>
                    <option value="gemini-2.0-flash">🚀 Gemini 2.0 Flash</option>
                  </select>
                  <input class="form-input" type="text" name="llm_model" id="llm_model" value="{_def_llm_model}" placeholder="or type model name"/>
                </div></div>
              <div class="form-group"><label class="form-label">🎨 Image Model</label>
                <div class="model-picker-row">
                  <select class="form-select" id="google-image-preset" onchange="applyPreset('google_image',this.value)">
                    <option value="">— pick a preset —</option>
                    <option value="gemini-3.1-flash-image-preview">✨ Gemini 3.1 Flash Image (recommended)</option>
                    <option value="imagen-3.0-generate-002">Imagen 3.0</option>
                  </select>
                  <input class="form-input" type="text" name="image_model" id="image_model" value="{_def_image_model}" placeholder="or type model name"/>
                </div></div>
            </div>
            <div class="model-block hidden" id="block-openrouter">
              <div class="form-group"><label class="form-label">✍️ Text Model (OpenRouter)
                <button type="button" class="btn btn-ghost btn-sm" style="margin-left:8px;padding:2px 10px;font-size:10px;" onclick="refreshORModels()">⟳ Refresh</button></label>
                <div class="model-picker-row">
                  <select class="form-select" id="or-text-preset" onchange="applyPreset('or_text',this.value)">
                    <option value="">— pick a preset —</option>
                    <optgroup label="⚡ Free"><option value="google/gemini-2.5-flash:free">Gemini 2.5 Flash (free)</option>
                      <option value="meta-llama/llama-3.3-70b-instruct:free">Llama 3.3 70B (free)</option>
                      <option value="deepseek/deepseek-chat:free">DeepSeek V3 (free)</option></optgroup>
                    <optgroup label="🧠 Premium"><option value="google/gemini-2.5-pro">Gemini 2.5 Pro</option>
                      <option value="anthropic/claude-3.5-sonnet">Claude 3.5 Sonnet</option>
                      <option value="openai/gpt-4o">GPT-4o</option></optgroup>
                  </select>
                  <input class="form-input" type="text" id="or_llm_model" placeholder="or type full model ID" oninput="document.getElementById('llm_model').value=this.value"/>
                </div>
                <div class="form-hint" id="or-models-status"></div></div>
              <div class="form-group"><label class="form-label">🎨 Image Model</label>
                <div class="alert alert-info" style="margin-bottom:0;padding:10px 14px;font-size:12px;">OpenRouter does not support image generation. Images use your Gemini key automatically.</div></div>
            </div>
            <div class="form-group"><label class="form-label">🎬 Video Model</label>
              <div class="model-picker-row">
                <select class="form-select" id="video-model-preset" onchange="applyPreset('video',this.value)">
                  <option value="">— pick a preset —</option>
                  <option value="google/veo-3.1-i2v">🎬 Veo 3.1 Image-to-Video (recommended)</option>
                  <option value="google/veo-3.0-i2v">Veo 3.0 Image-to-Video</option>
                </select>
                <input class="form-input" type="text" name="video_model" id="video_model" value="{_def_video_model}" placeholder="or type model name"/>
              </div></div>
            <div class="section-divider">API Keys <span style="color:var(--text3);font-weight:400;font-family:var(--mono);font-size:9px;">all optional — fall back to .env and saved account keys</span></div>
            {_keys_hint}
            <div class="grid-3" style="gap:12px;">
              <div class="form-group" style="margin-bottom:0"><label class="form-label">✍️ Text Gen Key</label>
                <input class="form-input" type="password" name="llm_api_key" id="llm_api_key" placeholder="{"✓ Saved" if (_sk_gemini or _sk_or) else "Gemini / OpenRouter key"}"/>
                <div class="form-hint">{"✓ Saved — leave blank to use" if (_sk_gemini or _sk_or) else "Content · trends · competitor"}</div></div>
              <div class="form-group" style="margin-bottom:0"><label class="form-label">🎨 Image Gen Key</label>
                <input class="form-input" type="password" name="image_api_key" placeholder="{"✓ Saved" if _sk_gemini else "Gemini key"}"/>
                <div class="form-hint">{"✓ Saved" if _sk_gemini else "Static post image generation"}</div></div>
              <div class="form-group" style="margin-bottom:0"><label class="form-label">🎬 Video Gen Key</label>
                <input class="form-input" type="password" name="video_api_key" placeholder="{"✓ Saved" if _sk_aiml else "AIML API key"}"/>
                <div class="form-hint">{"✓ Saved" if _sk_aiml else "Veo 3 video generation"}</div></div>
            </div>
            <div class="form-group"><label class="form-label">🔑 Quick Fill
              <span class="form-hint" style="display:inline;margin-left:6px;">paste one key and fill all empty fields</span>
              <a class="auth-link" style="font-size:10px;margin-left:8px;" href="/account#api-keys">{"Manage saved keys →" if any([_sk_gemini,_sk_or,_sk_aiml]) else "Save keys to account →"}</a></label>
              <div style="display:flex;gap:8px;">
                <input class="form-input" type="password" id="quick-key-input" placeholder="paste your key here…" style="flex:1;"/>
                <button type="button" class="btn btn-ghost btn-sm" onclick="quickFillKeys()" style="white-space:nowrap;">Fill Empty Keys</button>
              </div></div>
            <div id="video-opts" class="video-opts mt-3 hidden">
              <div class="form-label mb-2">Video Settings</div>
              <div class="grid-2" style="gap:12px;">
                <div class="form-group" style="margin-bottom:0"><label class="form-label">Aspect Ratio</label>
                  <select class="form-select" name="aspect_ratio">
                    <option value="9:16" selected>9:16 — Reels / TikTok</option>
                    <option value="16:9">16:9 — YouTube</option>
                    <option value="1:1">1:1 — Square</option></select></div>
                <div class="form-group" style="margin-bottom:0"><label class="form-label">Product Image URL</label>
                  <input class="form-input" name="image_url" placeholder="https://cdn.example.com/product.jpg"/></div>
              </div></div>
            <div class="section-divider">Options</div>
            <div class="form-group"><label style="display:flex;align-items:center;gap:10px;cursor:pointer;">
              <input type="checkbox" name="human_review" value="1" id="human-review-cb" style="width:16px;height:16px;accent-color:var(--accent);cursor:pointer;"/>
              <span><span style="font-weight:600;font-size:13px;">Human-in-the-loop</span>
              <span style="font-family:var(--mono);font-size:10px;color:var(--text3);margin-left:8px;">optional</span><br>
              <span style="font-size:12px;color:var(--text2);">Review and approve ideas before image/video generation starts</span></span>
            </label></div>
            <button class="btn btn-primary btn-full btn-lg mt-2" type="submit" id="submit-btn" {"disabled" if q["remaining"]==0 else ""}>✦ Generate Content</button>
          </form>
        </div>
        <div style="display:flex;flex-direction:column;gap:14px;">
          <div class="card card-sm"><div class="card-title mb-3">How it works</div>
            {"".join(f'<div class="flex gap-3 items-center mb-2"><div style="width:26px;height:26px;border-radius:50%;background:rgba(79,142,247,0.12);border:1px solid rgba(79,142,247,0.2);display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:10px;color:var(--accent);flex-shrink:0;">{n}</div><div style="font-size:12px;color:var(--text2);">{s}</div></div>' for n,s in [("1","Deep-scrapes competitor URLs → full LLM report"),("2","Scrapes 14+ sources for trend signals"),("3","AI generates platform-optimized content"),("4","Compliance layer reviews every idea"),("5","Images/videos generated after approval")])}
          </div>
          <div class="card card-sm">
            <div class="card-title mb-2">🔑 Account API Keys</div>
            <div style="font-size:12px;color:var(--text2);">Save your keys once in <a class="auth-link" href="/account#api-keys">Account Settings</a> — the form will auto-fill them whenever a field is left blank.</div>
            {"".join([f'<div style="font-size:11px;margin-top:6px;color:var(--green);">✓ {k} key saved</div>' for k,saved in [("Gemini",_sk_gemini),("OpenRouter",_sk_or),("AIML",_sk_aiml)] if saved]) or '<div style="font-size:11px;margin-top:6px;color:var(--text3);">No keys saved yet.</div>'}
          </div>
        </div>
      </div>
    </div>
    <script>
    let selectedPlatforms = new Set(['Instagram','LinkedIn']);
    async function refreshORModels() {{
      const key = document.getElementById('llm_api_key').value.trim() || document.getElementById('quick-key-input').value.trim();
      const status = document.getElementById('or-models-status');
      status.textContent = '⟳ Fetching from OpenRouter…';
      try {{
        const r = await fetch('/api/models/openrouter?api_key=' + encodeURIComponent(key));
        const data = await r.json();
        if (data.error) {{ status.textContent = '✕ ' + data.error; return; }}
        const sel = document.getElementById('or-text-preset');
        sel.innerHTML = '<option value="">— pick a model —</option>';
        for (const grp of data.models) {{
          const og = document.createElement('optgroup'); og.label = grp.group;
          for (const m of grp.models) {{
            const opt = document.createElement('option'); opt.value = m.id; opt.textContent = m.label || m.id; og.appendChild(opt);
          }}
          sel.appendChild(og);
        }}
        const total = data.models.reduce((s,g)=>s+g.models.length,0);
        status.textContent = '✓ Loaded ' + total + ' models' + (data.cached?' (cached)':'');
        toast('OpenRouter models updated ✓', 'success');
      }} catch(e) {{ status.textContent = '✕ Fetch failed: ' + e.message; }}
    }}
    function setProvider(p) {{
      document.getElementById('llm_provider').value = p;
      document.getElementById('prov-google').classList.toggle('active', p==='google');
      document.getElementById('prov-openrouter').classList.toggle('active', p==='openrouter');
      document.getElementById('block-google').classList.toggle('hidden', p!=='google');
      document.getElementById('block-openrouter').classList.toggle('hidden', p!=='openrouter');
    }}
    function applyPreset(type, value) {{
      if (!value) return;
      const map = {{google_text:'llm_model',google_image:'image_model',or_text:'llm_model',video:'video_model'}};
      const fieldId = map[type]; if (fieldId) document.getElementById(fieldId).value = value;
      if (type==='or_text') document.getElementById('or_llm_model').value = value;
    }}
    function quickFillKeys() {{
      const key = document.getElementById('quick-key-input').value.trim(); if (!key) return;
      ['llm_api_key','image_api_key','video_api_key'].forEach(id => {{
        const el = document.getElementById(id) || document.querySelector('[name="'+id+'"]');
        if (el && !el.value) el.value = key;
      }});
      toast('Empty key fields filled ✓', 'success');
    }}
    function setType(type) {{
      document.getElementById('content_type').value=type;
      document.getElementById('btn-static').classList.toggle('active',type==='static');
      document.getElementById('btn-video').classList.toggle('active',type==='video');
      document.getElementById('video-opts').classList.toggle('hidden',type!=='video');
    }}
    function togglePlatform(el) {{
      const p=el.dataset.platform;
      if(selectedPlatforms.has(p)){{selectedPlatforms.delete(p);el.classList.remove('selected');}}
      else{{selectedPlatforms.add(p);el.classList.add('selected');}}
      document.getElementById('platforms-hidden').value=[...selectedPlatforms].join(',');
    }}
    // Apply saved default provider on page load
    setProvider('{_def_provider}');
    document.getElementById('gen-form').addEventListener('submit',function(){{
      const btn=document.getElementById('submit-btn');
      btn.disabled=true; btn.innerHTML='<div class="spinner"></div> Generating…';
    }});
    function handleLangChange(lang) {{
      const hint = document.getElementById('rtl-hint');
      if(hint) hint.style.display = lang.includes('Arabic') ? 'block' : 'none';
    }}
    const _ls = document.getElementById('main-lang-select');
    if(_ls) handleLangChange(_ls.value);
    </script>"""
    return HTMLResponse(_page(content, user, "Generate", "generate"))

@router.post("/generate", response_class=HTMLResponse)
async def generate_post(
    request: Request, background_tasks: BackgroundTasks,
    topic: str = Form(...), content_type: str = Form("static"),
    platforms: str = Form("Instagram,LinkedIn"), language: str = Form("English"),
    number_idea: int = Form(3), brand_color: str = Form("#4f8ef7"),
    brand_id: str = Form(""), competitor_urls: str = Form(""),
    product_features: str = Form(""), llm_provider: str = Form("google"),
    llm_model: str = Form("gemini-2.5-flash"),
    image_model: str = Form("gemini-3.1-flash-image-preview"),
    video_model: str = Form("google/veo-3.1-i2v"),
    llm_api_key: str = Form(""), image_api_key: str = Form(""),
    video_api_key: str = Form(""), image_url: str = Form(""),
    aspect_ratio: str = Form("9:16"), human_review: str = Form(""),
):
    user = get_current_user(request)
    if not user: return RedirectResponse("/login")
    if not quota_ok(user): return RedirectResponse("/pricing?error=quota")

    # Fall back to saved API keys when form fields are blank
    _saved = get_user_settings(user["id"])
    _saved_llm_key = (_saved.get("gemini_key","") if llm_provider == "google" else _saved.get("openrouter_key",""))
    llm_api_key   = llm_api_key   or _saved_llm_key
    image_api_key = image_api_key or _saved.get("gemini_key","")
    video_api_key = video_api_key or _saved.get("aiml_key","")
    llm_model   = llm_model   or _saved.get("llm_model","gemini-2.5-flash")
    image_model = image_model or _saved.get("image_model","gemini-3.1-flash-image-preview")
    video_model = video_model or _saved.get("video_model","google/veo-3.1-i2v")

    platform_list   = [p.strip() for p in platforms.split(",") if p.strip()]
    competitor_list = [u.strip() for u in competitor_urls.splitlines() if u.strip()]
    niche           = detect_niche(topic)

    if brand_id:
        selected_brand = get_brand(brand_id, user["id"])
        brand_profile  = selected_brand["profile"] if selected_brand else {}
    else:
        default_brand = get_default_brand(user["id"])
        brand_profile = default_brand["profile"] if default_brand else get_brand_profile(user["id"])

    cfg = {
        "topic": topic, "platforms": platform_list, "content_type": content_type,
        "language": language, "brand_color": brand_color, "brand_id": brand_id or "",
        "number_idea": max(1, min(5, number_idea)), "niche": niche,
        "competitor_urls": competitor_list,
        "product_features": [f.strip() for f in product_features.splitlines() if f.strip()],
        "brand_profile": brand_profile, "image_url": image_url.strip(),
        "aspect_ratio": aspect_ratio, "llm_provider": llm_provider,
        "llm_model": llm_model, "image_model": image_model, "video_model": video_model,
        "llm_api_key": llm_api_key or "", "image_api_key": image_api_key or "",
        "video_api_key": video_api_key or "", "human_review": human_review == "1",
    }
    gid = create_generation(user["id"], topic, content_type, platform_list, language, cfg)
    background_tasks.add_task(_run_pipeline, gid, user["id"], cfg)
    return RedirectResponse(f"/result/{gid}", status_code=303)