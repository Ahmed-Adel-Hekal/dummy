"""core/orchestrator.py v3"""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.logger import get_logger
logger = get_logger("Orchestrator")

def _format_trend_summary(trend_insight):
    if not trend_insight or not isinstance(trend_insight,dict): return ""
    top = trend_insight.get("top_trends",[])[:6]
    kws = trend_insight.get("keywords",[])[:10]
    cs  = trend_insight.get("confidence_summary",{})
    lines = ["=== TREND INTELLIGENCE ==="]
    if top:
        lines.append("Top trending topics right now:")
        for t in top:
            lines.append(f"  * [{t.get('trend_strength','').upper()}] {t.get('topic','')} — {t.get('marketing_angle','')} (forecast: {t.get('forecast','')})")
    if kws: lines.append(f"Hot keywords: {', '.join(kws)}")
    if cs: lines.append(f"Confidence: avg={cs.get('average_score',0)} high={cs.get('high_confidence_count',0)}")
    lines.append("=== MAKE CONTENT TIMELY & RELEVANT WITH THESE TRENDS ===")
    return "\n".join(lines)

def _format_brand_voice(brand_profile):
    if not brand_profile or not isinstance(brand_profile,dict): return ""
    bp = brand_profile
    lines = ["=== BRAND VOICE PROFILE ==="]
    if bp.get("brand_name"): lines.append(f"Brand: {bp['brand_name']}")
    if bp.get("tagline"): lines.append(f"Tagline: {bp['tagline']}")
    if bp.get("industry"): lines.append(f"Industry: {bp['industry']}")
    if bp.get("target_audience"): lines.append(f"Target audience: {bp['target_audience']}")
    if bp.get("voice_desc"): lines.append(f"Voice & tone: {bp['voice_desc']}")
    if bp.get("emoji_style"):
        style_map = {"none":"never use emojis","minimal":"use 1-2 emojis max","moderate":"use emojis expressively","heavy":"heavy emoji usage (Gen Z)"}
        lines.append(f"Emoji style: {style_map.get(bp['emoji_style'],bp['emoji_style'])}")
    if bp.get("cta_style"): lines.append(f"CTA style: {bp['cta_style']}")
    if bp.get("signature_words"): lines.append(f"Always use: {bp['signature_words']}")
    if bp.get("banned_words"): lines.append(f"NEVER use these words: {bp['banned_words']}")
    if bp.get("visual_style"): lines.append(f"Visual style: {bp['visual_style']}")
    if bp.get("usps"):
        lines.append("Key USPs:")
        for usp in bp["usps"].splitlines():
            if usp.strip(): lines.append(f"  * {usp.strip()}")
    if bp.get("sample_post"): lines.append(f"Writing style reference (match this voice):\n  {bp['sample_post'][:400]}")
    lines.append("=== WRITE EVERY IDEA IN THIS BRAND VOICE. THIS IS NON-NEGOTIABLE. ===")
    return "\n".join(lines)

def _format_product_features(features):
    if not features: return ""
    lines = ["=== PRODUCT / SERVICE FEATURES TO HIGHLIGHT ==="]
    for f in features[:15]: lines.append(f"  * {f}")
    lines.append("=== WEAVE THESE FEATURES INTO HOOKS, COPY, AND IMAGE DESCRIPTIONS ===")
    return "\n".join(lines)

