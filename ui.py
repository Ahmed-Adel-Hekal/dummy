"""ui.py — CSS, sidebar, page templates and HTML helper functions."""
from __future__ import annotations
import re
from pathlib import Path
from db import (get_conn, safe_json_loads, quota_status, PLAN_QUOTAS, OUTPUT_ROOT)

# ── URL regex: extracts the "outputs/..." portion from any absolute path ──────
# Matches everything from "outputs/" onward (handles both / and \ separators)
_OUTPUT_URL_RE = re.compile(r'(outputs[/\\].+)', re.IGNORECASE)


def _load_css():
    css_path = Path(__file__).parent / "core" / "_styles.css"
    if css_path.exists():
        return f"<style>{css_path.read_text(encoding='utf-8')}</style>"
    return "<style>body{font-family:sans-serif;background:#05080f;color:#e8edf5;}</style>"

BASE_CSS = _load_css()


def _sidebar_html(user, active="generate"):
    q   = quota_status(user)
    pct = round((q["used"] / max(q["limit"], 1)) * 100)
    bar = ("var(--red)" if pct >= 90 else "var(--amber)" if pct >= 70
           else "linear-gradient(90deg,var(--accent),var(--accent2))")
    nav_items = [
        ("dashboard", "⚡", "Dashboard", "/dashboard"),
        ("generate",  "✦", "Generate",  "/generate"),
        ("strategy",  "◐", "Strategy",  "/strategy"),
        ("insights",  "◎", "Insights",  "/insights"),
        ("calendar",  "◫", "Calendar",  "/calendar"),
        ("history",   "◈", "History",   "/history"),
        ("brands",    "◆", "Brands",    "/brands"),
        ("account",   "◉", "Account",   "/account"),
        ("pricing",   "⬡", "Upgrade",   "/pricing"),
    ]
    nav_html = "".join(
        f'<a class="nav-item {"active" if active==k else ""}" href="{href}">' +
        f'<span class="nav-icon">{icon}</span>{label}</a>'
        for k, icon, label, href in nav_items
    )
    return f"""
    <aside class="sidebar">
      <div class="sidebar-logo">
        <div class="logo-icon">⚡</div>
        <span class="logo-text">SignalMind</span>
        <span class="logo-badge">AI</span>
      </div>
      <div class="quota-pill">
        <div class="quota-label">Monthly Quota</div>
        <div class="quota-bar-wrap">
          <div class="quota-bar" style="width:{pct}%;background:{bar};"></div>
        </div>
        <div class="quota-text">
          <span>{q["used"]} / {q["limit"]} used</span>
          <span class="plan-badge">{q["plan"]}</span>
        </div>
      </div>
      <nav class="sidebar-nav">
        <div class="nav-section">Workspace</div>
        {nav_html}
      </nav>
      <div class="sidebar-footer">
        <div class="user-row">
          <div class="user-avatar">{user["name"][0].upper()}</div>
          <div class="user-info">
            <div class="user-name">{user["name"]}</div>
            <div class="user-plan">{user["plan"]}</div>
          </div>
          <a class="logout-btn" href="/logout">out</a>
        </div>
      </div>
    </aside>"""


