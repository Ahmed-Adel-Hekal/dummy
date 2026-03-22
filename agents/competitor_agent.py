"""agents/competitor_agent.py"""
from __future__ import annotations
import json, re
from core.gemini_client import Agent
from media.video_generator import parse_llm_json
from core.logger import get_logger
logger = get_logger("CompetitorAgent")

REPORT_SCHEMA = '''{"brand_overview":"...","top_hooks":["..."],"content_patterns":["..."],"winning_angles":["..."],"gap_opportunities":["..."],"tone_summary":"...","keyword_cloud":["..."],"cta_patterns":["..."],"content_ideas":[{"hook":"...","angle":"...","platform":"Instagram"}],"audience_signals":"..."}'''

class CompetitorAgent(Agent):
    def analyze(self, profiles_or_posts):
        if not profiles_or_posts: return self._empty_report("no data provided")
        if profiles_or_posts and isinstance(profiles_or_posts[0],dict):
            first = profiles_or_posts[0]
            if "brand_name" in first or "headings" in first:
                return self._analyze_profiles(profiles_or_posts)
            else:
                return self._analyze_posts(profiles_or_posts)
        return self._empty_report("unrecognised data format")

    def _analyze_profiles(self, profiles):
        prompt_blocks = []
        for i,p in enumerate(profiles,1):
            block = self._profile_to_block(p)
            prompt_blocks.append(f"=== COMPETITOR {i} ===\n{block}")
        prompt = f"You are an expert social media competitive intelligence analyst.\nAnalyze these competitor profiles:\n{chr(10).join(prompt_blocks)}\nReturn ONLY valid JSON matching this schema (no markdown):\n{REPORT_SCHEMA}".strip()
        raw = self.ask(prompt, max_tokens=3000)
        if not raw: return self._empty_report("LLM returned empty response")
        try:
            data = parse_llm_json(raw)
            if not isinstance(data,dict): return self._empty_report("LLM returned non-dict JSON")
            data["report"]   = self._build_markdown_report(profiles,data)
            data["profiles"] = profiles
            return data
        except Exception as exc:
            return self._empty_report(str(exc))

    def _analyze_posts(self, posts):
        lines = []
        for i,post in enumerate(posts[:30],1):
            caption = post.get("caption") or post.get("title") or ""
            hook = post.get("hook",""); platform = post.get("platform","unknown")
            lines.append(f"{i}. [{platform}] hook={hook[:80]} | caption={caption[:200]}")
        prompt = f"Analyze these competitor posts and extract patterns:\n{chr(10).join(lines)}\nReturn ONLY valid JSON:\n{REPORT_SCHEMA}".strip()
        raw = self.ask(prompt, max_tokens=2048)
        if not raw: return self._empty_report("LLM returned empty response")
        try:
            data = parse_llm_json(raw)
            return data if isinstance(data,dict) else self._empty_report("bad JSON")
        except Exception as exc:
            return self._empty_report(str(exc))

    def _profile_to_block(self, p):
        lines = [f"Brand: {p.get('brand_name','')}", f"Platform: {p.get('platform','')}",
                 f"Desc: {p.get('description','')[:300]}", f"Keywords: {', '.join(p.get('keywords',[])[:12])}",
                 "Headings:"] + [f"  - {h}" for h in p.get("headings",[])[:8]] +                 ["Recent posts:"] + [f"  - {r.get('title','')}" for r in p.get("recent_posts",[])[:10]] +                 [f"CTAs: {', '.join(p.get('cta_phrases',[])[:8])}"]
        return "\n".join(lines)

    def _build_markdown_report(self, profiles, data):
        sections = ["# Competitor Intelligence Report\n","## Brands Analysed"]
        for p in profiles:
            sections.append(f"- **{p.get('brand_name',p.get('url',''))}** ({p.get('platform','')}) — {p.get('description','')[:120]}")
        sections += ["\n## Top Hooks"] + [f"- {h}" for h in data.get("top_hooks",[])]
        sections += ["\n## Gap Opportunities"] + [f"- {g}" for g in data.get("gap_opportunities",[])]
        return "\n".join(sections)

    @staticmethod
    def _empty_report(reason=""):
        return {"profiles":[],"report":f"No competitor data available. {reason}".strip(),"top_hooks":[],"content_patterns":[],"winning_angles":[],"gap_opportunities":[],"tone_summary":"","keyword_cloud":[],"cta_patterns":[],"content_ideas":[],"audience_signals":"","error":reason}
