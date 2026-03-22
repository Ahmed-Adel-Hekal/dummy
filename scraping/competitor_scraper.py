"""scraping/competitor_scraper.py — Deep competitor intelligence scraper."""
from __future__ import annotations
import re
from urllib.parse import urlparse, urljoin
from dataclasses import dataclass, field, asdict
import requests

try:
    import feedparser
    _HAS_FP = True
except ImportError:
    _HAS_FP = False

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False

from core.logger import get_logger
logger = get_logger("CompetitorScraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 12


@dataclass
class CompetitorProfile:
    url:            str
    platform:       str
    brand_name:     str  = ""
    tagline:        str  = ""
    description:    str  = ""
    keywords:       list = field(default_factory=list)
    headings:       list = field(default_factory=list)
    content_topics: list = field(default_factory=list)
    recent_posts:   list = field(default_factory=list)
    social_signals: dict = field(default_factory=dict)
    tone_signals:   list = field(default_factory=list)
    cta_phrases:    list = field(default_factory=list)
    raw_text:       str  = ""
    scrape_error:   str  = ""

    def to_dict(self):
        return asdict(self)

    def to_prompt_block(self):
        kw = ", ".join(self.keywords[:12])
        lines_out = [
            f"COMPETITOR: {self.brand_name or self.url}",
            f"Platform: {self.platform}",
            f"Description: {self.description[:300]}",
            f"Keywords: {kw}",
            "",
            "TOP HEADINGS:",
        ] + [f"  - {h}" for h in self.headings[:8]] + [
            "",
            "RECENT CONTENT:",
        ] + [f"  - {p['title']}" for p in self.recent_posts[:10]]
        return "\n".join(l for l in lines_out if l is not None)


class CompetitorScraper:
    """
    Scrapes competitor websites, YouTube channels and social profiles
    to extract brand signals, hooks, CTAs and recent content.
    """

    # ── Tone vocabulary ───────────────────────────────────────────────────────
    TONE_WORDS = [
        # urgency / scarcity
        "now", "today", "limited", "exclusive", "hurry", "last chance",
        "don't miss", "ends soon", "only",
        # authority / social proof
        "proven", "trusted", "expert", "guaranteed", "award", "best",
        "top", "leading", "official", "certified",
        # empathy / community
        "you", "your", "we", "our", "together", "join", "community",
        "feel", "love", "care", "support",
        # aspiration / transformation
        "transform", "achieve", "success", "results", "powerful", "change",
        "better", "improve", "grow", "boost",
        # curiosity / intrigue
        "secret", "discover", "reveal", "inside", "hidden", "truth",
        "what if", "how to", "why",
    ]

    # ── CTA pattern regex ─────────────────────────────────────────────────────
    CTA_PATTERNS = re.compile(
        r'\b('
        r'buy now|shop now|get started|sign up|subscribe|learn more|'
        r'try free|start free|get free|claim now|download now|'
        r'book now|schedule now|contact us|get a quote|request demo|'
        r'watch now|listen now|read more|see more|view all|'
        r'join now|join free|register now|apply now|order now|'
        r'add to cart|checkout|get access|unlock|discover more'
        r')\b',
        re.IGNORECASE,
    )

    # ── Public entry point ────────────────────────────────────────────────────
    def scrape(self, url: str) -> CompetitorProfile:
        profile = CompetitorProfile(url=url, platform=self._detect_platform(url))
        if not url:
            profile.scrape_error = "empty url"
            return profile
        try:
            if profile.platform == "youtube":
                self._scrape_youtube(url, profile)
            elif profile.platform in ("instagram", "tiktok", "twitter"):
                self._scrape_social_heuristic(url, profile)
            else:
                self._scrape_website(url, profile)
            if profile.platform not in ("instagram", "tiktok", "twitter", "youtube"):
                self._discover_rss(url, profile)
        except Exception as exc:
            profile.scrape_error = str(exc)
            logger.warning("CompetitorScraper error for %s: %s", url, exc)
        return profile

    # ── Website scraper ───────────────────────────────────────────────────────
    def _scrape_website(self, url: str, profile: CompetitorProfile):
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()

        if not _HAS_BS4:
            profile.raw_text = resp.text[:4000]
            return

        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "aside",
                          "form", "iframe", "noscript"]):
            tag.decompose()

        # Brand name
        title_tag = soup.find("title")
        profile.brand_name = (title_tag.get_text(strip=True) if title_tag else "")[:120]

        def _meta(name: str) -> str:
            t = soup.find("meta", attrs={"name": name}) or \
                soup.find("meta", attrs={"property": name})
            return (t.get("content") or "").strip() if t else ""

        profile.description = (_meta("description") or _meta("og:description"))[:500]
        profile.tagline     = _meta("og:title")[:120] or profile.brand_name

        kw_raw = _meta("keywords")
        if kw_raw:
            profile.keywords = [k.strip() for k in kw_raw.split(",") if k.strip()][:20]

        # Headings
        seen_h = set()
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = tag.get_text(" ", strip=True)
            if text and len(text) > 4 and text not in seen_h:
                seen_h.add(text)
                profile.headings.append(text)
        profile.headings = profile.headings[:20]

        # Body text
        body_parts = [
            p.get_text(" ", strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(" ", strip=True)) >= 30
        ]
        profile.raw_text       = " ".join(body_parts)[:5000]
        profile.content_topics = body_parts[:15]

        # Tone signals — check which tone words appear in the body
        lower_body = profile.raw_text.lower()
        profile.tone_signals = [w for w in self.TONE_WORDS if w in lower_body]

        # CTA phrases
        cta_found: set = set()
        for m in self.CTA_PATTERNS.finditer(resp.text):
            phrase = m.group(0).lower()
            if phrase not in cta_found:
                cta_found.add(phrase)
                profile.cta_phrases.append(phrase)
        profile.cta_phrases = list(cta_found)[:10]

        # Internal blog/article links as recent posts
        seen_p: set = set()
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        for a in soup.find_all("a", href=True):
            href  = a["href"]
            atext = a.get_text(strip=True)
            if not atext or len(atext) < 10:
                continue
            if href.startswith("/") or href.startswith(base):
                full = urljoin(base, href)
                if any(seg in full for seg in ("/blog/", "/article", "/post", "/news/")):
                    if full not in seen_p:
                        seen_p.add(full)
                        profile.recent_posts.append({
                            "title":  atext[:120],
                            "url":    full,
                            "source": "internal_link",
                        })
            if len(profile.recent_posts) >= 15:
                break

    # ── RSS discovery ─────────────────────────────────────────────────────────
    def _discover_rss(self, url: str, profile: CompetitorProfile):
        if not _HAS_FP:
            return
        candidates = [
            urljoin(url, "/feed"),
            urljoin(url, "/rss"),
            urljoin(url, "/feed.xml"),
            urljoin(url, "/rss.xml"),
            urljoin(url, "/blog/feed"),
            urljoin(url, "/blog/rss"),
        ]
        for feed_url in candidates:
            try:
                feed = feedparser.parse(feed_url)
                if not feed.entries:
                    continue
                for entry in feed.entries[:15]:
                    title = getattr(entry, "title", "").strip()
                    link  = getattr(entry, "link",  "")
                    if title and len(title) > 5:
                        profile.recent_posts.append({
                            "title":  title[:150],
                            "url":    link,
                            "source": "rss",
                        })
                break
            except Exception:
                continue

    # ── YouTube scraper ───────────────────────────────────────────────────────
    def _scrape_youtube(self, url: str, profile: CompetitorProfile):
        if not _HAS_FP:
            return
        channel_id = ""
        path = urlparse(url).path or ""

        if "/channel/" in path:
            channel_id = path.split("/channel/", 1)[1].split("/", 1)[0].strip()
        elif "/@" in path:
            handle = path.split("/@", 1)[1].split("/", 1)[0].strip()
            profile.brand_name = f"@{handle}"
            try:
                html = requests.get(
                    f"https://www.youtube.com/@{handle}",
                    headers=HEADERS, timeout=TIMEOUT,
                ).text
                m = re.search(r'"channelId":"(UC[^"]+)"', html)
                if m:
                    channel_id = m.group(1)
            except Exception:
                pass
        elif "/user/" in path:
            channel_id = path.split("/user/", 1)[1].split("/", 1)[0].strip()

        if not channel_id:
            profile.scrape_error = "Could not resolve YouTube channel ID"
            return

        feed = feedparser.parse(
            f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        )
        profile.brand_name = profile.brand_name or (
            feed.feed.get("title", "") if feed.feed else ""
        )
        for entry in feed.entries[:15]:
            title = getattr(entry, "title", "").strip()
            link  = getattr(entry, "link",  "")
            if title:
                profile.recent_posts.append({
                    "title":    title,
                    "url":      link,
                    "source":   "youtube_rss",
                    "platform": "youtube",
                })

    # ── Social heuristic scraper (Instagram / TikTok / Twitter) ──────────────
    def _scrape_social_heuristic(self, url: str, profile: CompetitorProfile):
        path   = urlparse(url).path.strip("/")
        parts  = [p for p in path.split("/") if p]
        handle = parts[0] if parts else ""
        profile.brand_name = f"@{handle}" if handle else url
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if _HAS_BS4 and resp.ok:
                soup = BeautifulSoup(resp.text, "html.parser")

                def _og(prop: str) -> str:
                    t = soup.find("meta", property=prop) or \
                        soup.find("meta", attrs={"name": prop})
                    return (t.get("content") or "").strip() if t else ""

                profile.description = _og("og:description")[:400]
                profile.tagline     = _og("og:title")[:120]
        except Exception:
            pass
        profile.social_signals = {"handle": handle, "platform": profile.platform}

    # ── Utility: scrape and return flat post list ─────────────────────────────
    def scrape_as_posts(self, url: str) -> list[dict]:
        profile = self.scrape(url)
        posts   = []
        for p in profile.recent_posts:
            posts.append({
                "caption":  p.get("title", ""),
                "url":      p.get("url", ""),
                "source":   p.get("source", "website"),
                "platform": profile.platform,
                "hook":     p.get("title", "")[:80],
            })
        for h in profile.headings[:5]:
            posts.append({
                "caption":  h,
                "url":      url,
                "source":   "heading",
                "platform": profile.platform,
                "hook":     h[:80],
            })
        return posts

    # ── Static helper ─────────────────────────────────────────────────────────
    @staticmethod
    def _detect_platform(url: str) -> str:
        low = (url or "").lower()
        if "youtube.com" in low or "youtu.be" in low:
            return "youtube"
        if "instagram.com" in low:
            return "instagram"
        if "tiktok.com" in low:
            return "tiktok"
        if "twitter.com" in low or "x.com" in low:
            return "twitter"
        if "linkedin.com" in low:
            return "linkedin"
        return "website"