def _page(content, user, title="SignalMind", active="generate"):
    return f"""<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{title} — SignalMind</title>{BASE_CSS}
<style>[dir=rtl],.rtl{{direction:rtl;text-align:right;}}.idea-card input,.idea-card textarea{{unicode-bidi:plaintext;direction:auto;}}</style>
</head>
<body>
<div class="orb orb-1"></div><div class="orb orb-2"></div>
<div class="layout">
  {_sidebar_html(user, active)}
  <main class="main">{content}</main>
</div>
<div class="toast-wrap" id="toast-wrap"></div>
<script>
function toast(msg,type='info'){{
  const icons={{success:'✓',error:'✕',info:'◈',warn:'⚠'}};
  const el=document.createElement('div');
  el.className=`toast ${{type}}`;
  el.innerHTML=`<span>${{icons[type]||'◈'}}</span><span>${{msg}}</span>`;
  document.getElementById('toast-wrap').appendChild(el);
  setTimeout(()=>el.remove(),3800);
}}
function _setStatus(gid,idx,html,active){{
  const bar=document.getElementById(`idea-status-${{gid}}-${{idx}}`);
  if(!bar)return;
  bar.innerHTML=html;
  bar.className='idea-status-bar'+(active?' active':'');
}}
async function saveStaticEdits(gid,idx){{
  const hook=(document.getElementById(`hook-${{gid}}-${{idx}}`)||{{}}).value||''
  const copy=(document.getElementById(`copy-${{gid}}-${{idx}}`)||{{}}).value||''
  const imgdesc=(document.getElementById(`imgdesc-${{gid}}-${{idx}}`)||{{}}).value||''
  _setStatus(gid,idx,'<span class="regen-spinner"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;display:inline-block;"></div> Saving…</span>',true);
  try{{
    const r=await fetch(`/api/update-idea/${{gid}}/${{idx}}`,{{method:'POST',headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{hook,post_copy:copy,image_description:imgdesc}})}});
    if(!r.ok)throw new Error(await r.text());
    _setStatus(gid,idx,'<span class="regen-done">✓ Saved</span>',true);
    toast('Edits saved','success');
    setTimeout(()=>_setStatus(gid,idx,'',false),3000);
  }}catch(e){{
    _setStatus(gid,idx,`<span class="regen-err">✕ ${{e.message}}</span>`,true);
    toast('Save failed: '+e.message,'error');
  }}
}}
function _collectScript(gid,ideaIdx){{
  const scenes=[];
  document.querySelectorAll(`.scene-visuals[data-gid="${{gid}}"][data-idea="${{ideaIdx}}"]`).forEach(ta=>{{
    const si=parseInt(ta.dataset.scene);
    if(!scenes[si])scenes[si]={{}};scenes[si].visuals=ta.value;
  }});
  document.querySelectorAll(`.scene-voiceover[data-gid="${{gid}}"][data-idea="${{ideaIdx}}"]`).forEach(ta=>{{
    const si=parseInt(ta.dataset.scene);
    if(!scenes[si])scenes[si]={{}};scenes[si].voiceover=ta.value;
  }});
  return scenes.filter(Boolean);
}}
async function saveScriptChanges(gid,idx){{
  const hook=(document.getElementById(`hook-${{gid}}-${{idx}}`)||{{}}).value||''
  const caption=(document.getElementById(`caption-${{gid}}-${{idx}}`)||{{}}).value||''
  const cta=(document.getElementById(`cta-${{gid}}-${{idx}}`)||{{}}).value||''
  const script=_collectScript(gid,idx);
  _setStatus(gid,idx,'<span class="regen-spinner"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;display:inline-block;"></div> Saving…</span>',true);
  try{{
    const r=await fetch(`/api/update-idea/${{gid}}/${{idx}}`,{{method:'POST',headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{hook,caption,cta:{{text:cta}},script}})}});
    if(!r.ok)throw new Error(await r.text());
    _setStatus(gid,idx,'<span class="regen-done">✓ Script saved</span>',true);
    toast('Script saved','success');
    setTimeout(()=>_setStatus(gid,idx,'',false),3000);
  }}catch(e){{
    _setStatus(gid,idx,`<span class="regen-err">✕ ${{e.message}}</span>`,true);
    toast('Save failed: '+e.message,'error');
  }}
}}
async function regenerateIdea(gid,idx,type){{
  const card=document.getElementById(`idea-card-${{gid}}-${{idx}}`);
  _setStatus(gid,idx,'<span class="regen-spinner"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--amber);border-radius:50%;animation:spin .8s linear infinite;display:inline-block;"></div> Regenerating…</span>',true);
  if(card)card.style.opacity='0.5';
  try{{
    const r=await fetch(`/api/regenerate-idea/${{gid}}/${{idx}}`,{{method:'POST'}});
    const d=await r.json();
    if(!r.ok||d.error)throw new Error(d.error||'Unknown error');
    _setStatus(gid,idx,'<span class="regen-done">✓ New idea ready — reloading…</span>',true);
    toast('Idea regenerated!','success');
    setTimeout(()=>window.location.replace(window.location.href),800);
  }}catch(e){{
    if(card)card.style.opacity='1';
    _setStatus(gid,idx,`<span class="regen-err">✕ ${{e.message}}</span>`,true);
    toast('Regenerate failed: '+e.message,'error');
  }}
}}
async function approveAllIndividual(gid,n){{
  for(let i=0;i<n;i++){{
    try{{
      const r=await fetch(`/api/approve-idea/${{gid}}/${{i}}`,{{method:'POST'}});
      const d=await r.json();
      if(!r.ok||d.error)throw new Error(d.error||'Unknown');
      toast(`Idea ${{i+1}} queued ✓`,'success');
    }}catch(e){{
      toast(`Idea ${{i+1}} failed: ${{e.message}}`,'error');
    }}
    await new Promise(r=>setTimeout(r,800));
  }}
  toast('All ideas queued for generation','success');
  setTimeout(()=>window.location.replace(window.location.href),2000);
}}
async function approveIdea(gid,idx){{
  _setStatus(gid,idx,'<span class="regen-spinner"><div class="spinner" style="width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite;display:inline-block;"></div> Generating media…</span>',true);
  try{{
    const r=await fetch(`/api/approve-idea/${{gid}}/${{idx}}`,{{method:'POST'}});
    const d=await r.json();
    if(!r.ok||d.error)throw new Error(d.error||'Unknown');
    toast('Media generation started!','info');
    _pollIdeaStatus(gid,idx);
  }}catch(e){{
    _setStatus(gid,idx,`<span class="regen-err">✕ ${{e.message}}</span>`,true);
    toast('Failed: '+e.message,'error');
  }}
}}
function _pollIdeaStatus(gid,idx){{
  let stopped=false;
  window.addEventListener('pagehide',()=>stopped=true,{{once:true}});
  function poll(){{
    if(stopped)return;
    fetch(`/api/idea-status/${{gid}}/${{idx}}`,{{cache:'no-store'}})
      .then(r=>r.json())
      .then(d=>{{
        if(d.status==='completed'||d.status==='partial'){{
          _setStatus(gid,idx,'<span class="regen-done">✓ Media ready — reloading…</span>',true);
          toast('Media ready!','success');
          setTimeout(()=>window.location.replace(window.location.href),1200);
        }}else if(d.status==='failed'){{
          _setStatus(gid,idx,'<span class="regen-err">✕ Media generation failed</span>',true);
        }}else{{setTimeout(poll,2000);}}
      }}).catch(()=>{{if(!stopped)setTimeout(poll,3500);}});
  }}
  setTimeout(poll,2000);
}}
</script>
</body></html>"""


