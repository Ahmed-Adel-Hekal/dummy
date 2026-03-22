"""routes/auth.py — Login, register, logout."""

from __future__ import annotations
from fastapi import Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from db import (get_conn, get_user_by_email, create_user, update_last_login, get_user_settings, save_user_settings,
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
from auth import get_current_user, require_user, create_token, verify_password, hash_password, TOKEN_EXPIRE
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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = "", next: str = "/generate"):
    if get_current_user(request): return RedirectResponse("/generate")
    err = f'<div class="alert alert-error">✕ {error}</div>' if error else ""
    html = f"""<div class="auth-wrap"><div class="auth-card">
      <div class="auth-logo"><div class="logo-icon">⚡</div><span class="logo-text">SignalMind</span></div>
      <div class="auth-title">Welcome back</div><div class="auth-sub">Sign in to your workspace</div>
      {err}
      <form method="post" action="/login">
        <input type="hidden" name="next" value="{next}"/>
        <div class="form-group"><label class="form-label">Email</label>
          <input class="form-input" type="email" name="email" placeholder="you@company.com" required autocomplete="email"/></div>
        <div class="form-group"><label class="form-label">Password</label>
          <input class="form-input" type="password" name="password" placeholder="••••••••" required autocomplete="current-password"/></div>
        <button class="btn btn-primary btn-full btn-lg" type="submit">Sign In →</button>
      </form>
      <div class="auth-footer">Don't have an account? <a class="auth-link" href="/register">Create one free</a></div>
    </div></div>"""
    return HTMLResponse(_auth_page(html, "Sign In"))

@router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/generate")):
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return RedirectResponse(f"/login?error=Invalid+email+or+password&next={next}", status_code=303)
    update_last_login(user["id"])
    resp = RedirectResponse(next or "/generate", status_code=303)
    resp.set_cookie("sm_token", create_token(user["id"]), httponly=True, samesite="lax", max_age=TOKEN_EXPIRE*60)
    return resp

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, error: str = ""):
    if get_current_user(request): return RedirectResponse("/generate")
    err = f'<div class="alert alert-error">✕ {error}</div>' if error else ""
    html = f"""<div class="auth-wrap"><div class="auth-card">
      <div class="auth-logo"><div class="logo-icon">⚡</div><span class="logo-text">SignalMind</span></div>
      <div class="auth-title">Create account</div><div class="auth-sub">Start free — no credit card needed</div>
      {err}
      <form method="post" action="/register">
        <div class="form-group"><label class="form-label">Full Name</label>
          <input class="form-input" type="text" name="name" placeholder="Your name" required/></div>
        <div class="form-group"><label class="form-label">Email</label>
          <input class="form-input" type="email" name="email" placeholder="you@company.com" required autocomplete="email"/></div>
        <div class="form-group"><label class="form-label">Password <span style="color:var(--text3);font-size:10px;">(min 8 chars)</span></label>
          <input class="form-input" type="password" name="password" placeholder="••••••••" required minlength="8" autocomplete="new-password"/></div>
        <button class="btn btn-primary btn-full btn-lg" type="submit">Create Free Account →</button>
      </form>
      <div class="auth-footer">Already have an account? <a class="auth-link" href="/login">Sign in</a></div>
    </div></div>"""
    return HTMLResponse(_auth_page(html, "Register"))

@router.post("/register", response_class=HTMLResponse)
async def register_post(name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    if len(password) < 8: return RedirectResponse("/register?error=Password+must+be+at+least+8+characters", status_code=303)
    if get_user_by_email(email): return RedirectResponse("/register?error=Email+already+registered", status_code=303)
    try:
        user = create_user(email, name, password)
        resp = RedirectResponse("/generate", status_code=303)
        resp.set_cookie("sm_token", create_token(user["id"]), httponly=True, samesite="lax", max_age=TOKEN_EXPIRE*60)
        return resp
    except Exception as e:
        return RedirectResponse(f"/register?error={str(e)}", status_code=303)

@router.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("sm_token")
    return resp