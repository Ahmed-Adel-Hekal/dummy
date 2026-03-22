import re
from scraping.base_scraper import BaseScraper
class ProductHuntScraper(BaseScraper):
    SOURCE_NAME = "producthunt"
    def fetch(self, limit=50):
        posts = []
        for entry in self.get_feed("https://www.producthunt.com/feed")[:limit]:
            title = getattr(entry,"title","").strip(); url = getattr(entry,"link","")
            summary = getattr(entry,"summary",""); score = 1
            if "upvote" in summary.lower():
                m = re.search(r"(\d+)\s*upvote",summary,re.I)
                if m: score = int(m.group(1))
            post = self.make_post(title,url,"producthunt",score)
            if post: posts.append(post)
        return posts
def scrape_producthunt(limit=50): return ProductHuntScraper().fetch(limit)