def _auth_page(content, title="SignalMind"):
    return f"""<!DOCTYPE html><html lang="en">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{title} — SignalMind</title>{BASE_CSS}</head>
<body>
<div class="orb orb-1"></div><div class="orb orb-2"></div>
{content}
</body></html>"""


def _media_display_html(uid, gid, idea_idx, ct, media):
    if not media:
        return ""
    status = media.get("status", "")
    if status not in ("completed", "partial"):
        return ""

    img_path   = media.get("image_path", "") or ""
    # VideoResult stores local path in video_url; also accept legacy keys
    video_path = (
        media.get("video_path", "") or
        media.get("video_url",  "") or
        media.get("output_path","") or ""
    )

    # For multi-scene video prefer the full joined file
    if ct == "video" and uid and gid:
        _full = OUTPUT_ROOT / uid / gid / f"idea_{idea_idx + 1}_full.mp4"
        if _full.exists():
            video_path = str(_full)

    def _to_url(path: str) -> str:
        """Convert an absolute filesystem path to a web-accessible /outputs/… URL."""
        if not path:
            return ""
        # Normalise backslashes so the regex works on Windows paths too
        normalised = path.replace("\\", "/")
        m = _OUTPUT_URL_RE.search(normalised)
        if m:
            return "/" + m.group(1).replace("\\", "/")
        return ""

    html = '<div class="media-result" style="margin-top:14px;">'

    if ct == "video" and video_path:
        url = _to_url(video_path)
        if url:
            html += (
                f'<video controls style="width:100%;max-height:420px;'
                f'border-radius:8px;background:#000;" src="{url}"></video>'
                f'<a class="btn btn-ghost btn-sm mt-2" href="{url}" download>'
                f'⬇ Download MP4</a>'
            )
    elif img_path:
        url = _to_url(img_path)
        if url:
            html += (
                f'<img src="{url}" alt="Generated image" '
                f'style="width:100%;border-radius:8px;max-height:520px;object-fit:cover;"/>'
                f'<a class="btn btn-ghost btn-sm mt-2" href="{url}" download>'
                f'⬇ Download image</a>'
            )

    if status == "partial":
        html += (
            '<div class="alert alert-warn mt-2" style="font-size:12px;">'
            '⚠ Media partially generated</div>'
        )

    html += '</div>'
    return html