class Orchestrator:
    def run(self, topic, platforms, content_type, language, brand_color,
            brand_img=None, number_idea=3, competitor_urls=None, product_features=None,
            brand_profile=None, niche="tech", output_dir="output_posts",
            image_url="", aspect_ratio="9:16", llm_provider="google",
            llm_model="gemini-2.5-flash", image_model="gemini-3.1-flash-image-preview",
            video_model="google/veo-3.1-i2v", llm_api_key=None,
            image_api_key=None, video_api_key=None, human_review=False):
        total_start = time.perf_counter()
        comp_insight = {}; trend_insight = {}
        def _comp():
            return self._run_competitor_step(competitor_urls=competitor_urls, platforms=platforms,
                llm_provider=llm_provider, llm_model=llm_model, llm_api_key=llm_api_key)
        def _trend():
            return self._run_trend_step(platforms=platforms, topic=topic, niche=niche,
                llm_provider=llm_provider, llm_model=llm_model, llm_api_key=llm_api_key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_comp = pool.submit(_comp); fut_trend = pool.submit(_trend)
            for fut in as_completed([fut_comp, fut_trend]):
                if fut is fut_comp:
                    try: comp_insight = fut.result()
                    except Exception as e: logger.error("Step 1 failed: %s", e)
                else:
                    try: trend_insight = fut.result()
                    except Exception as e: logger.error("Step 2 failed: %s", e)
        trend_summary  = _format_trend_summary(trend_insight)
        brand_block    = _format_brand_voice(brand_profile or {})
        features_block = _format_product_features(product_features or [])
        result = self._run_content_step(
            topic=topic, platforms=platforms, content_type=content_type,
            language=language, brand_color=brand_color, brand_img=brand_img,
            number_idea=number_idea, comp_insight=comp_insight,
            trend_insight=trend_summary, brand_block=brand_block,
            features_block=features_block, output_dir=output_dir,
            image_url=image_url, aspect_ratio=aspect_ratio,
            llm_provider=llm_provider, llm_model=llm_model,
            image_model=image_model, video_model=video_model,
            llm_api_key=llm_api_key, image_api_key=image_api_key,
            video_api_key=video_api_key, human_review=human_review)
        if isinstance(result,dict):
            result["competitor_insight"] = comp_insight
            result["trend_insight"]      = trend_insight
        logger.info("Pipeline complete in %.2fs", time.perf_counter()-total_start)
        return result

    def _run_competitor_step(self, competitor_urls, platforms, llm_provider, llm_model, llm_api_key):
        from agents.competitor_agent import CompetitorAgent
        from scraping.competitor_scraper import CompetitorScraper
        from core.data_loader import DataLoader
        from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
        scraper = CompetitorScraper()
        agent   = CompetitorAgent(provider=llm_provider, model=llm_model, api_key=llm_api_key)
        if competitor_urls:
            # Scrape URLs in parallel with a 20s hard timeout per URL
            profiles = []
            with ThreadPoolExecutor(max_workers=min(len(competitor_urls), 4)) as pool:
                futures = {pool.submit(scraper.scrape, url): url for url in competitor_urls}
                for fut in as_completed(futures, timeout=25):
                    try:
                        p = fut.result(timeout=20)
                        profiles.append(p.to_dict())
                    except FutureTimeout:
                        logger.warning("Competitor URL timed out: %s", futures[fut])
                    except Exception as exc:
                        logger.warning("Competitor scrape failed %s: %s", futures[fut], exc)
            return agent.analyze(profiles) if profiles else agent._empty_report("all URLs timed out")
        else:
            plat = (platforms[0] if platforms else None)
            if plat: plat = plat.replace("/X","").replace("/x","")
            return agent.analyze(DataLoader().load_competitor_posts(platform=plat))

    def _run_trend_step(self, platforms, topic, niche, llm_provider, llm_model, llm_api_key):
        from agents.trend_agent import TrendAgent
        return TrendAgent().analyze(platforms=platforms, topic=topic, niche=niche,
            llm_provider=llm_provider, llm_model=llm_model, llm_api_key=llm_api_key)

    def _run_content_step(self, topic, platforms, content_type, language, brand_color,
            brand_img, number_idea, comp_insight, trend_insight, brand_block,
            features_block, output_dir, image_url, aspect_ratio,
            llm_provider, llm_model, image_model, video_model,
            llm_api_key, image_api_key, video_api_key, human_review):
        from agents.content_agent import run_content_pipeline
        return run_content_pipeline(
            topic=topic, platforms=platforms, content_type=content_type,
            language=language, brand_color=brand_color, brand_img=brand_img,
            number_idea=number_idea, comp_insight=comp_insight,
            trend_insight=trend_insight, brand_block=brand_block,
            features_block=features_block, output_dir=output_dir,
            image_url=image_url, aspect_ratio=aspect_ratio,
            llm_provider=llm_provider, llm_model=llm_model,
            image_model=image_model, video_model=video_model,
            llm_api_key=llm_api_key, image_api_key=image_api_key,
            video_api_key=video_api_key, human_review=human_review)
