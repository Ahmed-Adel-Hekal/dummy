"""agents/trend_agent.py — Trend intelligence agent"""
from __future__ import annotations

import hashlib
import json
import os
import time
import pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus

import requests as req_lib

from core.data_loader import DataLoader
from core.gemini_client import Agent
from core.logger import get_logger
from media.video_generator import parse_llm_json
from trend_engine.deduplicator import deduplicate_posts
from trend_engine.keyword_extractor import extract_keywords
from trend_engine.novelty_detector import detect_novelty
from trend_engine.topic_clusterer import cluster_topics
from trend_engine.trend_classifier import classify_trends
from trend_engine.trend_forecaster import TrendForecaster
from trend_engine.trend_ranker import TrendRanker
from trend_engine.trend_scorer import score_trends
from trend_engine.trend_time_analyzer import TrendTimeAnalyzer
from trend_engine.trend_velocity import calculate_velocity

logger = get_logger("TrendAgent")

# Absolute project root — one level above the agents/ directory.
# Using this instead of a relative Path("data/...") means the cache file
# is always written and read from the same location regardless of where
# uvicorn / the process manager launches from.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

SOURCE_QUALITY = {
    "deep_search":       0.55,
    "reddit":            0.75,
    "reddit_search":     0.78,
    "hackernews":        0.80,
    "hackernews_search": 0.82,
    "github":            0.80,
    "github_search":     0.85,
    "linkedin":          0.70,
    "twitter":           0.68,
    "google_news":       0.74,
    "google_news_search":0.78,
    "youtube":           0.70,
    "instagram":         0.65,
    "tiktok":            0.65,
}