def _build_ideas_html(gen):
    result = gen.get("result", {})
    if not result:
        return ""
    ideas  = result.get("ideas", [])
    ct     = gen["content_type"]
    gid    = gen["id"]
    uid    = gen["user_id"]

    media_by_idx = {}
    for r in result.get("results", []):
        if isinstance(r, dict) and r.get("idea_index") is not None:
            media_by_idx[int(r["idea_index"])] = r

    compliance  = result.get("compliance_report", {})
    comp_badge  = {
        "passed":   "badge-green",
        "sanitized":"badge-amber",
        "adjusted": "badge-red",
    }.get(compliance.get("status", "passed"), "badge-gray")

    header = f'''<div class="flex gap-3 items-center mb-4" style="flex-wrap:wrap;">
      <span class="badge {comp_badge}">🛡 Compliance: {compliance.get("status","passed")}</span>
      <span class="badge badge-gray" style="font-size:9px;">{len(ideas)} idea(s)</span>
    </div>
    <div class="alert alert-info mb-4" style="font-size:12px;">
      ✦ Each idea can be <strong>edited</strong>, <strong>regenerated</strong>,
      or <strong>approved individually</strong> to generate its media.
    </div>'''

    cards = []
    for i, idea in enumerate(ideas):
        if ct == "video":
            hook     = idea.get("hook", {})
            hook_txt = hook.get("text", "") if isinstance(hook, dict) else str(hook)
            caption  = idea.get("caption", "")
            hashtags = idea.get("hashtags", [])
            script   = idea.get("script", [])
            cta      = idea.get("cta", {})
            cta_txt  = cta.get("text", "") if isinstance(cta, dict) else str(cta)

            tags = "".join(
                f'<span class="idea-tag" style="font-family:var(--mono);font-size:10px;'
                f'color:var(--accent2);background:rgba(124,90,240,0.08);'
                f'border:1px solid rgba(124,90,240,0.15);padding:2px 8px;'
                f'border-radius:20px;">{h}</span>'
                for h in hashtags[:6]
            )

            scenes_editor = ""
            for si, s in enumerate(script):
                scenes_editor += f'''
                <div class="scene-editor">
                  <div class="scene-editor-header">
                    <span class="scene-num">Scene {s.get("scene", si+1)}</span>
                    <span class="scene-dur">{s.get("duration_seconds", 8)}s</span>
                  </div>
                  <div class="scene-fields">
                    <div><label class="scene-field-label">Visuals</label>
                      <textarea class="form-textarea scene-visuals"
                        data-gid="{gid}" data-idea="{i}" data-scene="{si}"
                        style="min-height:60px;font-size:12px;">{s.get("visuals","")}</textarea></div>
                    <div><label class="scene-field-label">Voiceover</label>
                      <textarea class="form-textarea scene-voiceover"
                        data-gid="{gid}" data-idea="{i}" data-scene="{si}"
                        style="min-height:50px;font-size:12px;">{s.get("voiceover","")}</textarea></div>
                  </div>
                </div>'''

            cards.append(f'''
            <div class="idea-card" id="idea-card-{gid}-{i}">
              <div class="idea-card-header">
                <div class="flex items-center gap-2">
                  <span style="font-family:var(--mono);font-size:10px;color:var(--accent);">IDEA {i+1}</span>
                  <span class="badge badge-purple" style="font-size:9px;">🎬 VIDEO</span>
                </div>
                <div class="idea-actions">
                  <button class="btn btn-ghost btn-sm" onclick="saveScriptChanges('{gid}',{i})">💾 Save edits</button>
                  <button class="btn btn-ghost btn-sm" onclick="regenerateIdea('{gid}',{i},'video')">⟳ Regenerate</button>
                  <button class="btn btn-green btn-sm" onclick="approveIdea('{gid}',{i})">▶ Generate video</button>
                </div>
              </div>
              <div class="idea-body">
                <div class="idea-field-row"><label class="scene-field-label">Hook</label>
                  <input class="form-input" type="text" id="hook-{gid}-{i}"
                    value="{hook_txt.replace(chr(34),'&quot;')}"
                    style="font-size:13px;font-weight:600;"/></div>
                <div class="idea-field-row"><label class="scene-field-label">Caption</label>
                  <textarea class="form-textarea" id="caption-{gid}-{i}"
                    style="min-height:60px;font-size:12px;">{caption}</textarea></div>
                <div class="idea-field-row mb-3"><label class="scene-field-label">CTA</label>
                  <input class="form-input" type="text" id="cta-{gid}-{i}"
                    value="{cta_txt.replace(chr(34),'&quot;')}" style="font-size:12px;"/></div>
                <div class="scene-editor-wrap">
                  <div class="scene-editor-title">📽 Script — {len(script)} scene(s)</div>
                  {scenes_editor}
                </div>
                <div class="mt-3" style="display:flex;flex-wrap:wrap;gap:5px;">{tags}</div>
                {_media_display_html(uid, gid, i, ct, media_by_idx.get(i))}
              </div>
              <div class="idea-status-bar" id="idea-status-{gid}-{i}"></div>
            </div>''')
        else:
            hook     = idea.get("hook", "")
            copy_    = idea.get("post_copy", "")
            img_desc = idea.get("image_description", "")
            tags = "".join(
                f'<span class="idea-tag" style="font-family:var(--mono);font-size:10px;'
                f'color:var(--accent2);background:rgba(124,90,240,0.08);'
                f'border:1px solid rgba(124,90,240,0.15);padding:2px 8px;'
                f'border-radius:20px;">{h}</span>'
                for h in idea.get("hashtags", [])[:6]
            )
            cards.append(f'''
            <div class="idea-card" id="idea-card-{gid}-{i}">
              <div class="idea-card-header">
                <div class="flex items-center gap-2">
                  <span style="font-family:var(--mono);font-size:10px;color:var(--accent);">IDEA {i+1}</span>
                  <span class="badge badge-blue" style="font-size:9px;">📸 STATIC</span>
                </div>
                <div class="idea-actions">
                  <button class="btn btn-ghost btn-sm" onclick="saveStaticEdits('{gid}',{i})">💾 Save edits</button>
                  <button class="btn btn-ghost btn-sm" onclick="regenerateIdea('{gid}',{i},'static')">⟳ Regenerate</button>
                  <button class="btn btn-green btn-sm" onclick="approveIdea('{gid}',{i})">▶ Generate image</button>
                </div>
              </div>
              <div class="idea-body">
                <div class="idea-field-row"><label class="scene-field-label">Hook</label>
                  <input class="form-input" type="text" id="hook-{gid}-{i}"
                    value="{hook.replace(chr(34),'&quot;')}"
                    style="font-size:13px;font-weight:600;"/></div>
                <div class="idea-field-row"><label class="scene-field-label">Post copy</label>
                  <textarea class="form-textarea" id="copy-{gid}-{i}"
                    style="min-height:80px;font-size:13px;">{copy_}</textarea></div>
                <div class="idea-field-row"><label class="scene-field-label">Image description</label>
                  <textarea class="form-textarea" id="imgdesc-{gid}-{i}"
                    style="min-height:60px;font-size:12px;color:var(--text2);">{img_desc}</textarea></div>
                <div class="mt-3" style="display:flex;flex-wrap:wrap;gap:5px;">{tags}</div>
                {_media_display_html(uid, gid, i, ct, media_by_idx.get(i))}
              </div>
              <div class="idea-status-bar" id="idea-status-{gid}-{i}"></div>
            </div>''')

    return header + '<div style="display:flex;flex-direction:column;gap:20px;">' + "".join(cards) + "</div>"


