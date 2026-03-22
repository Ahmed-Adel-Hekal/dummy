"""routes/api.py — JSON API endpoints, health, admin."""

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
from pathlib import Path
from db import get_brand_profile, save_brand_profile

# ── Module-level logger ───────────────────────────────────────────────────────
logger = logging.getLogger("SignalMind.api")

# ── OpenRouter model cache (module-level, shared across requests) ─────────────
_or_models_cache: dict = {}


@router.get("/api/status/{gid}")
async def api_status(request: Request, gid: str):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    gen = get_generation(gid, user["id"])
    if not gen:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(
        {"status": gen["status"], "error": gen.get("error")},
        headers={"Cache-Control": "no-store, no-cache"},
    )


@router.post("/api/regenerate-idea/{gid}/{idea_idx}")
async def api_regenerate_idea(request: Request, gid: str, idea_idx: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    gen = get_generation(gid, user["id"])
    if not gen or not gen.get("result"):
        return JSONResponse({"error": "Generation not found"}, status_code=404)

    cfg          = gen.get("config", {})
    result       = gen["result"]
    ideas        = result.get("ideas", [])
    content_type = gen["content_type"]

    if idea_idx < 0 or idea_idx >= len(ideas):
        return JSONResponse({"error": f"idea_idx {idea_idx} out of range"}, status_code=400)

    try:
        from agents.content_agent import (ContentAgent, AgentConfig,
                                          build_variation_context,
                                          get_target_duration,
                                          _format_competitor_context,
                                          _build_fallback_payload)
        from media.video_generator import parse_llm_json

        platforms = cfg.get("platforms", gen["platforms"])
        mapped    = ["X" if p in ("Twitter/X", "Twitter", "X") else p for p in platforms]
        platform_literals = [p for p in mapped if p in ("X", "Facebook", "Instagram", "LinkedIn", "TikTok")]

        config = AgentConfig(
            llm_provider    = cfg.get("llm_provider", "google"),
            llm_api_key     = cfg.get("llm_api_key") or None,
            video_content   = (content_type == "video"),
            brand_color     = [cfg.get("brand_color", "#4f8ef7")],
            target_platform = platform_literals,
            model           = cfg.get("llm_model", "gemini-2.5-flash"),
            language        = cfg.get("language", "English"),
            number_idea     = 1,
        )
        agent = ContentAgent(config=config)

        comp  = _format_competitor_context(result.get("competitor_insight"))
        trend = result.get("trend_insight", "")
        if isinstance(trend, dict):
            from core.orchestrator import _format_trend_summary
            trend = _format_trend_summary(trend)

        variation     = build_variation_context()
        duration_info = get_target_duration(platform_literals) if content_type == "video" else None
        agent.generate_prompt(cfg.get("topic", ""), comp, trend,
                              variation=variation, duration_info=duration_info)

        raw    = agent.ask(agent.full_prompt, max_tokens=4096)
        parsed = parse_llm_json(raw) if raw else None
        if not parsed or not isinstance(parsed, dict):
            parsed = _build_fallback_payload(cfg.get("topic", ""), content_type, 1)

        new_ideas = parsed.get("ideas", [])
        if not new_ideas:
            return JSONResponse({"error": "LLM returned no ideas"}, status_code=500)

        ideas[idea_idx]        = new_ideas[0]
        result["ideas"]        = ideas
        result["raw_json"]     = result.get("raw_json", {})
        result["raw_json"]["ideas"] = ideas
        update_generation(gid, gen["status"], result=result)
        return JSONResponse({"ok": True, "idea": new_ideas[0], "idea_idx": idea_idx})

    except Exception as e:
        logger.error("regenerate_idea failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/update-idea/{gid}/{idea_idx}")
async def api_update_idea(request: Request, gid: str, idea_idx: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    gen = get_generation(gid, user["id"])
    if not gen or not gen.get("result"):
        return JSONResponse({"error": "Not found"}, status_code=404)

    result = gen["result"]
    ideas  = result.get("ideas", [])
    if idea_idx < 0 or idea_idx >= len(ideas):
        return JSONResponse({"error": "idea_idx out of range"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    existing = dict(ideas[idea_idx])
    for key, val in body.items():
        if key == "script" and isinstance(val, list):
            old_script = existing.get("script", [])
            new_script = []
            for si, new_scene in enumerate(val):
                base = dict(old_script[si]) if si < len(old_script) else {}
                base.update({k: v for k, v in new_scene.items() if v is not None})
                new_script.append(base)
            existing["script"] = new_script
        elif key == "hook" and isinstance(val, str):
            if isinstance(existing.get("hook"), dict):
                existing["hook"]["text"] = val
            else:
                existing["hook"] = val
        else:
            existing[key] = val

    ideas[idea_idx] = existing
    result["ideas"] = ideas
    if "raw_json" in result and isinstance(result["raw_json"], dict):
        result["raw_json"]["ideas"] = ideas
    update_generation(gid, gen["status"], result=result)
    return JSONResponse({"ok": True, "idea_idx": idea_idx})


@router.post("/api/approve-idea/{gid}/{idea_idx}")
async def api_approve_idea(
    request: Request, gid: str, idea_idx: int,
    background_tasks: BackgroundTasks,
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    gen = get_generation(gid, user["id"])
    if not gen or not gen.get("result"):
        return JSONResponse({"error": "Not found"}, status_code=404)

    result = gen["result"]
    ideas  = result.get("ideas", [])
    if idea_idx < 0 or idea_idx >= len(ideas):
        return JSONResponse({"error": "idea_idx out of range"}, status_code=400)

    cfg = gen.get("config", {})
    background_tasks.add_task(
        _run_single_idea_media, gid, user["id"], idea_idx,
        {"ideas": [ideas[idea_idx]]}, cfg,
    )
    return JSONResponse({"ok": True, "idea_idx": idea_idx, "status": "generating"})


@router.get("/api/idea-status/{gid}/{idea_idx}")
async def api_idea_status(request: Request, gid: str, idea_idx: int):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    gen = get_generation(gid, user["id"])
    if not gen or not gen.get("result"):
        return JSONResponse({"error": "Not found"}, status_code=404)

    results = gen["result"].get("results", [])
    for r in results:
        if isinstance(r, dict) and r.get("idea_index") == idea_idx:
            return JSONResponse(
                {"status": r.get("status", "unknown"), "result": r},
                headers={"Cache-Control": "no-store"},
            )
    return JSONResponse({"status": "pending"}, headers={"Cache-Control": "no-store"})


@router.get("/api/strategy-status/{sid}")
async def api_strategy_status(request: Request, sid: str):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    s = get_strategy(sid, user["id"])
    if not s:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"status": s["status"]}, headers={"Cache-Control": "no-store"})


@router.get("/api/models/openrouter")
async def api_openrouter_models(request: Request, api_key: str = ""):
    import time as _time
    cached = _or_models_cache.get("data")
    ts     = _or_models_cache.get("ts", 0)
    if cached and (_time.time() - ts) < 900:
        return JSONResponse({"models": cached, "cached": True})

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
        models_raw = r.json().get("data", [])
        free = []; paid = []
        for m in models_raw:
            mid   = m.get("id", "")
            name  = m.get("name", mid)
            entry = {"id": mid, "label": name}
            (free if ":free" in mid else paid).append(entry)
        groups = [
            {"group": "Free Models",  "models": free[:20]},
            {"group": "Paid Models",  "models": paid[:40]},
        ]
        _or_models_cache["data"] = groups
        _or_models_cache["ts"]   = _time.time()
        return JSONResponse({"models": groups, "cached": False})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/cache/clear-trends")
async def clear_trend_cache(request: Request):
    """
    Clear the trend intelligence cache so the next generation forces a
    fresh scrape.  Works whether called via fetch() (returns JSON) or via
    an HTML <form> POST (redirects back to the insights page).

    The cache file path is resolved absolutely from the project root so it
    matches exactly what TrendAgent writes, regardless of the working
    directory uvicorn was launched from.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Resolve the same absolute path that TrendAgent uses:
    #   TrendAgent sets self.cache_path = Path("data/processed/trend_cache.json")
    #   relative to cwd — but cwd can vary.  We anchor to this file's location
    #   (routes/api.py is one level below the project root) and walk up.
    _project_root = Path(__file__).resolve().parent.parent
    cache_path    = _project_root / "data" / "processed" / "trend_cache.json"

    deleted = False
    try:
        if cache_path.exists():
            cache_path.unlink()
            deleted = True
            logger.info("Trend cache cleared: %s", cache_path)
        else:
            logger.info("Trend cache clear requested but file not found: %s", cache_path)
    except Exception as exc:
        logger.error("Failed to delete trend cache: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    # If the request came from a browser form, redirect back to insights.
    # fetch() calls send Accept: application/json — return JSON for those.
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True, "cache_cleared": deleted,
                             "path": str(cache_path)})
    return RedirectResponse("/insights?tab=trend&msg=cache_cleared", status_code=303)


@router.get("/api/strategy-bulk-status/{sid}")
async def api_strategy_bulk_status(request: Request, sid: str):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    prog = _bulk_progress.get(sid, {})
    return JSONResponse(prog, headers={"Cache-Control": "no-store"})


@router.get("/")
async def root(request: Request):
    user = get_current_user(request)
    return RedirectResponse("/dashboard" if user else "/login")


@router.get("/health")
async def health():
    import time as _t
    start = _t.perf_counter()
    db_ok = False
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        pass
    db_ms = round((_t.perf_counter() - start) * 1000)
    return JSONResponse({
        "status":  "ok" if db_ok else "degraded",
        "version": "3.0.0",
        "db":      {"ok": db_ok, "latency_ms": db_ms},
    })


@router.get("/admin/stats")
async def admin_stats(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    with get_conn() as conn:
        users      = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
        gens       = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
        month_gens = conn.execute(
            "SELECT COUNT(*) FROM usage WHERE month=?", (current_month(),)
        ).fetchone()[0]
        by_status  = conn.execute(
            "SELECT status,COUNT(*) FROM generations GROUP BY status"
        ).fetchall()
    return JSONResponse({
        "active_users":       users,
        "total_generations":  gens,
        "this_month":         month_gens,
        "by_status":          {r[0]: r[1] for r in by_status},
    })


@router.get("/api/update-quota")
async def api_update_quota(request: Request, plan: str = ""):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if plan not in PLAN_QUOTAS:
        return JSONResponse({"error": "Invalid plan"}, status_code=400)
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan=? WHERE id=?", (plan, user["id"]))
    return JSONResponse({"ok": True, "plan": plan, "limit": PLAN_QUOTAS[plan]})