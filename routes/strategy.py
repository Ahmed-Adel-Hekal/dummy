"""routes/strategy.py — Strategy management and scheduling."""

from __future__ import annotations
import datetime
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from auth import (get_current_user, hash_password, verify_password,
                  create_token, require_user)
from db import (
    OUTPUT_ROOT, LANGUAGE_CHOICES, PLAN_PRICES, PLAN_QUOTAS, PLATFORM_CHOICES,
    add_calendar_items, cancel_scheduled_generation, create_brand,
    create_generation, create_scheduled_generation, create_strategy,
    delete_brand, delete_calendar_item, detect_niche, get_brand,
    get_brand_profile, get_brands, get_calendar_item, get_calendar_items,
    get_conn, get_default_brand, get_generation, get_scheduled_generations,
    get_strategy, get_user_generations, get_user_settings, get_user_strategies,
    now_iso, current_month, quota_ok, quota_status, record_usage,
    safe_json_loads, save_brand_profile, save_user_settings, set_default_brand,
    update_brand, update_calendar_item_status, update_generation,
    update_strategy,
)
from pipelines import (
    _bulk_progress, _run_media_approval, _run_pipeline,
    _run_single_idea_media, _run_strategy_post,
)
from ui import (
    _auth_page, _build_competitor_report_html, _build_ideas_html,
    _get_latest_insights, _media_display_html, _page,
    _render_competitor_panel, _render_trend_panel, _sidebar_html,
)

router  = APIRouter()
logger  = logging.getLogger("SignalMind.strategy")

# ── i18n strings ─────────────────────────────────────────────────────────────
_T = {
    "en": {
        "strategy":          "Content Strategy",
        "generate_strategy": "✦ Generate Strategy",
        "topic_label":       "Topic / Product",
        "topic_ph":          "AI productivity tools for startups",
        "title_label":       "Strategy Title",
        "title_ph":          "Q1 Launch Campaign",
        "platforms":         "Platforms",
        "duration":          "Duration",
        "brand_voice":       "Brand Voice",
        "no_brand":          "— no brand voice —",
        "content_types":     "Content Types",
        "static":            "📸 Static",
        "video":             "🎬 Video",
        "language":          "Language",
        "competitor_urls":   "Competitor URLs",
        "competitor_ph":     "https://competitor.com\nhttps://youtube.com/@channel",
        "competitor_hint":   "One per line — scrapes hooks, gaps & patterns",
        "ai_provider":       "AI Provider",
        "model":             "Model",
        "api_key":           "API Key",
        "api_key_hint":      "optional",
        "btn_generate":      "◐ Generate Strategy",
        "past_strategies":   "Past Strategies",
        "days":              "days",
        "topic":             "Topic",
        "status":            "Status",
        "created":           "Created",
        "view":              "View →",
        "review_schedule":   "📋 Review & Schedule",
        "no_strategies":     "No strategies yet",
        "trend_note":        "Trend intelligence is fetched automatically",
        "comp_note":         "Add competitor URLs for deeper content angles",
        "dir":               "ltr",
        "font":              "'Outfit', sans-serif",
        "days_7":            "7 days",
        "days_14":           "14 days",
        "days_30":           "30 days",
        "days_60":           "60 days",
        "days_90":           "90 days",
    },
    "ar": {
        "strategy":          "استراتيجية المحتوى",
        "generate_strategy": "✦ إنشاء استراتيجية",
        "topic_label":       "الموضوع / المنتج",
        "topic_ph":          "أدوات الإنتاجية بالذكاء الاصطناعي للشركات الناشئة",
        "title_label":       "عنوان الاستراتيجية",
        "title_ph":          "حملة إطلاق الربع الأول",
        "platforms":         "المنصات",
        "duration":          "المدة",
        "brand_voice":       "صوت العلامة التجارية",
        "no_brand":          "— بدون صوت علامة تجارية —",
        "content_types":     "أنواع المحتوى",
        "static":            "📸 منشور ثابت",
        "video":             "🎬 فيديو",
        "language":          "اللغة",
        "competitor_urls":   "روابط المنافسين",
        "competitor_ph":     "https://competitor.com\nhttps://youtube.com/@channel",
        "competitor_hint":   "رابط واحد في كل سطر — يستخرج الأفكار والفجوات والأنماط",
        "ai_provider":       "مزود الذكاء الاصطناعي",
        "model":             "النموذج",
        "api_key":           "مفتاح API",
        "api_key_hint":      "اختياري",
        "btn_generate":      "◐ إنشاء الاستراتيجية",
        "past_strategies":   "الاستراتيجيات السابقة",
        "days":              "يوم",
        "topic":             "الموضوع",
        "status":            "الحالة",
        "created":           "تاريخ الإنشاء",
        "view":              "عرض →",
        "review_schedule":   "📋 مراجعة وجدولة",
        "no_strategies":     "لا توجد استراتيجيات بعد",
        "trend_note":        "يتم جلب بيانات الترند تلقائيًا",
        "comp_note":         "أضف روابط المنافسين للحصول على زوايا محتوى أعمق",
        "dir":               "rtl",
        "font":              "'Cairo', 'Outfit', sans-serif",
        "days_7":            "7 أيام",
        "days_14":           "14 يومًا",
        "days_30":           "30 يومًا",
        "days_60":           "60 يومًا",
        "days_90":           "90 يومًا",
    },
}

def _t(lang: str) -> dict:
    """Return translation dict for the given language code."""
    return _T["ar"] if lang in ("ar", "arabic", "Egyptian Arabic",
                                "Gulf Arabic", "egyptian arabic", "gulf arabic") else _T["en"]

def _is_arabic(lang: str) -> bool:
    return lang.lower() in ("ar", "arabic", "egyptian arabic", "gulf arabic")


# ── Strategy pipeline (with trends + competitors) ─────────────────────────────
def _run_strategy_pipeline(sid: str, uid: str, cfg: dict):
    """
    Full strategy generation:
      1. Fetch trend intelligence (TrendAgent)
      2. Fetch competitor intelligence if URLs provided (CompetitorAgent)
      3. Inject both into the LLM strategy prompt
      4. Seed calendar items
    Both steps run in parallel before the LLM call.
    """
    try:
        from core.gemini_client import Agent
        from media.video_generator import parse_llm_json
        from core.orchestrator import _format_trend_summary, _format_brand_voice

        saved         = get_user_settings(uid)
        provider      = cfg.get("llm_provider", "google")
        saved_key     = (saved.get("gemini_key", "") if provider == "google"
                         else saved.get("openrouter_key", ""))
        resolved_key  = cfg.get("llm_api_key", "") or saved_key or os.getenv("GEMINI_API_KEY", "")

        agent = Agent(
            provider = provider,
            model    = cfg.get("llm_model", "gemini-2.5-flash")
                       or saved.get("llm_model", "gemini-2.5-flash"),
            api_key  = resolved_key,
        )

        topic          = cfg.get("topic", "")
        brand_id       = cfg.get("brand_id", "")
        duration_days  = cfg.get("duration_days", 30)
        platforms      = cfg.get("platforms", ["Instagram", "LinkedIn"])
        content_types  = cfg.get("content_types", ["static"])
        language       = cfg.get("language", "English")
        competitor_urls= cfg.get("competitor_urls", []) or []
        niche          = cfg.get("niche", detect_niche(topic))

        # ── Brand block ───────────────────────────────────────────────────────
        brand      = get_brand(brand_id, uid) if brand_id else None
        brand_block = _format_brand_voice(brand.get("profile", {}) if brand else {})

        # ── Step 1 & 2 in parallel: trends + competitors ──────────────────────
        trend_summary = ""
        comp_summary  = ""

        def _fetch_trends():
            try:
                from agents.trend_agent import TrendAgent
                result = TrendAgent().analyze(
                    platforms      = platforms,
                    topic          = topic,
                    niche          = niche,
                    llm_provider   = provider,
                    llm_model      = cfg.get("llm_model", "gemini-2.5-flash"),
                    llm_api_key    = resolved_key,
                )
                return _format_trend_summary(result)
            except Exception as exc:
                logger.warning("Strategy trend fetch failed: %s", exc)
                return ""

        def _fetch_competitors():
            if not competitor_urls:
                return ""
            try:
                from agents.competitor_agent import CompetitorAgent
                from scraping.competitor_scraper import CompetitorScraper
                scraper  = CompetitorScraper()
                profiles = []
                with ThreadPoolExecutor(max_workers=min(len(competitor_urls), 4)) as pool:
                    futs = {pool.submit(scraper.scrape, url): url
                            for url in competitor_urls}
                    for fut in _as_completed(futs, timeout=25):
                        try:
                            profiles.append(fut.result(timeout=20).to_dict())
                        except Exception as exc:
                            logger.warning("Competitor scrape failed %s: %s",
                                           futs[fut], exc)
                if not profiles:
                    return ""
                comp_agent = CompetitorAgent(
                    provider = provider,
                    model    = cfg.get("llm_model", "gemini-2.5-flash"),
                    api_key  = resolved_key,
                )
                report = comp_agent.analyze(profiles)
                # Build a compact text summary for the strategy prompt
                lines = ["=== COMPETITOR INTELLIGENCE ==="]
                if report.get("top_hooks"):
                    lines.append("Top hooks competitors use:")
                    lines += [f"  - {h}" for h in report["top_hooks"][:5]]
                if report.get("gap_opportunities"):
                    lines.append("Content gaps you can own:")
                    lines += [f"  - {g}" for g in report["gap_opportunities"][:4]]
                if report.get("content_patterns"):
                    lines.append("Patterns to leverage or differentiate from:")
                    lines += [f"  - {p}" for p in report["content_patterns"][:3]]
                if report.get("tone_summary"):
                    lines.append(f"Competitor tone: {report['tone_summary']}")
                if report.get("keyword_cloud"):
                    lines.append(f"Key terms: {', '.join(report['keyword_cloud'][:10])}")
                lines.append("=== USE THESE TO CREATE A DIFFERENTIATED STRATEGY ===")
                return "\n".join(lines)
            except Exception as exc:
                logger.warning("Strategy competitor fetch failed: %s", exc)
                return ""

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_trend = pool.submit(_fetch_trends)
            fut_comp  = pool.submit(_fetch_competitors)
            trend_summary = fut_trend.result()
            comp_summary  = fut_comp.result()

        logger.info("Strategy %s: trend=%s comp=%s",
                    sid,
                    "✓" if trend_summary else "✗",
                    "✓" if comp_summary  else "✗")

        # ── Arabic / bilingual instruction ────────────────────────────────────
        lang_instruction = ""
        if _is_arabic(language):
            lang_instruction = (
                "\nCRITICAL: All hook, topic, angle and visual_direction text in "
                "daily_posts MUST be written in natural, native-sounding Arabic. "
                "Do NOT use English for any content fields."
            )

        # ── Build the enriched strategy prompt ────────────────────────────────
        context_blocks = []
        if brand_block:
            context_blocks.append(brand_block)
        if trend_summary:
            context_blocks.append(trend_summary)
        if comp_summary:
            context_blocks.append(comp_summary)
        context_section = "\n\n".join(context_blocks)

        prompt = f"""You are a Content Strategy Director and expert social media planner.
Language for all content: {language}{lang_instruction}
Topic / Product: {topic}
Platforms: {", ".join(platforms)}
Duration: {duration_days} days
Content Types: {", ".join(content_types)}

{context_section}

Using the trend intelligence and competitor insights above (if provided), create a
highly differentiated {duration_days}-day content calendar.

Rules:
- Use trending topics and formats to maximize reach
- Exploit the competitor gaps identified — own the angles they are missing
- Vary content formats, hooks and angles across days — no repetition
- Every hook must be scroll-stopping and platform-native
- Write ALL content fields in {language}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "title": "...",
  "overview": "...",
  "daily_posts": [
    {{
      "day": 1,
      "date_offset": 0,
      "platform": "Instagram",
      "content_type": "static",
      "topic": "...",
      "hook": "...",
      "angle": "...",
      "visual_direction": "...",
      "trend_tie_in": "...",
      "competitor_angle": "..."
    }}
  ],
  "key_themes": ["theme1", "theme2"],
  "posting_frequency": "...",
  "success_metrics": ["..."],
  "trend_opportunities": ["..."],
  "competitor_gaps_exploited": ["..."]
}}

Generate exactly {duration_days} daily_posts entries, one per day.
Each post MUST have: day, date_offset, platform, content_type, topic, hook, angle."""

        raw = agent.ask(prompt, max_tokens=8192, temperature=0.75)
        if not raw:
            update_strategy(sid, "failed")
            return

        plan = parse_llm_json(raw)
        if not isinstance(plan, dict):
            update_strategy(sid, "failed")
            return

        # Store trend/competitor summaries inside the plan for display later
        plan["_trend_summary"] = trend_summary
        plan["_comp_summary"]  = comp_summary

        update_strategy(sid, "ready", plan=plan)

        # ── Seed calendar ─────────────────────────────────────────────────────
        brand_id_val = cfg.get("brand_id", "")
        today        = datetime.datetime.utcnow().date()
        cal_items    = []

        for post in plan.get("daily_posts", [])[:duration_days]:
            offset       = int(post.get("date_offset", post.get("day", 1) - 1))
            publish_date = (today + datetime.timedelta(days=offset)).isoformat()
            cal_items.append({
                "strategy_id":  sid,
                "brand_id":     brand_id_val,
                "title":        (post.get("hook", "") or post.get("topic", ""))[:120],
                "platform":     post.get("platform", "Instagram"),
                "content_type": post.get("content_type", "static"),
                "publish_date": publish_date,
                "status":       "scheduled",
                "idea":         post,
            })

        if cal_items:
            add_calendar_items(uid, cal_items)

        logger.info("Strategy %s ready — %d posts, lang=%s, trends=%s, comp=%s",
                    sid, len(cal_items), language,
                    "yes" if trend_summary else "no",
                    "yes" if comp_summary  else "no")

    except Exception as exc:
        logger.error("Strategy pipeline failed: %s", exc)
        update_strategy(sid, "failed")