def _build_competitor_report_html(result, gid=""):
    ci = result.get("competitor_insight", {})
    if not ci or (ci.get("error") and not ci.get("top_hooks")):
        return ""
    hooks    = ci.get("top_hooks", [])
    gaps     = ci.get("gap_opportunities", [])
    patterns = ci.get("content_patterns", [])
    kws      = ci.get("keyword_cloud", [])
    if not (hooks or gaps or patterns):
        return ""
    rows = ""
    if hooks:
        rows += (f'<div class="comp-stat"><strong style="color:var(--accent);">Top Hooks Used</strong>'
                 f'<br>{"<br>".join(f"· {h}" for h in hooks[:4])}</div>')
    if patterns:
        rows += (f'<div class="comp-stat"><strong style="color:var(--accent);">Content Patterns</strong>'
                 f'<br>{"<br>".join(f"· {p}" for p in patterns[:3])}</div>')
    if gaps:
        rows += (f'<div class="comp-stat"><strong style="color:var(--green);">Gap Opportunities</strong>'
                 f'<br>{"<br>".join(f"· {g}" for g in gaps[:3])}</div>')
    if kws:
        rows += f'<div class="comp-stat"><strong>Keywords:</strong> {", ".join(kws[:10])}</div>'
    tone = ci.get("tone_summary", "")
    if tone:
        rows += f'<div class="comp-stat"><strong>Competitor Tone:</strong> {tone}</div>'
    link = (
        f'<a class="btn btn-ghost btn-sm" href="/insights?gen_id={gid}&tab=competitor" '
        f'style="font-size:11px;margin-top:10px;display:inline-flex;">◎ Full Analysis →</a>'
    ) if gid else ""
    return (
        f'<div class="comp-report mb-4">'
        f'<div class="comp-report-title" style="display:flex;justify-content:space-between;align-items:center;">'
        f'🔍 Competitor Intelligence{link}</div>{rows}</div>'
    )


