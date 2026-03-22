"""routes/calendar.py — Calendar view and item management."""

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


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, year: int=0, month: int=0):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    now=datetime.datetime.utcnow()
    year=year or now.year; month=month or now.month
    prev_m=month-1 or 12; prev_y=year-(1 if month==1 else 0)
    next_m=month%12+1; next_y=year+(1 if month==12 else 0)
    items=get_calendar_items(user["id"],year,month)
    import calendar as _cal
    _cal.setfirstweekday(6)  # Sunday first
    weeks=_cal.monthcalendar(year,month)
    today_str=now.strftime("%Y-%m-%d")
    items_by_date: dict={} 
    for item in items:
        d=item.get("publish_date","")[:10]
        items_by_date.setdefault(d,[]).append(item)
    platform_colors={"Instagram":"#e1306c","TikTok":"#000000","LinkedIn":"#0077b5","Twitter/X":"#1da1f2","Facebook":"#1877f2"}
    status_colors={"published":"var(--green)","scheduled":"var(--accent)","draft":"var(--text3)"}
    def _render_item(item):
        color=platform_colors.get(item.get("platform",""),"var(--accent)")
        status_color=status_colors.get(item.get("status","scheduled"),"var(--accent)")
        title=(item.get("title","") or "")[:30]
        iid=item["id"]; plat=title or item.get("platform","")
        tstr=item.get("publish_time","") or ""
        time_badge=f'<span style="font-size:9px;font-family:var(--mono);color:var(--text3);flex-shrink:0;">{tstr[:5]}</span>' if tstr else ""
        return f'<div class="cal-item" onclick="showCalItem(&quot;{iid}&quot;)" style="border-left:2px solid {color};"><span style="width:5px;height:5px;border-radius:50%;background:{status_color};flex-shrink:0;display:inline-block;"></span><span style="font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;">{plat}</span>{time_badge}</div>'
    day_names=["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
    grid_headers="".join(f'<div class="cal-header">{d}</div>' for d in day_names)
    grid_cells=""
    for week in weeks:
        for day in week:
            if day==0:
                grid_cells+='<div class="cal-cell cal-empty"></div>'
            else:
                date_str=f"{year}-{month:02d}-{day:02d}"
                is_today=" cal-today" if date_str==today_str else ""
                day_items=items_by_date.get(date_str,[])
                items_html="".join(_render_item(i) for i in day_items[:4])
                overflow="" if len(day_items)<=4 else f'<div style="font-family:var(--mono);font-size:9px;color:var(--text3);padding:1px 5px;">+{len(day_items)-4} more</div>'
                grid_cells+=f'<div class="cal-cell{is_today}"><div class="cal-day-num">{day}</div>{items_html}{overflow}</div>'
    month_name=datetime.date(year,month,1).strftime("%B %Y")
    item_detail_panel=""
    for item in items:
        idea=item.get("idea",{}); status_opts="".join(f'<option value="{s}" {"selected" if item.get("status")==s else ""}>{s.title()}</option>' for s in ("scheduled","published","draft"))
        item_detail_panel+=f'''
        <div id="panel-{item["id"]}" style="display:none;position:fixed;top:0;right:0;bottom:0;width:340px;background:var(--surface);border-left:1px solid var(--border);padding:24px;overflow-y:auto;z-index:100;">
          <div class="flex items-center justify-between mb-4">
            <div style="font-weight:700;">{item.get("title","")[:60]}</div>
            <button onclick="hideCalItem()" style="background:none;border:none;color:var(--text2);cursor:pointer;font-size:18px;">✕</button>
          </div>
          <div style="font-family:var(--mono);font-size:11px;color:var(--text3);margin-bottom:12px;">
            {item.get("platform","—")} · {item.get("publish_date","")[:10]}
          </div>
          {f'<div style="font-size:12px;color:var(--text2);margin-bottom:12px;">{idea.get("hook","") or idea.get("topic","")}</div>' if idea else ""}
          <div class="form-group">
            <label class="form-label">Status</label>
            <form method="post" action="/calendar/{item["id"]}/status">
              <select class="form-select" name="status" onchange="this.form.submit()" style="margin-bottom:8px;">{status_opts}</select>
            </form>
          </div>
          <div class="flex gap-2 mt-3">
            <a class="btn btn-ghost btn-sm" href="/generate?topic={idea.get("topic","")}&platform={item.get("platform","")}&content_type={item.get("content_type","static")}">✦ Generate now</a>
            <form method="post" action="/calendar/{item["id"]}/delete" onsubmit="return confirm('Delete?')">
              <button class="btn btn-danger btn-sm" type="submit">Delete</button>
            </form>
          </div>
        </div>'''
    content=f"""
    <div class="topbar">
      <div><div class="topbar-title">◫ Calendar</div></div>
      <div class="flex gap-3">
        <a class="btn btn-ghost btn-sm" href="/calendar?year={prev_y}&month={prev_m}">← {datetime.date(prev_y,prev_m,1).strftime("%b")}</a>
        <span style="font-weight:700;font-size:14px;min-width:120px;text-align:center;">{month_name}</span>
        <a class="btn btn-ghost btn-sm" href="/calendar?year={next_y}&month={next_m}">{datetime.date(next_y,next_m,1).strftime("%b")} →</a>
      </div>
    </div>
    <div class="content" style="max-width:none;">
      <div class="cal-grid" style="margin-bottom:20px;">{grid_headers}{grid_cells}</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;">
        <span style="font-family:var(--mono);font-size:11px;color:var(--text3);">{len(items)} posts scheduled this month</span>
        <a class="btn btn-ghost btn-sm" href="/strategy">◐ Generate Strategy</a>
      </div>
    </div>
    {item_detail_panel}
    <script>
    function showCalItem(id){{
      document.querySelectorAll("[id^='panel-']").forEach(p=>p.style.display="none");
      const p=document.getElementById("panel-"+id);
      if(p)p.style.display="block";
    }}
    function hideCalItem(){{
      document.querySelectorAll("[id^='panel-']").forEach(p=>p.style.display="none");
    }}
    document.addEventListener("keydown",e=>{{if(e.key==="Escape")hideCalItem();}});
    </script>"""
    return HTMLResponse(_page(content,user,f"Calendar — {month_name}","calendar"))

@router.post("/calendar/{item_id}/status", response_class=HTMLResponse)
async def calendar_status(request: Request, item_id: str, status: str=Form(...)):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    update_calendar_item_status(item_id,user["id"],status)
    item=get_calendar_item(item_id,user["id"])
    if item:
        d=datetime.date.fromisoformat(item["publish_date"][:10])
        return RedirectResponse(f"/calendar?year={d.year}&month={d.month}",status_code=303)
    return RedirectResponse("/calendar",status_code=303)

@router.post("/calendar/{item_id}/delete", response_class=HTMLResponse)
async def calendar_delete(request: Request, item_id: str):
    user=get_current_user(request)
    if not user: return RedirectResponse("/login")
    item=get_calendar_item(item_id,user["id"])
    delete_calendar_item(item_id,user["id"])
    if item:
        d=datetime.date.fromisoformat(item["publish_date"][:10])
        return RedirectResponse(f"/calendar?year={d.year}&month={d.month}",status_code=303)
    return RedirectResponse("/calendar",status_code=303)