# ── Strategy list / create page ───────────────────────────────────────────────
@router.get("/strategy", response_class=HTMLResponse)
async def strategy_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    # Detect UI language from query param or cookie (default English)
    ui_lang      = request.query_params.get("lang", request.cookies.get("ui_lang", "en"))
    t            = _t(ui_lang)
    is_ar        = _is_arabic(ui_lang)

    strategies   = get_user_strategies(user["id"])
    all_brands   = get_brands(user["id"])
    saved_keys   = get_user_settings(user["id"])
    _sk_gemini   = bool(saved_keys.get("gemini_key", ""))
    _sk_or       = bool(saved_keys.get("openrouter_key", ""))
    _def_provider= saved_keys.get("llm_provider", "google")
    _def_model   = saved_keys.get("llm_model", "gemini-2.5-flash")
    _key_hint    = ("✓ Saved key — leave blank to use"
                    if (_sk_gemini or _sk_or) else
                    ("Gemini or OpenRouter key" if not is_ar
                     else "مفتاح Gemini أو OpenRouter"))

    brand_options = "".join(
        f'<option value="{b["id"]}" {"selected" if b["is_default"] else ""}>'
        f'{b["name"]}</option>'
        for b in all_brands
    )

    # Language selector options
    lang_options = "".join(
        f'<option value="{lv}" {"selected" if lv == ui_lang else ""}>{ll}</option>'
        for ll, lv in [
            ("English", "en"), ("العربية", "ar"),
            ("مصري", "Egyptian Arabic"), ("خليجي", "Gulf Arabic"),
            ("Français", "fr"), ("Español", "es"), ("Deutsch", "de"),
        ]
    )

    content_lang_options = "".join(
        f'<option value="{lv}" {"selected" if lv == ("Arabic" if is_ar else "English") else ""}>{ll}</option>'
        for ll, lv in [
            ("English", "English"),
            ("Arabic", "Arabic"),
            ("Egyptian Arabic", "Egyptian Arabic"),
            ("Gulf Arabic", "Gulf Arabic"),
            ("French", "French"),
            ("Spanish", "Spanish"),
            ("German", "German"),
        ]
    )

    def _plat_chip(p):
        sel = "  selected" if p in ["Instagram", "LinkedIn"] else ""
        return (f'<div class="platform-chip{sel}" data-platform="{p}" '
                f'onclick="this.classList.toggle(\'selected\');updatePlatforms()">{p}</div>')

    platform_chips = "".join(_plat_chip(p) for p in PLATFORM_CHOICES)

    # Past strategies table
    sb = {
        "ready":      "badge-green",
        "generating": "badge-amber",
        "approved":   "badge-blue",
        "failed":     "badge-red",
        "draft":      "badge-gray",
    }
    strat_rows = ""
    if strategies:
        strat_rows = "".join(
            f'''<tr>
              <td style="font-weight:600;">{s["title"]}</td>
              <td style="font-family:var(--mono);font-size:10px;color:var(--text2);">
                {s["topic"][:50]}</td>
              <td>{s["duration_days"]}{t["days"]}</td>
              <td><span class="badge {sb.get(s["status"],"badge-gray")}">{s["status"]}</span></td>
              <td style="font-family:var(--mono);font-size:10px;color:var(--text3);">
                {s["created_at"][:10]}</td>
              <td>
                <div class="flex gap-2">
                  <a class="btn btn-ghost btn-sm" href="/strategy/{s["id"]}">{t["view"]}</a>
                  {('<a class="btn btn-green btn-sm" href="/strategy/'+s["id"]+'/review">'
                    + t["review_schedule"] + '</a>')
                   if s["status"] == "ready" else ""}
                </div>
              </td>
            </tr>'''
            for s in strategies
        )

    # RTL style overrides
    rtl_style = """
    <style>
      [data-lang="ar"] .form-label { text-align: right; }
      [data-lang="ar"] .form-hint  { text-align: right; }
      [data-lang="ar"] .card-title { text-align: right; }
      [data-lang="ar"] .nav-item   { flex-direction: row-reverse; }
      [data-lang="ar"] .form-input,
      [data-lang="ar"] .form-select,
      [data-lang="ar"] .form-textarea { direction: rtl; text-align: right; }
      [data-lang="ar"] .platform-chips { flex-direction: row-reverse; flex-wrap: wrap; }
      [data-lang="ar"] .type-toggle    { flex-direction: row-reverse; }
      [data-lang="ar"] .hist-table th,
      [data-lang="ar"] .hist-table td  { text-align: right; }
      [data-lang="ar"] .topbar         { flex-direction: row-reverse; }
      [data-lang="ar"] .flex           { flex-direction: row-reverse; }
      [data-lang="ar"] .alert          { text-align: right; flex-direction: row-reverse; }
      @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;900&display=swap');
    </style>""" if is_ar else ""

    content = f"""
    {rtl_style}
    <div class="topbar" dir="{t["dir"]}">
      <div class="topbar-title" style="font-family:{t["font"]};">{t["strategy"]}</div>
      <div class="flex gap-2 items-center">
        <select class="form-select" style="width:130px;padding:5px 10px;font-size:12px;"
                onchange="switchLang(this.value)">{lang_options}</select>
      </div>
    </div>

    <div class="content" dir="{t["dir"]}" data-lang="{"ar" if is_ar else "en"}"
         style="font-family:{t["font"]};">
      <div class="grid-2" style="align-items:start;gap:24px;">

        <!-- ── CREATE FORM ── -->
        <div class="card">
          <div class="card-title mb-4" style="font-family:{t["font"]};">
            {t["generate_strategy"]}
          </div>
          <form method="post" action="/strategy/generate">
            <input type="hidden" name="ui_lang" value="{ui_lang}"/>

            <div class="form-group">
              <label class="form-label">{t["topic_label"]}</label>
              <input class="form-input" type="text" name="topic"
                     placeholder="{t["topic_ph"]}" required
                     style="font-family:{t["font"]};direction:{t["dir"]};"/>
            </div>

            <div class="form-group">
              <label class="form-label">{t["title_label"]}</label>
              <input class="form-input" type="text" name="title"
                     placeholder="{t["title_ph"]}"
                     style="font-family:{t["font"]};direction:{t["dir"]};"/>
            </div>

            <div class="form-group">
              <label class="form-label">{t["language"]}</label>
              <select class="form-select" name="language"
                      style="font-family:{t["font"]};">
                {content_lang_options}
              </select>
            </div>

            <div class="form-group">
              <label class="form-label">{t["platforms"]}</label>
              <div class="platform-chips" id="strategy-platform-chips">
                {platform_chips}
              </div>
              <input type="hidden" name="platforms" id="strategy-platforms"
                     value="Instagram,LinkedIn"/>
            </div>

            <div class="grid-2" style="gap:12px;">
              <div class="form-group">
                <label class="form-label">{t["duration"]}</label>
                <select class="form-select" name="duration_days"
                        style="font-family:{t["font"]};">
                  <option value="7">{t["days_7"]}</option>
                  <option value="14">{t["days_14"]}</option>
                  <option value="30" selected>{t["days_30"]}</option>
                  <option value="60">{t["days_60"]}</option>
                  <option value="90">{t["days_90"]}</option>
                </select>
              </div>
              <div class="form-group">
                <label class="form-label">{t["brand_voice"]}</label>
                <select class="form-select" name="brand_id"
                        style="font-family:{t["font"]};">
                  <option value="">{t["no_brand"]}</option>
                  {brand_options}
                </select>
              </div>
            </div>

            <div class="form-group">
              <label class="form-label">{t["content_types"]}</label>
              <div style="display:flex;gap:12px;flex-direction:{"row-reverse" if is_ar else "row"};">
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;
                              font-family:{t["font"]};">
                  <input type="checkbox" name="content_types" value="static" checked
                         style="accent-color:var(--accent);"/>
                  {t["static"]}
                </label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;
                              font-family:{t["font"]};">
                  <input type="checkbox" name="content_types" value="video"
                         style="accent-color:var(--accent);"/>
                  {t["video"]}
                </label>
              </div>
            </div>

            <!-- ── Competitor URLs ── -->
            <div class="form-group">
              <label class="form-label">{t["competitor_urls"]}</label>
              <textarea class="form-textarea" name="competitor_urls" rows="3"
                        placeholder="{t["competitor_ph"]}"
                        style="font-family:{t["font"]};direction:ltr;"></textarea>
              <div class="form-hint">{t["competitor_hint"]}</div>
            </div>

            <!-- ── Intelligence notice ── -->
            <div class="alert alert-info mb-3" style="font-size:12px;font-family:{t["font"]};">
              📈 {t["trend_note"]} &nbsp;·&nbsp; 🔍 {t["comp_note"]}
            </div>

            <div class="section-divider">{t["ai_provider"]}</div>

            <div class="form-group">
              <div style="display:flex;gap:8px;flex-direction:{"row-reverse" if is_ar else "row"};">
                <button type="button"
                        class="provider-btn {"active" if _def_provider=="google" else ""}"
                        id="sprov-google" onclick="setStratProv('google')">
                  🔵 Google
                </button>
                <button type="button"
                        class="provider-btn {"active" if _def_provider=="openrouter" else ""}"
                        id="sprov-openrouter" onclick="setStratProv('openrouter')">
                  🟣 OpenRouter
                </button>
              </div>
              <input type="hidden" name="llm_provider" id="strat-llm-provider"
                     value="{_def_provider}"/>
            </div>

            <div class="form-group">
              <label class="form-label">{t["model"]}</label>
              <input class="form-input" type="text" name="llm_model"
                     value="{_def_model}" placeholder="gemini-2.5-flash"/>
            </div>

            <div class="form-group">
              <label class="form-label">
                {t["api_key"]}
                <span class="form-hint" style="display:inline;margin-{("right" if is_ar else "left")}:6px;">
                  ({t["api_key_hint"]})
                </span>
              </label>
              <input class="form-input" type="password" name="llm_api_key"
                     placeholder="{"✓ saved — leave blank" if (_sk_gemini or _sk_or) else "Gemini / OpenRouter key"}"/>
              <div class="form-hint">{_key_hint}</div>
            </div>

            <button class="btn btn-primary btn-full" type="submit"
                    style="font-family:{t["font"]};">
              {t["btn_generate"]}
            </button>
          </form>
        </div>

        <!-- ── PAST STRATEGIES ── -->
        <div>
          <div class="card-title mb-3" style="font-family:{t["font"]};">
            {t["past_strategies"]}
          </div>
          {(f'<table class="hist-table"><thead><tr>'
            f'<th>{t["topic_label"]}</th>'
            f'<th>{t["topic"]}</th>'
            f'<th>{t["duration"]}</th>'
            f'<th>{t["status"]}</th>'
            f'<th>{t["created"]}</th>'
            f'<th></th></tr></thead><tbody>{strat_rows}</tbody></table>')
           if strat_rows else
           f'<div class="empty-state"><div class="empty-icon">◐</div>'
           f'<div class="empty-text" style="font-family:{t["font"]};">'
           f'{t["no_strategies"]}</div></div>'}
        </div>

      </div>
    </div>

    <script>
    function setStratProv(p) {{
      document.getElementById("strat-llm-provider").value = p;
      document.getElementById("sprov-google").classList.toggle("active", p === "google");
      document.getElementById("sprov-openrouter").classList.toggle("active", p === "openrouter");
    }}
    function updatePlatforms() {{
      const sel = [...document.querySelectorAll(".platform-chip.selected")]
                    .map(e => e.dataset.platform);
      document.getElementById("strategy-platforms").value = sel.join(",");
    }}
    function switchLang(lang) {{
      window.location.href = "/strategy?lang=" + encodeURIComponent(lang);
    }}
    </script>"""

    resp = HTMLResponse(_page(content, user, t["strategy"], "strategy"))
    resp.set_cookie("ui_lang", ui_lang, max_age=60 * 60 * 24 * 365)
    return resp