def _get_latest_insights(uid, limit=10):
    """
    Pull the last N completed generations that have any result data.
    Deliberately permissive — we show the generation in the selector even
    if competitor or trend data is empty; the panel renderers handle the
    empty-state gracefully.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, user_id, topic, result_json, created_at
               FROM generations
               WHERE user_id=? AND status='completed'
                 AND result_json IS NOT NULL
               ORDER BY created_at DESC LIMIT ?""",
            (uid, min(limit, 50)),
        ).fetchall()

    out = []
    for r in rows:
        result = safe_json_loads(r["result_json"], {})
        if not result:
            continue

        comp  = result.get("competitor_insight") or {}
        trend = result.get("trend_insight")      or {}

        # Accept any result — even ones with empty insight dicts
        has_comp  = bool(
            comp and isinstance(comp, dict) and
            (comp.get("top_hooks") or comp.get("brand_overview") or comp.get("content_patterns"))
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
            "competitor": comp,
            "trend":      trend,
            "has_comp":   has_comp,
            "has_trend":  has_trend,
        })
        if len(out) >= limit:
            break

    return out


def _render_competitor_panel(ci):
    """Render one full competitor-insight object into HTML."""
    if not ci or not isinstance(ci, dict):
        return (
            '<div class="card card-sm">'
            '<div class="empty-state" style="padding:32px 0;">'
            '<div class="empty-icon">🔍</div>'
            '<div class="empty-text">No competitor data</div>'
            '<div class="empty-sub">Add competitor URLs when generating content to capture intelligence here.</div>'
            '</div></div>'
        )

    # Surface the error reason if present
    if ci.get("error") and not any([
        ci.get("top_hooks"), ci.get("brand_overview"),
        ci.get("content_patterns"), ci.get("gap_opportunities"),
    ]):
        return (
            f'<div class="card card-sm">'
            f'<div class="alert alert-warn">⚠ Competitor analysis ran but returned no data. '
            f'Reason: {ci.get("error","unknown")}</div>'
            f'<div class="empty-sub" style="padding:12px 0;">Try adding direct competitor URLs '
            f'(website, YouTube channel, Instagram profile) when generating.</div>'
            f'</div>'
        )

    brand_overview   = ci.get("brand_overview",   "")
    top_hooks        = ci.get("top_hooks",         [])
    content_patterns = ci.get("content_patterns",  [])
    winning_angles   = ci.get("winning_angles",    [])
    gap_opps         = ci.get("gap_opportunities", [])
    tone_summary     = ci.get("tone_summary",      "")
    keyword_cloud    = ci.get("keyword_cloud",     [])
    cta_patterns     = ci.get("cta_patterns",      [])
    content_ideas    = ci.get("content_ideas",     [])
    audience_signals = ci.get("audience_signals",  "")

    def _list_items(items, color="var(--text2)", limit=8):
        if not items:
            return '<div style="color:var(--text3);font-size:12px;">—</div>'
        return "".join(
            f'<div style="padding:6px 0;border-bottom:1px solid var(--border);'
            f'font-size:13px;color:{color};">'
            f'<span style="color:var(--accent);margin-right:6px;">›</span>'
            f'{str(item)[:160]}</div>'
            for item in items[:limit]
        )

    kw_pills = "".join(
        f'<span style="background:rgba(79,142,247,0.1);color:var(--accent);'
        f'border:1px solid rgba(79,142,247,0.2);padding:3px 10px;border-radius:20px;'
        f'font-size:11px;font-family:var(--mono);">{kw}</span>'
        for kw in keyword_cloud[:18]
    ) if keyword_cloud else '<span style="color:var(--text3);font-size:12px;">—</span>'

    idea_cards = ""
    for idea in content_ideas[:6]:
        idea_cards += (
            f'<div style="background:var(--surface2);border:1px solid var(--border);'
            f'border-radius:var(--r2);padding:12px;margin-bottom:8px;">'
            f'<div style="font-weight:700;font-size:13px;color:var(--text);margin-bottom:4px;">'
            f'{idea.get("hook","")[:100]}</div>'
            f'<div style="font-size:11px;font-family:var(--mono);color:var(--text3);">'
            f'{idea.get("angle","")[:80]} · {idea.get("platform","")}</div></div>'
        )

    return f'''
    {f'<div class="alert alert-info mb-4" style="font-size:13px;">{brand_overview}</div>' if brand_overview else ""}

    <div class="grid-2" style="gap:16px;margin-bottom:16px;">
      <div class="card card-sm">
        <div style="font-size:12px;font-weight:700;color:var(--accent);margin-bottom:8px;letter-spacing:0.5px;">
          🎣 TOP HOOKS COMPETITORS USE
        </div>
        {_list_items(top_hooks, "var(--text)")}
      </div>
      <div class="card card-sm">
        <div style="font-size:12px;font-weight:700;color:var(--green);margin-bottom:8px;letter-spacing:0.5px;">
          🚀 GAP OPPORTUNITIES FOR YOU
        </div>
        {_list_items(gap_opps, "var(--green)")}
      </div>
    </div>

    <div class="grid-2" style="gap:16px;margin-bottom:16px;">
      <div class="card card-sm">
        <div style="font-size:12px;font-weight:700;color:var(--accent2);margin-bottom:8px;">📐 CONTENT PATTERNS</div>
        {_list_items(content_patterns)}
      </div>
      <div class="card card-sm">
        <div style="font-size:12px;font-weight:700;color:var(--amber);margin-bottom:8px;">🎯 WINNING ANGLES</div>
        {_list_items(winning_angles)}
      </div>
    </div>

    {f'<div class="card card-sm mb-4"><div style="font-size:12px;font-weight:700;color:var(--text3);margin-bottom:6px;">🗣 COMPETITOR TONE</div><div style="font-size:13px;color:var(--text2);">{tone_summary}</div></div>' if tone_summary else ""}

    {f'<div class="card card-sm mb-4"><div style="font-size:12px;font-weight:700;color:var(--text3);margin-bottom:8px;">👥 AUDIENCE SIGNALS</div><div style="font-size:13px;color:var(--text2);">{audience_signals}</div></div>' if audience_signals else ""}

    {f'''<div class="card card-sm mb-4">
      <div style="font-size:12px;font-weight:700;color:var(--pink);margin-bottom:6px;">🔥 CTA PATTERNS</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        {"".join(f'<span style="background:rgba(236,72,153,0.1);color:var(--pink);padding:3px 10px;border-radius:20px;font-size:11px;">{c}</span>' for c in cta_patterns[:10])}
      </div>
    </div>''' if cta_patterns else ""}

    <div class="card card-sm mb-4">
      <div style="font-size:12px;font-weight:700;color:var(--text3);margin-bottom:10px;">☁️ KEYWORD CLOUD</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">{kw_pills}</div>
    </div>

    {f'<div class="card card-sm"><div style="font-size:12px;font-weight:700;color:var(--accent);margin-bottom:8px;">💡 CONTENT IDEAS EXTRACTED</div>{idea_cards}</div>' if idea_cards else ""}
    '''