class TrendAgent:
    def __init__(self):
        self.loader        = DataLoader()
        self.forecaster    = TrendForecaster()
        self.ranker        = TrendRanker()
        self.time_analyzer = TrendTimeAnalyzer()
        self.cache_ttl     = int(os.getenv("TREND_CACHE_TTL_HOURS", "24")) * 3600

        # Absolute path — always resolves to <project_root>/data/processed/trend_cache.json
        self.cache_path = _PROJECT_ROOT / "data" / "processed" / "trend_cache.json"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        self._scrapers = self._load_scrapers()

    # ── Scraper registry ──────────────────────────────────────────────────────
    def _load_scrapers(self) -> dict:
        scrapers = {}
        scraper_map = {
            "reddit":       ("scraping.sources.reddit_scraper",       "scrape_reddit"),
            "hackernews":   ("scraping.sources.hackernews_scraper",    "scrape_hackernews"),
            "devto":        ("scraping.sources.devto_scraper",         "scrape_devto"),
            "medium":       ("scraping.sources.medium_scraper",        "scrape_medium"),
            "github":       ("scraping.sources.github_scraper",        "scrape_github"),
            "stackoverflow":("scraping.sources.stackoverflow_scraper", "scrape_stackoverflow"),
            "youtube":      ("scraping.sources.youtube_scraper",       "scrape_youtube"),
            "producthunt":  ("scraping.sources.producthunt_scraper",   "scrape_producthunt"),
            "google_news":  ("scraping.sources.google_news_scraper",   "scrape_google_news"),
            "google_trends":("scraping.sources.google_trends_scraper", "scrape_google_trends"),
            "twitter":      ("scraping.sources.twitter_scraper",       "scrape_twitter"),
            "linkedin":     ("scraping.sources.linkedin_scraper",      "scrape_linkedin"),
            "tiktok":       ("scraping.sources.tiktok_scraper",        "scrape_tiktok"),
            "instagram":    ("scraping.sources.instagram_scraper",     "scrape_instagram"),
        }
        for key, (module_path, fn_name) in scraper_map.items():
            try:
                import importlib
                mod = importlib.import_module(module_path)
                scrapers[key] = getattr(mod, fn_name)
            except Exception:
                pass
        return scrapers

    # ── Cache helpers ─────────────────────────────────────────────────────────
    def _cache_key(self, topic, platforms, niche, markets, limit, provider, model) -> str:
        raw = json.dumps({
            "topic":     topic or "",
            "platforms": sorted(platforms or []),
            "niche":     niche or "",
            "markets":   sorted(markets or []),
            "limit":     int(limit or 0),
            "provider":  provider or "google",
            "model":     model or "gemini-2.5-flash",
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _read_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_cache(self, store: dict):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _get_cached(self, key: str):
        store = self._read_cache()
        item  = store.get(key)
        if not isinstance(item, dict):
            return None
        if int(time.time()) - int(item.get("ts", 0)) > self.cache_ttl:
            return None
        return item.get("result")

    def _set_cached(self, key: str, result: dict):
        store       = self._read_cache()
        store[key]  = {"ts": int(time.time()), "result": result}
        self._write_cache(store)

    # ── Public entry point ────────────────────────────────────────────────────
    def analyze(
        self,
        platforms,
        topic          = "",
        niche          = "tech",
        markets        = None,
        limit_per_source = 100,
        force_refresh  = False,
        llm_provider   = "google",
        llm_model      = "gemini-2.5-flash",
        llm_api_key    = None,
    ) -> dict:
        try:
            cache_key = self._cache_key(
                topic, platforms, niche, markets or [],
                limit_per_source, llm_provider, llm_model,
            )

            if not force_refresh:
                cached = self._get_cached(cache_key)
                if cached is not None:
                    cached["cache"] = {"used": True, "ttl_hours": 24}
                    return cached

            llm   = Agent(provider=llm_provider, model=llm_model, api_key=llm_api_key)
            posts = self._run_scrapers(platforms, limit_per_source, topic)
            posts.extend(self._topic_probes(topic, limit=min(50, limit_per_source // 2)))
            posts.extend(self._deep_search(topic, niche, markets or [], llm))

            if not posts:
                fb     = self.loader.load_trends(platform=None, niche=niche, limit=30)
                result = self._format_fallback(fb, topic)
                result["cache"] = {"used": False, "ttl_hours": 24}
                self._set_cached(cache_key, result)
                return result

            ranked = self._run_pipeline(posts)
            result = self._format_result(ranked, topic)
            result["cache"] = {"used": False, "ttl_hours": 24}
            self._set_cached(cache_key, result)
            return result

        except Exception as exc:
            logger.error("TrendAgent.analyze failed: %s", exc)
            fb = self.loader.load_trends(platform=None, niche=niche, limit=30)
            return self._format_fallback(fb, topic)

    # ── Platform normalisation ────────────────────────────────────────────────
    def _normalize_platform(self, p: str) -> str:
        return {
            "twitter/x": "twitter",
            "x":         "twitter",
            "insta":     "instagram",
        }.get(p.strip().lower(), p.strip().lower())

    # ── Scraper runner ────────────────────────────────────────────────────────
    def _run_scrapers(self, platforms, limit: int, topic: str = "") -> list:
        selected  = [self._normalize_platform(p) for p in (platforms or [])]
        selected  = [p for p in selected if p in self._scrapers]

        # Always include high-signal universal sources
        universal = ["hackernews", "reddit", "google_news", "github"]
        if selected:
            for u in universal:
                if u in self._scrapers and u not in selected:
                    selected.append(u)

        funcs = [self._scrapers[p] for p in selected] if selected else list(self._scrapers.values())
        posts = []

        if not funcs:
            return posts

        # 18 s global cap — never let slow scrapers stall the pipeline
        with ThreadPoolExecutor(max_workers=min(14, len(funcs))) as exe:
            futures = {exe.submit(fn, limit): fn.__name__ for fn in funcs}
            for future in as_completed(futures, timeout=18):
                try:
                    rows = future.result(timeout=8) or []
                    posts.extend(rows)
                    logger.debug("Scraper %s → %d posts", futures[future], len(rows))
                except Exception:
                    pass

        return self._rank_by_topic(posts, topic)

    # ── Topic relevance helpers ───────────────────────────────────────────────
    def _topic_keywords(self, topic: str) -> list[str]:
        words = [
            w.strip().lower()
            for w in (topic or "").replace("/", " ").replace("-", " ").split()
        ]
        return [w for w in words if len(w) >= 3][:8]

    def _topic_score(self, post: dict, keywords: list) -> int:
        if not keywords:
            return 1
        text = f"{post.get('title', '')} {post.get('source', '')}".lower()
        return sum(1 for kw in keywords if kw in text)

    def _rank_by_topic(self, posts: list, topic: str) -> list:
        kws = self._topic_keywords(topic)
        if not kws:
            return posts
        return sorted(
            posts,
            key=lambda p: (self._topic_score(p, kws), p.get("score", 0)),
            reverse=True,
        )

    # ── Topic probes (targeted searches per source) ───────────────────────────
    def _topic_probes(self, topic: str, limit: int = 20) -> list:
        if not (topic or "").strip():
            return []
        posts = []
        for fn in (
            self._probe_reddit,
            self._probe_hackernews,
            self._probe_google_news,
            self._probe_github,
        ):
            try:
                posts.extend(fn(topic, limit))
            except Exception:
                pass
        return posts

    def _probe_reddit(self, topic: str, limit: int) -> list:
        resp = req_lib.get(
            "https://www.reddit.com/search.json",
            params={"q": topic, "sort": "top", "t": "week", "limit": limit},
            headers={"User-Agent": "AI-Content-Factory/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        return [
            {
                "title":  c["data"].get("title", ""),
                "source": f"reddit_search/{topic}",
                "url":    c["data"].get("url", ""),
                "score":  int(c["data"].get("score", 1) or 1),
            }
            for c in resp.json().get("data", {}).get("children", [])
            if c["data"].get("title", "").strip()
        ]

    def _probe_hackernews(self, topic: str, limit: int) -> list:
        resp = req_lib.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": topic, "tags": "story", "hitsPerPage": limit},
            timeout=10,
        )
        resp.raise_for_status()
        return [
            {
                "title":  h.get("title", ""),
                "source": f"hackernews_search/{topic}",
                "url":    h.get("url", "") or "",
                "score":  int(h.get("points", 1) or 1),
            }
            for h in resp.json().get("hits", [])
            if h.get("title", "").strip()
        ]

    def _probe_google_news(self, topic: str, limit: int) -> list:
        import xml.etree.ElementTree as et
        resp = req_lib.get(
            f"https://news.google.com/rss/search"
            f"?q={quote_plus(topic)}&hl=en-US&gl=US&ceid=US:en",
            timeout=10,
        )
        resp.raise_for_status()
        root = et.fromstring(resp.content)
        return [
            {
                "title":  (item.findtext("title") or "").strip(),
                "source": f"google_news_search/{topic}",
                "url":    (item.findtext("link") or "").strip(),
                "score":  1,
            }
            for item in root.findall(".//item")[:limit]
            if (item.findtext("title") or "").strip()
        ]

    def _probe_github(self, topic: str, limit: int) -> list:
        resp = req_lib.get(
            "https://api.github.com/search/repositories",
            params={"q": topic, "sort": "stars", "order": "desc",
                    "per_page": min(30, limit)},
            timeout=10,
        )
        resp.raise_for_status()
        return [
            {
                "title":  f"{item.get('full_name', '')} - {item.get('description', '')[:80]}".strip(" -"),
                "source": f"github_search/{topic}",
                "url":    item.get("html_url", ""),
                "score":  int(item.get("stargazers_count", 1) or 1),
            }
            for item in resp.json().get("items", [])
            if item.get("full_name", "").strip()
        ]

    # ── LLM deep-search (trend ideation) ─────────────────────────────────────
    def _deep_search(self, topic: str, niche: str, markets: list, llm: Agent) -> list:
        market_str = ", ".join(markets) if markets else "global"
        _schema    = '{"trends":["trend 1","trend 2"]}'
        prompt = (
            f"You are a senior trend analyst specializing in {niche} content "
            f"for {market_str}. Identify 8 emerging social-media content trends for: "
            f'"{topic or "general"}". Focus on unique angles, format innovations, '
            f"audience pain points, and viral potential. "
            f"Return ONLY this JSON (no markdown): {_schema}"
        )
        raw = llm.ask(prompt, max_tokens=1024)
        if not raw:
            return []
        try:
            data = parse_llm_json(raw)
            return [
                {"title": str(t).strip(), "source": "deep_search", "url": "", "score": 1}
                for t in data.get("trends", [])[:8]
                if str(t).strip()
            ]
        except Exception:
            return []

    # ── 9-stage trend pipeline ────────────────────────────────────────────────
    def _run_pipeline(self, posts: list) -> dict:
        rows = deduplicate_posts(posts)
        if not rows:
            return {"exploding": [], "growing": [], "future": [], "stable": []}
        n_clusters = max(2, min(10, len(rows) // 8 or 2))
        rows = cluster_topics(rows, n_clusters=n_clusters)
        rows = calculate_velocity(rows)
        rows = detect_novelty(rows)
        rows = self.time_analyzer.enrich(rows)
        rows = score_trends(rows)
        rows = classify_trends(rows)
        rows = self.forecaster.forecast(rows)
        ranked = self.ranker.rank(rows)
        ranked["keywords"] = extract_keywords(rows, top_k=12)
        return ranked

    # ── Confidence helper ─────────────────────────────────────────────────────
    def _confidence_level(self, score: float) -> str:
        if score >= 75:
            return "high"
        if score >= 55:
            return "medium"
        return "low"

    # ── Result formatters ─────────────────────────────────────────────────────
    def _format_result(self, ranked: dict, topic: str = "") -> dict:
        all_rows = (
            ranked.get("exploding", []) +
            ranked.get("growing",   []) +
            ranked.get("future",    []) +
            ranked.get("stable",    [])
        )
        kws       = self._topic_keywords(topic)
        max_score = max(
            (float(r.get("trend_score", 0) or 0) for r in all_rows),
            default=1.0,
        ) or 1.0

        top = []
        for row in all_rows[:12]:
            state    = row.get("trend_state", "stable")
            strength = (
                "high"   if state == "exploding" else
                "medium" if state == "growing"   else
                "low"
            )
            src_key = str(row.get("source", "social")).split("/", 1)[0]
            t_norm  = min(1.0, max(0.0, float(row.get("trend_score", 0) or 0) / max_score))
            t_match = min(1.0, self._topic_score(row, kws) / max(1, len(kws))) if kws else 0.5
            s_norm  = SOURCE_QUALITY.get(src_key, 0.6)
            f_norm  = (
                1.0 if row.get("forecast") == "viral"        else
                0.8 if row.get("forecast") == "future_trend" else
                0.6
            )
            conf = round((0.45 * t_norm + 0.25 * t_match + 0.15 * s_norm + 0.15 * f_norm) * 100, 1)

            top.append({
                "topic":          row.get("title", ""),
                "trend_strength": strength,
                "content_format": (
                    "short" if src_key in {"youtube", "tiktok", "instagram"} else "post"
                ),
                "marketing_angle": f"Leverage {row.get('source', 'social')} momentum",
                "hook_style":      "question" if len(row.get("title", "")) % 2 else "shocking statistic",
                "forecast":        row.get("forecast", "stable"),
                "confidence_score":conf,
                "confidence_level":self._confidence_level(conf),
            })

        scores = [float(t.get("confidence_score", 0) or 0) for t in top]
        return {
            "top_trends": top,
            "keywords":   ranked.get("keywords", []),
            "confidence_summary": {
                "average_score":         round(float(sum(scores) / len(scores)), 1) if scores else 0.0,
                "high_confidence_count": int(len([s for s in scores if s >= 75])),
                "medium_confidence_count": int(len([s for s in scores if 55 <= s < 75])),
                "low_confidence_count":  int(len([s for s in scores if s < 55])),
                "cacheable_for_hours":   24,
            },
        }

    def _format_fallback(self, trends: list, topic: str = "") -> dict:
        kws = self._topic_keywords(topic)
        if kws:
            filtered = [
                t for t in trends
                if any(
                    kw in f"{t.get('topic', '')} {t.get('marketing_angle', '')}".lower()
                    for kw in kws
                )
            ]
            if filtered:
                trends = filtered

        top = []
        for t in trends:
            score = float(t.get("score", 0) or 0)
            conf  = round(min(100.0, max(35.0, score)), 1)
            top.append({
                "topic":           t.get("topic", ""),
                "trend_strength":  t.get("trend_strength", "medium"),
                "content_format":  t.get("content_format", "post"),
                "marketing_angle": t.get("marketing_angle", ""),
                "hook_style":      t.get("hook_style", "question"),
                "forecast":        "future_trend" if score >= 80 else "stable",
                "confidence_score":conf,
                "confidence_level":self._confidence_level(conf),
            })

        scores = [float(t.get("confidence_score", 0) or 0) for t in top[:12]]
        return {
            "top_trends": top[:12],
            "keywords": [
                t.get("topic", "").split()[0]
                for t in trends[:8]
                if t.get("topic")
            ],
            "confidence_summary": {
                "average_score":           round(sum(scores) / len(scores), 1) if scores else 0.0,
                "high_confidence_count":   len([s for s in scores if s >= 75]),
                "medium_confidence_count": len([s for s in scores if 55 <= s < 75]),
                "low_confidence_count":    len([s for s in scores if s < 55]),
                "cacheable_for_hours":     24,
            },
        }