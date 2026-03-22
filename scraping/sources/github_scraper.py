import re
from urllib.parse import urlparse
from scraping.base_scraper import BaseScraper
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
TIMEFRAMES = ["daily","weekly"]
class GitHubTrendingScraper(BaseScraper):
    SOURCE_NAME = "github_trending"
    def fetch(self, limit=50):
        if BeautifulSoup is None: return []
        posts = []; seen = set()
        for tf in TIMEFRAMES:
            html = self.get_html(f"https://github.com/trending?since={tf}&spoken_language_code=en")
            if not html: continue
            soup = BeautifulSoup(html,"html.parser")
            for repo in soup.select("article.Box-row"):
                h2 = repo.select_one("h2 a")
                if not h2: continue
                name = " ".join(h2.text.split())
                href = h2.get("href","")
                desc_el = repo.select_one("p.col-9")
                desc = desc_el.text.strip() if desc_el else ""
                title = f"{name} — {desc}" if desc else name
                url = f"https://github.com{href}"
                stars = 0
                stars_el = repo.select_one("span.d-inline-block.float-sm-right")
                if stars_el:
                    try: stars = int(stars_el.text.strip().replace(",",""))
                    except ValueError: pass
                if title not in seen:
                    seen.add(title)
                    post = self.make_post(title,url,"github_trending",stars)
                    if post: posts.append(post)
            if len(posts)>=limit: break
        return posts[:limit]
def scrape_github_trending(limit=50): return GitHubTrendingScraper().fetch(limit)
def scrape_github(limit=50): return scrape_github_trending(limit)