def _render_trend_panel(ti):
    """Render one full trend-insight object into HTML."""
    if not ti or not isinstance(ti, dict):
        return (
            '<div class="card card-sm">'
            '<div class="empty-state" style="padding:32px 0;">'
            '<div class="empty-icon">📈</div>'
            '<div class="empty-text">No trend data</div>'
            '<div class="empty-sub">Trend signals are captured automatically on every generation run.</div>'
            '</div></div>'
        )

    top_trends = ti.get("top_trends", [])
    keywords   = ti.get("keywords",   [])
    cs         = ti.get("confidence_summary", {})

    if not top_trends:
        return (
            '<div class="card card-sm">'
            '<div class="alert alert-warn">Trend scrape ran but returned no signals. '
            'This usually means all scrapers timed out. Try clearing the cache and regenerating.</div>'
            '</div>'
        )

    strength_cfg = {
        "high":   ("var(--red)",    "🔥", "Exploding"),
        "medium": ("var(--amber)", "📈", "Growing"),
        "low":    ("var(--text3)", "〰",  "Stable"),
    }

    trend_cards = ""
    for t in top_trends:
        strength          = t.get("trend_strength", "low")
        color, emoji, label = strength_cfg.get(strength, strength_cfg["low"])
        conf  = t.get("confidence_score", 0)
        topic = t.get("topic", "")[:120]
        angle = t.get("marketing_angle", "")[:140]
        hook  = t.get("hook_style", "")
        fmt   = t.get("content_format", "")
        fcast = t.get("forecast", "")
        fcast_badge = {
            "viral":        '<span style="background:rgba(239,68,68,0.12);color:var(--red);padding:2px 8px;border-radius:20px;font-size:10px;font-family:var(--mono);">🔥 Viral potential</span>',
            "future_trend": '<span style="background:rgba(79,142,247,0.12);color:var(--accent);padding:2px 8px;border-radius:20px;font-size:10px;font-family:var(--mono);">📡 Future trend</span>',
        }.get(fcast, "")

        trend_cards += f'''
        <div style="border:1px solid var(--border);border-radius:var(--r);padding:16px;margin-bottom:10px;
                    border-left:3px solid {color};">
          <div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:8px;">
            <span style="font-size:18px;flex-shrink:0;">{emoji}</span>
            <div style="flex:1;min-width:0;">
              <div style="font-weight:700;font-size:14px;color:var(--text);margin-bottom:2px;">{topic}</div>
              <div style="font-size:12px;color:var(--text2);">{angle}</div>
            </div>
            <div style="flex-shrink:0;text-align:right;">
              <div style="font-family:var(--mono);font-size:18px;font-weight:800;color:{color};">{conf}%</div>
              <div style="font-family:var(--mono);font-size:9px;color:var(--text3);">confidence</div>
            </div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
            <span style="background:rgba(0,0,0,0.2);color:{color};padding:2px 8px;border-radius:20px;
                          font-size:10px;font-family:var(--mono);">{label}</span>
            {f'<span style="background:rgba(124,90,240,0.1);color:var(--accent2);padding:2px 8px;border-radius:20px;font-size:10px;font-family:var(--mono);">hook: {hook}</span>' if hook else ""}
            {f'<span style="background:rgba(99,179,237,0.08);color:var(--text3);padding:2px 8px;border-radius:20px;font-size:10px;font-family:var(--mono);">{fmt}</span>' if fmt else ""}
            {fcast_badge}
          </div>
          <div style="margin-top:10px;">
            <div style="height:4px;background:var(--surface2);border-radius:4px;overflow:hidden;">
              <div style="width:{conf}%;height:100%;background:{color};border-radius:4px;transition:width 0.6s;"></div>
            </div>
          </div>
        </div>'''

    avg   = cs.get("average_score", 0)
    high  = cs.get("high_confidence_count", 0)

    kw_pills = "".join(
        f'<span style="background:rgba(79,142,247,0.08);color:var(--accent);'
        f'border:1px solid rgba(79,142,247,0.18);padding:3px 10px;border-radius:20px;'
        f'font-size:11px;font-family:var(--mono);">{k}</span>'
        for k in keywords[:16]
    ) if keywords else ""

    return f'''
    <div class="card card-sm mb-4">
      <div style="font-size:12px;font-weight:700;color:var(--text3);margin-bottom:12px;letter-spacing:0.5px;">
        📊 CONFIDENCE SUMMARY
      </div>
      <div class="grid-3" style="gap:10px;">
        <div style="text-align:center;padding:12px;background:var(--surface2);border-radius:var(--r2);">
          <div style="font-size:24px;font-weight:800;color:var(--accent);">{avg}%</div>
          <div style="font-size:10px;font-family:var(--mono);color:var(--text3);">AVG CONFIDENCE</div>
        </div>
        <div style="text-align:center;padding:12px;background:var(--surface2);border-radius:var(--r2);">
          <div style="font-size:24px;font-weight:800;color:var(--green);">{high}</div>
          <div style="font-size:10px;font-family:var(--mono);color:var(--text3);">HIGH CONFIDENCE</div>
        </div>
        <div style="text-align:center;padding:12px;background:var(--surface2);border-radius:var(--r2);">
          <div style="font-size:24px;font-weight:800;color:var(--text2);">{len(top_trends)}</div>
          <div style="font-size:10px;font-family:var(--mono);color:var(--text3);">TOTAL TRENDS</div>
        </div>
      </div>
    </div>

    {f'<div class="card card-sm mb-4"><div style="font-size:12px;font-weight:700;color:var(--text3);margin-bottom:8px;">🔑 HOT KEYWORDS</div><div style="display:flex;gap:6px;flex-wrap:wrap;">{kw_pills}</div></div>' if kw_pills else ""}

    <div style="font-size:12px;font-weight:700;color:var(--text3);margin-bottom:10px;letter-spacing:0.5px;">
      📈 TOP TRENDS
    </div>
    {trend_cards}
    '''