# ── POST: create strategy ─────────────────────────────────────────────────────
@router.post("/strategy/generate", response_class=HTMLResponse)
async def strategy_generate_post(
    request:        Request,
    background_tasks: BackgroundTasks,
    topic:          str = Form(...),
    title:          str = Form(""),
    platforms:      str = Form("Instagram,LinkedIn"),
    duration_days:  int = Form(30),
    brand_id:       str = Form(""),
    language:       str = Form("English"),
    competitor_urls:str = Form(""),
    llm_provider:   str = Form("google"),
    llm_model:      str = Form(""),
    llm_api_key:    str = Form(""),
    ui_lang:        str = Form("en"),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    saved         = get_user_settings(user["id"])
    saved_key     = (saved.get("gemini_key", "") if llm_provider == "google"
                     else saved.get("openrouter_key", ""))
    llm_api_key   = llm_api_key or saved_key
    llm_model     = llm_model   or saved.get("llm_model", "gemini-2.5-flash")

    form          = await request.form()
    content_types = form.getlist("content_types") or ["static"]
    platform_list = [p.strip() for p in platforms.split(",") if p.strip()]
    comp_urls     = [u.strip() for u in competitor_urls.splitlines() if u.strip()]
    strat_title   = title.strip() or f"{topic[:50]} — {duration_days}d Strategy"

    sid = create_strategy(user["id"], brand_id, strat_title, topic, duration_days)

    cfg = {
        "topic":           topic,
        "brand_id":        brand_id,
        "platforms":       platform_list,
        "content_types":   content_types,
        "duration_days":   duration_days,
        "language":        language,
        "competitor_urls": comp_urls,
        "niche":           detect_niche(topic),
        "llm_provider":    llm_provider,
        "llm_model":       llm_model,
        "llm_api_key":     llm_api_key,
        "ui_lang":         ui_lang,
    }

    background_tasks.add_task(_run_strategy_pipeline, sid, user["id"], cfg)
    resp = RedirectResponse(f"/strategy/{sid}", status_code=303)
    resp.set_cookie("ui_lang", ui_lang, max_age=60 * 60 * 24 * 365)
    return resp


# ── Strategy detail page ──────────────────────────────────────────────────────
@router.get("/strategy/{sid}", response_class=HTMLResponse)
async def strategy_detail(request: Request, sid: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    s = get_strategy(sid, user["id"])
    if not s:
        return RedirectResponse("/strategy")

    ui_lang = request.query_params.get("lang", request.cookies.get("ui_lang", "en"))
    t       = _t(ui_lang)
    is_ar   = _is_arabic(ui_lang)

    status = s["status"]
    plan   = s.get("plan", {}) or {}
    sb     = {
        "ready":      "badge-green",
        "generating": "badge-amber",
        "approved":   "badge-blue",
        "failed":     "badge-red",
        "draft":      "badge-gray",
    }
    status_badge = sb.get(status, "badge-gray")

    # ── Polling spinner ───────────────────────────────────────────────────────
    poll_html = ""
    if status == "generating":
        poll_html = f"""
        <div class="card mb-4">
          <div class="flex gap-3 items-center">
            <div class="spinner"></div>
            <div>
              <div class="fw-bold" style="font-family:{t["font"]};">
                {"جاري إنشاء الاستراتيجية…" if is_ar else "Generating strategy…"}
              </div>
              <div style="font-size:12px;color:var(--text2);font-family:{t["font"]};">
                {"يتم الآن جلب بيانات الترند والمنافسين ثم توليد الخطة" if is_ar
                 else "Fetching trend + competitor intel, then building your plan…"}
              </div>
            </div>
          </div>
        </div>
        <script>
        (function(){{
          function poll(){{
            fetch('/api/strategy-status/{sid}',{{cache:'no-store'}})
              .then(r=>r.json())
              .then(d=>{{
                if(d.status==='ready'||d.status==='failed')
                  location.replace(location.href);
                else setTimeout(poll,2000);
              }}).catch(()=>setTimeout(poll,3500));
          }}
          setTimeout(poll,2000);
        }})();
        </script>"""

    # ── Intelligence summary cards (if plan has them) ─────────────────────────
    intel_html = ""
    trend_summary = plan.get("_trend_summary", "")
    comp_summary  = plan.get("_comp_summary",  "")
    trend_opps    = plan.get("trend_opportunities", [])
    comp_gaps     = plan.get("competitor_gaps_exploited", [])

    if trend_opps or comp_gaps or trend_summary or comp_summary:
        trend_items = "".join(
            f'<div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;">'
            f'<span style="color:var(--accent);margin-right:6px;">📈</span>{o}</div>'
            for o in (trend_opps or [])[:5]
        ) or (
            f'<div style="font-size:12px;color:var(--text3);">'
            f'{"بيانات الترند مُدمجة في الخطة" if is_ar else "Trend data embedded in plan"}'
            f'</div>'
            if trend_summary else
            f'<div style="font-size:12px;color:var(--text3);">'
            f'{"لا توجد بيانات ترند" if is_ar else "No trend data"}'
            f'</div>'
        )
        comp_items = "".join(
            f'<div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;">'
            f'<span style="color:var(--green);margin-right:6px;">🚀</span>{g}</div>'
            for g in (comp_gaps or [])[:5]
        ) or (
            f'<div style="font-size:12px;color:var(--text3);">'
            f'{"بيانات المنافسين مُدمجة في الخطة" if is_ar else "Competitor gaps embedded in plan"}'
            f'</div>'
            if comp_summary else
            f'<div style="font-size:12px;color:var(--text3);">'
            f'{"أضف روابط منافسين لتحليل أعمق" if is_ar else "Add competitor URLs for deeper analysis"}'
            f'</div>'
        )

        intel_html = f"""
        <div class="grid-2 mb-4" style="gap:14px;">
          <div class="card card-sm">
            <div style="font-size:11px;font-weight:700;color:var(--accent);
                        margin-bottom:8px;font-family:{t["font"]};">
              {"📈 فرص الترند المُدمجة" if is_ar else "📈 TREND OPPORTUNITIES EMBEDDED"}
            </div>
            {trend_items}
          </div>
          <div class="card card-sm">
            <div style="font-size:11px;font-weight:700;color:var(--green);
                        margin-bottom:8px;font-family:{t["font"]};">
              {"🚀 فجوات المنافسين المُستغلة" if is_ar else "🚀 COMPETITOR GAPS EXPLOITED"}
            </div>
            {comp_items}
          </div>
        </div>"""

    # ── Plan overview ─────────────────────────────────────────────────────────
    plan_html = ""
    if plan:
        themes_html = "".join(
            f'<span class="badge badge-blue" style="font-family:{t["font"]};">{th}</span> '
            for th in plan.get("key_themes", [])[:8]
        )
        plan_html = f"""
        <div class="card mb-4">
          <div class="card-title mb-2" style="font-family:{t["font"]};">
            {plan.get("title", "")}
          </div>
          <p style="font-size:13px;color:var(--text2);font-family:{t["font"]};">
            {plan.get("overview", "")}
          </p>
          {f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px;">{themes_html}</div>'
           if themes_html else ""}
        </div>"""

    # ── Posts table ───────────────────────────────────────────────────────────
    posts_html = ""
    posts = plan.get("daily_posts", [])
    if posts:
        rows = []
        for i, p in enumerate(posts[:60]):
            hook_txt   = (p.get("hook", "") or p.get("topic", ""))[:80].replace('"', "&quot;")
            angle_txt  = p.get("angle", "")[:50]
            trend_txt  = p.get("trend_tie_in", "")[:40]
            ct_icon    = "🎬" if p.get("content_type") == "video" else "📸"
            day_num    = p.get("day", i + 1)
            gen_url    = f"/strategy/{sid}/generate-post/{i}"

            trend_badge = (
                f'<span style="font-size:9px;font-family:var(--mono);color:var(--accent);">'
                f'📈 {trend_txt}</span>'
            ) if trend_txt else ""

            rows.append(
                f'<tr>'
                f'<td style="font-family:var(--mono);font-size:10px;text-align:center;">{day_num}</td>'
                f'<td>{p.get("platform","")}</td>'
                f'<td>{ct_icon}</td>'
                f'<td style="font-weight:600;font-size:13px;font-family:{t["font"]};">'
                f'{hook_txt}<br>{trend_badge}</td>'
                f'<td style="font-size:11px;color:var(--text2);font-family:{t["font"]};">'
                f'{angle_txt}</td>'
                f'<td><a class="btn btn-green btn-sm" href="{gen_url}" '
                f'style="white-space:nowrap;font-family:{t["font"]};">'
                f'▶ {"توليد" if is_ar else "Generate"}</a></td>'
                f'</tr>'
            )

        posts_html = (
            f'<div class="card" style="overflow:hidden;padding:0;">'
            f'<table class="hist-table" style="font-size:12px;">'
            f'<thead><tr>'
            f'<th>{"اليوم" if is_ar else "Day"}</th>'
            f'<th>{"المنصة" if is_ar else "Platform"}</th>'
            f'<th>{"النوع" if is_ar else "Type"}</th>'
            f'<th>{"الهوك / الموضوع" if is_ar else "Hook / Topic"}</th>'
            f'<th>{"الزاوية" if is_ar else "Angle"}</th>'
            f'<th></th>'
            f'</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>'
            f'</table></div>'
        )

    _gen_all_btn = (
        f'<a class="btn btn-primary btn-sm" href="/strategy/{sid}/review"'
        f'   style="font-family:{t["font"]};">{t["review_schedule"]}</a>'
        if status == "ready" else ""
    )

    rtl_style = """<style>
      [dir=rtl] .topbar { flex-direction: row-reverse; }
      [dir=rtl] .flex   { flex-direction: row-reverse; }
    </style>""" if is_ar else ""

    content = f"""
    {rtl_style}
    <div class="topbar" dir="{t["dir"]}">
      <div>
        <div class="topbar-title" style="font-family:{t["font"]};">{s["title"]}</div>
        <div class="topbar-sub" style="font-size:10px;color:var(--text3);
                                        font-family:{t["font"]};">
          {s["topic"][:60]}
        </div>
      </div>
      <div class="flex gap-3">
        <span class="badge {status_badge}">{status}</span>
        <a class="btn btn-ghost btn-sm" href="/strategy"
           style="font-family:{t["font"]};">
          {"← رجوع" if is_ar else "← Back"}
        </a>
        <a class="btn btn-ghost btn-sm" href="/calendar">◫</a>
        <a class="btn btn-ghost btn-sm" href="/strategy/{sid}/export.csv">⬇ CSV</a>
        <form method="post" action="/strategy/{sid}/regenerate" style="display:inline;"
              onsubmit="return confirm('{"إعادة توليد الاستراتيجية؟" if is_ar else "Regenerate strategy?"}')">
          <button class="btn btn-amber btn-sm" type="submit"
                  style="padding:6px 14px;font-size:12px;font-family:{t["font"]};">
            ⟳ {"إعادة توليد" if is_ar else "Regenerate"}
          </button>
        </form>
        {_gen_all_btn}
      </div>
    </div>

    <div class="content" dir="{t["dir"]}" style="font-family:{t["font"]};">
      {poll_html}
      {intel_html}
      {plan_html}
      {posts_html}
    </div>"""

    resp = HTMLResponse(_page(content, user, s["title"], "strategy"))
    resp.set_cookie("ui_lang", ui_lang, max_age=60 * 60 * 24 * 365)
    return resp


# ── Generate single post from strategy ───────────────────────────────────────
@router.get("/strategy/{sid}/generate-post/{day_idx}", response_class=HTMLResponse)
async def strategy_generate_post_page(request: Request, sid: str, day_idx: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    s = get_strategy(sid, user["id"])
    if not s:
        return RedirectResponse("/strategy")

    plan  = s.get("plan", {}) or {}
    posts = plan.get("daily_posts", [])
    if day_idx < 0 or day_idx >= len(posts):
        return RedirectResponse(f"/strategy/{sid}")

    post    = posts[day_idx]
    saved   = get_user_settings(user["id"])
    ui_lang = request.query_params.get("lang", request.cookies.get("ui_lang", "en"))
    t       = _t(ui_lang)
    is_ar   = _is_arabic(ui_lang)

    topic   = post.get("hook", "") or post.get("topic", s["topic"])
    platform= post.get("platform", "Instagram")
    ct      = post.get("content_type", "static")
    angle   = post.get("angle", "")
    visual  = post.get("visual_direction", "")
    trend_tie = post.get("trend_tie_in", "")
    comp_angle= post.get("competitor_angle", "")

    _def_prov  = saved.get("llm_provider", "google")
    _def_model = saved.get("llm_model", "gemini-2.5-flash")
    _def_imodel= saved.get("image_model", "gemini-3.1-flash-image-preview")
    _def_vmodel= saved.get("video_model", "google/veo-3.1-i2v")
    _sk_g      = bool(saved.get("gemini_key", ""))
    _sk_or     = bool(saved.get("openrouter_key", ""))

    all_plats  = ["Instagram", "TikTok", "LinkedIn", "Twitter/X", "Facebook"]
    all_langs  = ["English", "Arabic", "Egyptian Arabic", "Gulf Arabic",
                  "French", "Spanish", "German"]

    # Determine content language from strategy config
    cfg_lang = safe_json_loads(
        (get_conn().__enter__().execute(
            "SELECT config_json FROM strategies WHERE id=?", (sid,)
        ).fetchone() or {}).get("config_json", "{}"), {}
    ).get("language", "English") if False else "English"
    # Simple: use ui_lang mapping
    content_lang = "Arabic" if is_ar else "English"

    plat_chips = "".join(
        f'<div class="platform-chip{"  selected" if p == platform else ""}" '
        f'data-platform="{p}" onclick="togglePlatform(this)">{p}</div>'
        for p in all_plats
    )
    lang_opts = "".join(
        f'<option value="{l}"{" selected" if l == content_lang else ""}>{l}</option>'
        for l in all_langs
    )

    topic_safe  = topic.replace('"', "&quot;")
    visual_safe = visual.replace('"', "&quot;").replace("\n", " ")

    # Strategy intel badges shown on the form
    intel_badges = ""
    if trend_tie:
        intel_badges += (
            f'<div class="alert alert-info" style="font-size:11px;padding:8px 12px;margin-bottom:8px;">'
            f'📈 <strong>{"ربط بالترند" if is_ar else "Trend tie-in"}:</strong> {trend_tie}</div>'
        )
    if comp_angle:
        intel_badges += (
            f'<div class="alert alert-info" style="font-size:11px;padding:8px 12px;margin-bottom:8px;">'
            f'🔍 <strong>{"زاوية المنافس" if is_ar else "Competitor angle"}:</strong> {comp_angle}</div>'
        )

    content = f"""
    <div class="topbar" dir="{t["dir"]}">
      <div>
        <div class="topbar-title" style="font-family:{t["font"]};">
          ✦ {"توليد" if is_ar else "Generate"} — {"اليوم" if is_ar else "Day"} {post.get("day", day_idx+1)}
        </div>
        <div class="topbar-sub" style="font-family:var(--mono);">
          {s["title"][:50]} · {platform} · {"فيديو 🎬" if ct=="video" else "منشور ثابت 📸"}
        </div>
      </div>
      <a class="btn btn-ghost btn-sm" href="/strategy/{sid}">
        {"→ رجوع" if is_ar else "← Back"}
      </a>
    </div>

    <div class="content" dir="{t["dir"]}" style="font-family:{t["font"]};">
      {intel_badges}
      <div class="alert alert-info mb-4" style="font-size:12px;padding:10px 14px;">
        📋 {"جميع الحقول مملوءة مسبقًا من الاستراتيجية. عدّل ما تشاء ثم اضغط توليد."
            if is_ar else
            "All fields pre-filled from your strategy including trend and competitor angles. Edit anything, then generate."}
      </div>

      <div class="grid-2" style="align-items:start;gap:20px;">
        <div class="card">
          <form method="post" action="/strategy/{sid}/generate-post/{day_idx}" id="gen-form">
            <input type="hidden" name="content_type"  id="content_type"     value="{ct}"/>
            <input type="hidden" name="platforms"      id="platforms-hidden" value="{platform}"/>
            <input type="hidden" name="llm_provider"   id="llm_provider"     value="{_def_prov}"/>
            <input type="hidden" name="llm_model"      value="{_def_model}"/>
            <input type="hidden" name="image_model"    value="{_def_imodel}"/>
            <input type="hidden" name="video_model"    value="{_def_vmodel}"/>
            <input type="hidden" name="aspect_ratio"   value="9:16"/>
            <input type="hidden" name="trend_tie_in"   value="{trend_tie.replace(chr(34),'&quot;')}"/>
            <input type="hidden" name="competitor_angle" value="{comp_angle.replace(chr(34),'&quot;')}"/>

            <div class="form-group">
              <label class="form-label">{"نوع المحتوى" if is_ar else "Output Type"}</label>
              <div class="type-toggle">
                <button type="button" class="type-btn{"  active" if ct=="static" else ""}"
                        id="btn-static" onclick="setType('static')">
                  <span class="type-icon">📸</span>
                  <span class="type-name" style="font-family:{t["font"]};">{t["static"]}</span>
                </button>
                <button type="button" class="type-btn{"  active" if ct=="video" else ""}"
                        id="btn-video" onclick="setType('video')">
                  <span class="type-icon">🎬</span>
                  <span class="type-name" style="font-family:{t["font"]};">{t["video"]}</span>
                </button>
              </div>
            </div>

            <div class="form-group">
              <label class="form-label" style="font-family:{t["font"]};">{t["topic_label"]}</label>
              <input class="form-input" type="text" name="topic" id="topic-input"
                     value="{topic_safe}" required
                     style="font-size:14px;font-weight:600;font-family:{t["font"]};
                            direction:{t["dir"]};"/>
              {"<div class='form-hint' style='color:var(--accent2);font-family:"+t["font"]+"'>💡 " + ("الزاوية: " if is_ar else "Angle: ") + angle + "</div>" if angle else ""}
            </div>

            {"<div class='form-group'><label class='form-label' style='font-family:"+t["font"]+";'>"+("الاتجاه البصري" if is_ar else "Visual Direction")+"</label><textarea class='form-textarea' name='product_features' rows='2' style='font-size:12px;color:var(--text2);font-family:"+t["font"]+";direction:"+t["dir"]+";'>"+visual_safe+"</textarea></div>" if visual else ""}

            <div class="form-group">
              <label class="form-label" style="font-family:{t["font"]};">{t["platforms"]}</label>
              <div class="platform-chips" id="platform-chips">{plat_chips}</div>
            </div>

            <div class="grid-3" style="gap:12px;">
              <div class="form-group" style="margin-bottom:0">
                <label class="form-label" style="font-family:{t["font"]};">{t["language"]}</label>
                <select class="form-select" name="language" id="lang-select"
                        style="font-family:{t["font"]};">{lang_opts}</select>
              </div>
              <div class="form-group" style="margin-bottom:0">
                <label class="form-label">{"عدد الأفكار" if is_ar else "Ideas"}</label>
                <select class="form-select" name="number_idea">
                  <option value="1" selected>{"فكرة واحدة" if is_ar else "1 idea"}</option>
                  <option value="2">{"فكرتان" if is_ar else "2 ideas"}</option>
                  <option value="3">{"3 أفكار" if is_ar else "3 ideas"}</option>
                </select>
              </div>
              <div class="form-group" style="margin-bottom:0">
                <label class="form-label">{"لون العلامة" if is_ar else "Brand Color"}</label>
                <input class="form-input" type="color" name="brand_color" value="#4f8ef7"
                       style="height:40px;padding:4px 8px;cursor:pointer;"/>
              </div>
            </div>

            <div class="section-divider mt-3">{t["ai_provider"]}</div>
            <div class="form-group">
              <div style="display:flex;gap:8px;flex-direction:{"row-reverse" if is_ar else "row"};">
                <button type="button"
                        class="provider-btn{"  active" if _def_prov=="google" else ""}"
                        id="prov-google" onclick="setProvider('google')">🔵 Google</button>
                <button type="button"
                        class="provider-btn{"  active" if _def_prov=="openrouter" else ""}"
                        id="prov-openrouter" onclick="setProvider('openrouter')">🟣 OpenRouter</button>
              </div>
            </div>

            <div class="grid-2" style="gap:12px;">
              <div class="form-group" style="margin-bottom:0">
                <label class="form-label">✍️ {"مفتاح النص" if is_ar else "Text Key"}</label>
                <input class="form-input" type="password" name="llm_api_key"
                       placeholder="{"✓ محفوظ — اتركه فارغًا" if (_sk_g or _sk_or) else "Gemini / OpenRouter key"}"/>
              </div>
              <div class="form-group" style="margin-bottom:0">
                <label class="form-label">🎨 {"مفتاح الصور" if is_ar else "Image Key"}</label>
                <input class="form-input" type="password" name="image_api_key"
                       placeholder="{"✓ محفوظ" if _sk_g else "Gemini key"}"/>
              </div>
            </div>

            <button class="btn btn-primary btn-full btn-lg mt-4" type="submit"
                    id="submit-btn" style="font-family:{t["font"]};">
              ✦ {"توليد هذا المنشور" if is_ar else "Generate This Post"}
            </button>
          </form>
        </div>

        <!-- Context sidebar -->
        <div style="display:flex;flex-direction:column;gap:14px;">
          <div class="card card-sm">
            <div class="card-title mb-3" style="font-family:{t["font"]};">
              📋 {"سياق الاستراتيجية" if is_ar else "Strategy Context"}
            </div>
            {"".join(
              f'<div style="padding:8px;background:var(--surface2);border-radius:var(--r2);'
              f'border-{"right" if is_ar else "left"}:3px solid var(--accent);margin-bottom:6px;">'
              f'<div style="font-family:var(--mono);font-size:9px;color:var(--text3);'
              f'letter-spacing:1px;text-transform:uppercase;margin-bottom:3px;">{label}</div>'
              f'<div style="font-size:12px;color:var(--text2);font-family:{t["font"]};">{val}</div></div>'
              for label, val in [
                  ("Day" if not is_ar else "اليوم",       str(post.get("day", day_idx+1))),
                  ("Platform" if not is_ar else "المنصة", platform),
                  ("Type" if not is_ar else "النوع",      "Video 🎬" if ct=="video" else "Static 📸"),
              ]
              + ([("Trend" if not is_ar else "ترند", trend_tie[:100])] if trend_tie else [])
              + ([("Angle" if not is_ar else "زاوية", comp_angle[:100])] if comp_angle else [])
              + ([("Visual" if not is_ar else "بصري", visual[:100])] if visual else [])
            )}
          </div>

          <!-- Other posts -->
          <div class="card card-sm">
            <div style="font-size:12px;font-weight:700;color:var(--text2);margin-bottom:8px;
                        font-family:{t["font"]};">
              {"منشورات أخرى" if is_ar else "Other posts"}
            </div>
            {"".join(
              f'<div style="display:flex;justify-content:space-between;align-items:center;'
              f'padding:5px 0;border-bottom:1px solid var(--border);">'
              f'<div><div style="font-size:11px;font-weight:600;font-family:{t["font"]};">'
              f'{"اليوم" if is_ar else "Day"} {pp.get("day",j+1)} · {pp.get("platform","")}</div>'
              f'<div style="font-size:10px;color:var(--text3);font-family:{t["font"]};">'
              f'{(pp.get("hook","") or pp.get("topic",""))[:40]}</div></div>'
              f'<a class="btn btn-ghost btn-sm" href="/strategy/{sid}/generate-post/{j}"'
              f'   style="padding:2px 8px;font-size:10px;">▶</a>'
              f'</div>'
              for j, pp in enumerate(posts[:10]) if j != day_idx
            )}
            {f'<div style="font-size:10px;color:var(--text3);margin-top:5px;">+ {len(posts)-10} more</div>'
             if len(posts) > 10 else ""}
          </div>
        </div>
      </div>
    </div>

    <script>
    function setType(type) {{
      document.getElementById('content_type').value = type;
      document.getElementById('btn-static').classList.toggle('active', type==='static');
      document.getElementById('btn-video').classList.toggle('active',  type==='video');
    }}
    function setProvider(p) {{
      document.getElementById('llm_provider').value = p;
      document.getElementById('prov-google').classList.toggle('active', p==='google');
      document.getElementById('prov-openrouter').classList.toggle('active', p==='openrouter');
    }}
    function togglePlatform(el) {{
      document.querySelectorAll('.platform-chip').forEach(e => e.classList.remove('selected'));
      el.classList.add('selected');
      document.getElementById('platforms-hidden').value = el.dataset.platform;
    }}
    document.getElementById('gen-form').addEventListener('submit', function() {{
      const btn = document.getElementById('submit-btn');
      btn.disabled = true;
      btn.innerHTML = '<div class="spinner"></div> {"جاري التوليد…" if is_ar else "Generating…"}';
    }});
    </script>"""

    return HTMLResponse(_page(content, user,
                               f'Day {post.get("day", day_idx+1)}', "strategy"))


@router.post("/strategy/{sid}/generate-post/{day_idx}", response_class=HTMLResponse)
async def strategy_generate_post_submit(
    request:        Request,
    background_tasks: BackgroundTasks,
    sid:            str,
    day_idx:        int,
    topic:          str = Form(...),
    content_type:   str = Form("static"),
    platforms:      str = Form("Instagram"),
    language:       str = Form("English"),
    number_idea:    int = Form(1),
    brand_color:    str = Form("#4f8ef7"),
    llm_provider:   str = Form("google"),
    llm_model:      str = Form(""),
    image_model:    str = Form(""),
    video_model:    str = Form(""),
    llm_api_key:    str = Form(""),
    image_api_key:  str = Form(""),
    aspect_ratio:   str = Form("9:16"),
    product_features: str = Form(""),
    trend_tie_in:   str = Form(""),
    competitor_angle: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    if not quota_ok(user):
        return RedirectResponse("/pricing?error=quota")

    saved         = get_user_settings(user["id"])
    saved_llm     = (saved.get("gemini_key", "") if llm_provider == "google"
                     else saved.get("openrouter_key", ""))
    llm_api_key   = llm_api_key   or saved_llm
    image_api_key = image_api_key or saved.get("gemini_key", "")
    llm_model     = llm_model     or saved.get("llm_model", "gemini-2.5-flash")
    image_model   = image_model   or saved.get("image_model", "gemini-3.1-flash-image-preview")
    video_model   = video_model   or saved.get("video_model", "google/veo-3.1-i2v")

    platform_list = [p.strip() for p in platforms.split(",") if p.strip()]

    s             = get_strategy(sid, user["id"])
    brand_profile = {}
    if s and s.get("brand_id"):
        b = get_brand(s["brand_id"], user["id"])
        if b:
            brand_profile = b.get("profile", {})

    # Inject trend/competitor angle into product_features so the content
    # agent sees it as context
    features = [f.strip() for f in product_features.splitlines() if f.strip()]
    if trend_tie_in:
        features.insert(0, f"Trend angle to use: {trend_tie_in}")
    if competitor_angle:
        features.insert(0, f"Competitor differentiation angle: {competitor_angle}")

    cfg = {
        "topic":           topic,
        "platforms":       platform_list,
        "content_type":    content_type,
        "language":        language,
        "brand_color":     brand_color,
        "number_idea":     max(1, min(3, number_idea)),
        "niche":           detect_niche(topic),
        "competitor_urls": [],
        "product_features":features,
        "brand_profile":   brand_profile,
        "image_url":       "",
        "aspect_ratio":    aspect_ratio,
        "llm_provider":    llm_provider,
        "llm_model":       llm_model,
        "image_model":     image_model,
        "video_model":     video_model,
        "llm_api_key":     llm_api_key  or "",
        "image_api_key":   image_api_key or "",
        "video_api_key":   saved.get("aiml_key", ""),
        "human_review":    False,
        "strategy_id":     sid,
        "strategy_day":    day_idx,
    }

    gid = create_generation(user["id"], topic, content_type, platform_list, language, cfg)
    background_tasks.add_task(_run_pipeline, gid, user["id"], cfg)
    return RedirectResponse(f"/result/{gid}", status_code=303)


# ── Regenerate strategy ───────────────────────────────────────────────────────
@router.post("/strategy/{sid}/regenerate")
async def strategy_regenerate(
    request: Request, sid: str, background_tasks: BackgroundTasks
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    s = get_strategy(sid, user["id"])
    if not s:
        return RedirectResponse("/strategy")

    with get_conn() as conn:
        conn.execute(
            "UPDATE strategies SET status='generating', plan_json=NULL "
            "WHERE id=? AND user_id=?",
            (sid, user["id"]),
        )

    saved = get_user_settings(user["id"])
    cfg = {
        "topic":           s["topic"],
        "brand_id":        s.get("brand_id", ""),
        "platforms":       ["Instagram", "LinkedIn"],
        "content_types":   ["static"],
        "duration_days":   s["duration_days"],
        "language":        "English",
        "competitor_urls": [],
        "niche":           detect_niche(s["topic"]),
        "llm_provider":    "google",
        "llm_model":       saved.get("llm_model", "gemini-2.5-flash"),
        "llm_api_key":     saved.get("gemini_key", ""),
    }
    background_tasks.add_task(_run_strategy_pipeline, sid, user["id"], cfg)
    return RedirectResponse(f"/strategy/{sid}", status_code=303)


# ── Export CSV ────────────────────────────────────────────────────────────────
@router.get("/strategy/{sid}/export.csv")
async def strategy_export_csv(request: Request, sid: str):
    import csv, io
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    s = get_strategy(sid, user["id"])
    if not s:
        return RedirectResponse("/strategy")

    plan  = s.get("plan", {}) or {}
    posts = plan.get("daily_posts", [])
    buf   = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "day", "date", "platform", "content_type",
        "hook", "angle", "visual_direction", "trend_tie_in", "competitor_angle",
    ], extrasaction="ignore")
    writer.writeheader()
    today = datetime.datetime.utcnow().date()
    for p in posts:
        day      = p.get("day", 1)
        date_str = (today + datetime.timedelta(days=max(0, day - 1))).isoformat()
        writer.writerow({
            "day":               day,
            "date":              date_str,
            "platform":          p.get("platform", ""),
            "content_type":      p.get("content_type", "static"),
            "hook":              p.get("hook", "") or p.get("topic", ""),
            "angle":             p.get("angle", ""),
            "visual_direction":  p.get("visual_direction", ""),
            "trend_tie_in":      p.get("trend_tie_in", ""),
            "competitor_angle":  p.get("competitor_angle", ""),
        })
    buf.seek(0)
    slug = s["title"][:30].replace(" ", "_").replace("/", "_")
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="strategy_{slug}.csv"'},
    )


# ── Review & Schedule (kept from original, unchanged logic) ───────────────────
@router.get("/strategy/{sid}/review", response_class=HTMLResponse)
async def strategy_review_page(request: Request, sid: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    s = get_strategy(sid, user["id"])
    if not s or s["status"] not in ("ready", "approved"):
        return RedirectResponse(f"/strategy/{sid}")

    plan  = s.get("plan", {}) or {}
    posts = plan.get("daily_posts", [])
    if not posts:
        return RedirectResponse(f"/strategy/{sid}")

    ui_lang = request.query_params.get("lang", request.cookies.get("ui_lang", "en"))
    is_ar   = _is_arabic(ui_lang)
    t       = _t(ui_lang)

    q        = quota_status(user)
    quota    = q["remaining"]
    saved    = get_user_settings(user["id"])
    _sk_g    = bool(saved.get("gemini_key", ""))
    _sk_or   = bool(saved.get("openrouter_key", ""))
    _def_prov= saved.get("llm_provider", "google")

    today = datetime.date.today()
    posts_json = json.dumps([
        {
            "idx":      i,
            "day":      p.get("day", i + 1),
            "date":     (today + datetime.timedelta(days=max(0, p.get("day", i+1)-1))).isoformat(),
            "time":     "09:00",
            "platform": p.get("platform", "Instagram"),
            "ct":       p.get("content_type", "static"),
            "topic":    (p.get("hook", "") or p.get("topic", ""))[:200],
            "angle":    p.get("angle", "")[:100],
            "trend":    p.get("trend_tie_in", "")[:80],
        }
        for i, p in enumerate(posts)
    ])
    platforms_json = json.dumps(["Instagram", "TikTok", "LinkedIn", "Twitter/X", "Facebook"])

    content = f"""
    <div class="topbar" dir="{t["dir"]}">
      <div>
        <div class="topbar-title" style="font-family:{t["font"]};">{t["review_schedule"]}</div>
        <div class="topbar-sub">{s["title"]} · {len(posts)} {"منشور" if is_ar else "posts"}</div>
      </div>
      <div class="flex gap-3">
        <a class="btn btn-ghost btn-sm" href="/strategy/{sid}">
          {"→ رجوع" if is_ar else "← Back"}
        </a>
        <span class="badge badge-blue" id="quota-badge">
          {quota} {"فتحة متاحة" if is_ar else "slots available"}
        </span>
      </div>
    </div>

    <div class="content" dir="{t["dir"]}" style="max-width:none;font-family:{t["font"]};">

      <div class="card mb-4 card-sm" style="background:var(--surface2);">
        <div class="flex gap-4 items-center" style="flex-wrap:wrap;">
          <div class="flex gap-2 items-center">
            <span style="font-family:var(--mono);font-size:10px;color:var(--text3);">
              {"المزود" if is_ar else "PROVIDER"}
            </span>
            <button type="button" class="provider-btn active" id="prov-google"
                    onclick="setProvider('google')">🔵 Google</button>
            <button type="button" class="provider-btn" id="prov-openrouter"
                    onclick="setProvider('openrouter')">🟣 OpenRouter</button>
          </div>
          <div class="flex gap-2 items-center">
            <span style="font-family:var(--mono);font-size:10px;color:var(--text3);">
              {"مفتاح النص" if is_ar else "TEXT KEY"}
            </span>
            <input class="form-input" type="password" id="key-llm"
                   placeholder="{"✓ محفوظ" if (_sk_g or _sk_or) else "Gemini / OpenRouter"}"
                   style="width:200px;padding:6px 12px;"/>
          </div>
          <div class="flex gap-2 items-center">
            <span style="font-family:var(--mono);font-size:10px;color:var(--text3);">
              {"مفتاح الصور" if is_ar else "IMAGE KEY"}
            </span>
            <input class="form-input" type="password" id="key-img"
                   placeholder="{"✓ محفوظ" if _sk_g else "Gemini"}"
                   style="width:180px;padding:6px 12px;"/>
          </div>
          <div class="flex gap-2 items-center" style="margin-{("right" if is_ar else "left")}:auto;">
            <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;">
              <input type="checkbox" id="select-all" checked
                     onchange="toggleAll(this.checked)"
                     style="accent-color:var(--accent);"/>
              {"اختيار الكل" if is_ar else "Select all"}
            </label>
            <button class="btn btn-primary" id="schedule-btn" onclick="submitSchedule()"
                    style="font-family:{t["font"]};">
              📅 {"جدولة المختارة" if is_ar else "Schedule Selected"}
            </button>
          </div>
        </div>
      </div>

      <div id="quota-warn" class="alert alert-warn mb-3 hidden"></div>

      <div class="card" style="padding:0;overflow:hidden;">
        <div style="overflow-x:auto;">
          <table class="hist-table" id="review-table" style="min-width:900px;">
            <thead>
              <tr>
                <th style="width:36px;">
                  <input type="checkbox" id="th-check" checked
                         onchange="toggleAll(this.checked)"
                         style="accent-color:var(--accent);"/>
                </th>
                <th>{"اليوم" if is_ar else "Day"}</th>
                <th>{"التاريخ" if is_ar else "Date"}</th>
                <th>{"الوقت" if is_ar else "Time"}</th>
                <th>{"المنصة" if is_ar else "Platform"}</th>
                <th>{"النوع" if is_ar else "Type"}</th>
                <th>{"الموضوع / الهوك" if is_ar else "Topic / Hook"}</th>
                <th>{"الحصة" if is_ar else "Quota"}</th>
              </tr>
            </thead>
            <tbody id="review-body"></tbody>
          </table>
        </div>
        <div style="padding:12px 16px;border-top:1px solid var(--border);
                    display:flex;align-items:center;justify-content:space-between;">
          <span style="font-size:12px;color:var(--text2);" id="summary-text"></span>
          <button class="btn btn-primary" id="schedule-btn-2" onclick="submitSchedule()"
                  style="font-family:{t["font"]};">
            📅 {"جدولة المختارة" if is_ar else "Schedule Selected Posts"}
          </button>
        </div>
      </div>

      <form method="post" action="/strategy/{sid}/schedule-all"
            id="schedule-form" style="display:none;">
        <input type="hidden" name="rows_json"     id="rows_json"/>
        <input type="hidden" name="llm_provider"  id="llm_provider" value="{_def_prov}"/>
        <input type="hidden" name="llm_api_key"   id="llm_api_key_hidden"/>
        <input type="hidden" name="image_api_key" id="image_api_key_hidden"/>
      </form>
    </div>

    <script>
    const POSTS  = {posts_json};
    const PLATS  = {platforms_json};
    const QUOTA  = {quota};
    const IS_AR  = {"true" if is_ar else "false"};
    let rows     = POSTS.map(p => ({{...p, enabled: true, within_quota: true}}));

    function recount() {{
      let n = 0;
      rows.forEach(r => {{
        if (!r.enabled) return;
        n++;
        r.within_quota = (n <= QUOTA);
      }});
      updateSummary();
      updateQuotaWarn();
    }}

    function updateSummary() {{
      const sel = rows.filter(r => r.enabled).length;
      const ok  = rows.filter(r => r.enabled && r.within_quota).length;
      const skip= sel - ok;
      document.getElementById('summary-text').textContent =
        sel + (IS_AR ? ' محدد · ' : ' selected · ') + ok +
        (IS_AR ? ' ضمن الحصة' : ' within quota') +
        (skip ? (IS_AR ? ' · ' + skip + ' تجاوز الحصة' : ' · ' + skip + ' over quota') : '');
    }}

    function updateQuotaWarn() {{
      const over  = rows.filter(r => r.enabled && !r.within_quota).length;
      const warn  = document.getElementById('quota-warn');
      const badge = document.getElementById('quota-badge');
      const used  = rows.filter(r => r.enabled && r.within_quota).length;
      badge.textContent = used + ' / ' + QUOTA + (IS_AR ? ' فتحة' : ' slots used');
      if (over > 0) {{
        warn.textContent = (IS_AR ? '⚠ ' + over + ' منشور يتجاوز حصتك وسيتم تخطيه.' :
          '⚠ ' + over + ' post(s) beyond your quota and will be skipped.');
        warn.classList.remove('hidden');
      }} else {{
        warn.classList.add('hidden');
      }}
    }}

    function renderTable() {{
      const tbody = document.getElementById('review-body');
      tbody.innerHTML = '';
      rows.forEach((row, i) => {{
        const over = row.enabled && !row.within_quota;
        const tr   = document.createElement('tr');
        tr.style.opacity = over ? '0.45' : '1';
        tr.innerHTML = `
          <td><input type="checkbox" ${{row.enabled ? 'checked' : ''}}
                onchange="setEnabled(${{i}}, this.checked)"
                style="accent-color:var(--accent);"/></td>
          <td style="font-family:var(--mono);font-size:11px;text-align:center;">${{row.day}}</td>
          <td><input type="date" class="form-input" value="${{row.date}}"
                style="padding:5px 8px;font-size:12px;"
                onchange="rows[${{i}}].date=this.value;recount()"/></td>
          <td><input type="time" class="form-input" value="${{row.time}}"
                style="padding:5px 8px;font-size:12px;"
                onchange="rows[${{i}}].time=this.value"/></td>
          <td>
            <select class="form-select" style="padding:5px 8px;font-size:12px;"
                onchange="rows[${{i}}].platform=this.value">
              ${{PLATS.map(p=>`<option value="${{p}}" ${{p===row.platform?'selected':''}}>${{p}}</option>`).join('')}}
            </select>
          </td>
          <td>
            <div style="display:flex;gap:4px;">
              <button type="button"
                style="padding:4px 8px;font-size:11px;border-radius:6px;border:1px solid var(--border);
                       cursor:pointer;background:${{row.ct==='static'?'rgba(79,142,247,0.15)':'none'}};
                       color:${{row.ct==='static'?'var(--accent)':'var(--text2)'}};"
                onclick="rows[${{i}}].ct='static';renderTable()">📸</button>
              <button type="button"
                style="padding:4px 8px;font-size:11px;border-radius:6px;border:1px solid var(--border);
                       cursor:pointer;background:${{row.ct==='video'?'rgba(124,90,240,0.15)':'none'}};
                       color:${{row.ct==='video'?'var(--accent2)':'var(--text2)'}};"
                onclick="rows[${{i}}].ct='video';renderTable()">🎬</button>
            </div>
          </td>
          <td>
            <input type="text" class="form-input" value="${{row.topic.replace(/"/g,'&quot;')}}"
                style="padding:5px 8px;font-size:12px;"
                onchange="rows[${{i}}].topic=this.value"/>
            ${{row.trend ? `<div style="font-size:9px;color:var(--accent);margin-top:2px;">📈 ${{row.trend}}</div>` : ''}}
            ${{row.angle ? `<div style="font-size:9px;color:var(--text3);margin-top:1px;">${{row.angle}}</div>` : ''}}
          </td>
          <td style="text-align:center;">
            ${{over ? '<span class="badge badge-red" style="font-size:9px;">' + (IS_AR ? 'تجاوز' : 'Over quota') + '</span>'
                    : row.enabled ? '<span class="badge badge-green" style="font-size:9px;">✓</span>'
                    : '<span class="badge badge-gray" style="font-size:9px;">' + (IS_AR ? 'متخطى' : 'Skip') + '</span>'}}
          </td>
        `;
        tbody.appendChild(tr);
      }});
    }}

    function setEnabled(i, val) {{ rows[i].enabled = val; recount(); renderTable(); }}
    function toggleAll(val) {{
      rows.forEach(r => r.enabled = val);
      document.getElementById('th-check').checked   = val;
      document.getElementById('select-all').checked = val;
      recount(); renderTable();
    }}
    function setProvider(p) {{
      document.getElementById('llm_provider').value = p;
      document.getElementById('prov-google').classList.toggle('active', p==='google');
      document.getElementById('prov-openrouter').classList.toggle('active', p==='openrouter');
    }}
    function submitSchedule() {{
      const selected = rows.filter(r => r.enabled && r.within_quota);
      if (selected.length === 0) {{
        alert(IS_AR ? 'اختر منشورًا واحدًا على الأقل ضمن الحصة.' :
              'No posts selected within quota.');
        return;
      }}
      document.getElementById('rows_json').value           = JSON.stringify(selected);
      document.getElementById('llm_api_key_hidden').value  = document.getElementById('key-llm').value;
      document.getElementById('image_api_key_hidden').value= document.getElementById('key-img').value;
      const b1 = document.getElementById('schedule-btn');
      const b2 = document.getElementById('schedule-btn-2');
      b1.disabled = b2.disabled = true;
      b1.innerHTML = b2.innerHTML = '<div class="spinner"></div> ' +
        (IS_AR ? 'جاري الجدولة…' : 'Scheduling…');
      document.getElementById('schedule-form').submit();
    }}
    recount(); renderTable();
    </script>"""

    return HTMLResponse(_page(content, user, t["review_schedule"], "strategy"))


@router.post("/strategy/{sid}/schedule-all")
async def strategy_schedule_all(
    request:       Request,
    sid:           str,
    rows_json:     str = Form(...),
    llm_provider:  str = Form("google"),
    llm_api_key:   str = Form(""),
    image_api_key: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    s = get_strategy(sid, user["id"])
    if not s:
        return RedirectResponse("/strategy")

    try:
        rows = safe_json_loads(rows_json, [])
    except Exception:
        return RedirectResponse(f"/strategy/{sid}/review")

    if not rows:
        return RedirectResponse(f"/strategy/{sid}/review")

    saved         = get_user_settings(user["id"])
    saved_llm     = (saved.get("gemini_key", "") if llm_provider == "google"
                     else saved.get("openrouter_key", ""))
    llm_api_key   = llm_api_key   or saved_llm
    image_api_key = image_api_key or saved.get("gemini_key", "")
    llm_model     = saved.get("llm_model", "gemini-2.5-flash")
    image_model   = saved.get("image_model", "gemini-3.1-flash-image-preview")
    video_model   = saved.get("video_model", "google/veo-3.1-i2v")

    brand_profile = {}
    if s.get("brand_id"):
        b = get_brand(s["brand_id"], user["id"])
        if b:
            brand_profile = b.get("profile", {})

    q         = quota_status(user)
    remain    = q["remaining"]
    scheduled = 0
    gids_by_day: dict = {}

    for row in rows:
        if scheduled >= remain:
            break
        topic    = str(row.get("topic", "")).strip()
        ct       = row.get("ct", "static")
        platform = row.get("platform", "Instagram")
        date_str = row.get("date", "")
        time_str = row.get("time", "09:00")
        day_idx  = row.get("idx", 0)

        if not topic or not date_str:
            continue

        try:
            sched_dt     = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            scheduled_at = sched_dt.isoformat(sep=" ")
        except ValueError:
            scheduled_at = f"{date_str} {time_str}:00"

        cfg = {
            "topic":           topic,
            "platforms":       [platform],
            "content_type":    ct,
            "language":        "English",
            "brand_color":     "#4f8ef7",
            "number_idea":     1,
            "niche":           detect_niche(topic),
            "competitor_urls": [],
            "product_features":[],
            "brand_profile":   brand_profile,
            "image_url":       "",
            "aspect_ratio":    "9:16",
            "llm_provider":    llm_provider,
            "llm_model":       llm_model,
            "image_model":     image_model,
            "video_model":     video_model,
            "llm_api_key":     llm_api_key  or "",
            "image_api_key":   image_api_key or "",
            "video_api_key":   saved.get("aiml_key", ""),
            "human_review":    False,
            "strategy_id":     sid,
            "strategy_day":    day_idx,
            "scheduled_at":    scheduled_at,
        }

        gid = create_scheduled_generation(
            user["id"], topic, ct, [platform], "English", scheduled_at, cfg
        )
        gids_by_day[day_idx] = {
            "gid":          gid,
            "status":       "scheduled",
            "scheduled_at": scheduled_at,
        }
        add_calendar_items(user["id"], [{
            "strategy_id":  sid,
            "generation_id":gid,
            "brand_id":     s.get("brand_id", ""),
            "title":        topic[:120],
            "platform":     platform,
            "content_type": ct,
            "publish_date": date_str,
            "publish_time": time_str,
            "status":       "scheduled",
            "idea":         {"topic": topic, "platform": platform, "content_type": ct},
        }])
        scheduled += 1

    _bulk_progress[sid] = {
        "total":  scheduled,
        "done":   0,
        "failed": 0,
        "gids":   gids_by_day,
    }
    logger.info("Scheduled %d generations for strategy %s", scheduled, sid)
    return RedirectResponse(f"/strategy/{sid}/progress", status_code=303)


# ── Progress page ─────────────────────────────────────────────────────────────
@router.get("/strategy/{sid}/progress", response_class=HTMLResponse)
async def strategy_progress_page(request: Request, sid: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    s = get_strategy(sid, user["id"])
    if not s:
        return RedirectResponse("/strategy")

    ui_lang = request.query_params.get("lang", request.cookies.get("ui_lang", "en"))
    is_ar   = _is_arabic(ui_lang)
    t       = _t(ui_lang)

    prog    = _bulk_progress.get(sid, {})
    total   = prog.get("total", 0)
    done    = prog.get("done", 0)
    failed  = prog.get("failed", 0)
    gids    = prog.get("gids", {})
    running = total - done - failed
    pct     = round((done / max(total, 1)) * 100)

    db_scheduled = get_scheduled_generations(user["id"], strategy_id=sid)

    cards = ""
    plan  = s.get("plan", {}) or {}
    posts = plan.get("daily_posts", [])
    for day_idx in sorted(gids.keys()):
        info   = gids[day_idx]
        gid    = info["gid"]
        status = info["status"]
        sb2    = {
            "completed": "badge-green",
            "running":   "badge-amber",
            "failed":    "badge-red",
        }.get(status, "badge-amber")
        post = posts[day_idx] if day_idx < len(posts) else {}
        hook = (post.get("hook", "") or post.get("topic", ""))[:60]
        plat = post.get("platform", "")
        link = (f'<a class="btn btn-ghost btn-sm" href="/result/{gid}" target="_blank">'
                f'{"عرض" if is_ar else "View"} →</a>') if status == "completed" else ""
        dot  = ("completed" if status == "completed" else
                "running"   if status == "running"   else "failed")
        cards += (
            f'<div style="display:flex;align-items:center;gap:12px;padding:10px 0;'
            f'border-bottom:1px solid var(--border);">'
            f'<div class="status-dot {dot}"></div>'
            f'<div style="flex:1;min-width:0;">'
            f'<div style="font-size:12px;font-weight:600;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap;font-family:{t["font"]};">'
            f'{hook or (("اليوم" if is_ar else "Day") + " " + str(day_idx+1))}</div>'
            f'<div style="font-family:var(--mono);font-size:10px;color:var(--text3);">'
            f'{plat} · <span class="badge {sb2}" style="display:inline-flex;font-size:9px;">'
            f'{status}</span></div></div>'
            f'{link}</div>'
        )

    content = f"""
    <div class="topbar" dir="{t["dir"]}">
      <div>
        <div class="topbar-title" style="font-family:{t["font"]};">
          ▶ {"تقدم التوليد" if is_ar else "Generation Progress"} — {s["title"]}
        </div>
        <div class="topbar-sub">
          {done}/{total} {"مكتمل" if is_ar else "complete"} ·
          {failed} {"فشل" if is_ar else "failed"} ·
          {running} {"جارٍ" if is_ar else "running"}
        </div>
      </div>
      <div class="flex gap-3">
        <a class="btn btn-ghost btn-sm" href="/strategy/{sid}">
          {"→ رجوع" if is_ar else "← Back"}
        </a>
        <a class="btn btn-ghost btn-sm" href="/history">
          {"◈ السجل" if is_ar else "◈ History"}
        </a>
      </div>
    </div>

    <div class="content" dir="{t["dir"]}" style="max-width:780px;font-family:{t["font"]};">
      <div class="card mb-4">
        <div class="flex items-center justify-between mb-3">
          <div class="card-title">
            {"تقدم التوليد" if is_ar else "Generation Progress"}
          </div>
          <span style="font-family:var(--mono);font-size:11px;color:var(--text3);">
            {done}/{total} · {pct}%
          </span>
        </div>
        <div class="progress mb-4">
          <div class="progress-bar" id="prog-bar" style="width:{pct}%;"></div>
        </div>
        <div class="flex gap-3 mb-4" style="flex-wrap:wrap;">
          <span class="badge badge-green">✓ {done} {"مكتمل" if is_ar else "done"}</span>
          <span class="badge badge-amber">⟳ {running} {"جارٍ" if is_ar else "running"}</span>
          {f'<span class="badge badge-red">✕ {failed} {"فشل" if is_ar else "failed"}</span>'
           if failed else ""}
        </div>
        <div>{cards if cards else
              f'<div class="empty-state" style="padding:20px;">'
              f'<div class="spinner" style="margin:0 auto;"></div></div>'}
        </div>
      </div>

      {"<div class='alert alert-success'>" +
       ("✓ اكتمل الكل! " if is_ar else "✓ All done! ") +
       "<a class='auth-link' href='/history'>" +
       ("عرض في السجل →" if is_ar else "View in History →") +
       "</a></div>"
       if done == total and total > 0 and not db_scheduled else ""}
    </div>

    <script>
    (function(){{
      let stopped = {"true" if done >= total and total > 0 else "false"};
      function poll(){{
        if(stopped) return;
        fetch(window.location.href, {{cache:"no-store"}})
          .then(r=>r.text())
          .then(html=>{{ document.open(); document.write(html); document.close(); }})
          .catch(()=>setTimeout(poll,3000));
      }}
      if(!stopped) setTimeout(poll,2500);
    }})();
    </script>"""

    return HTMLResponse(
        _page(content, user, "Strategy Progress", "strategy"),
        headers={"Cache-Control": "no-store, no-cache"},
    )


@router.post("/strategy/cancel-generation/{gid}")
async def cancel_generation(request: Request, gid: str):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    cancel_scheduled_generation(gid, user["id"])
    return JSONResponse({"ok": True, "gid": gid})