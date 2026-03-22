from scraping.base_scraper import BaseScraper
TAGS = ["artificial-intelligence","machine-learning","programming","technology","startup"]
class MediumScraper(BaseScraper):
    SOURCE_NAME = "medium"
    def fetch(self, limit=50):
        posts = []; seen = set(); per_tag = max(limit//len(TAGS),5)
        for tag in TAGS:
            for entry in self.get_feed(f"https://medium.com/feed/tag/{tag}")[:per_tag]:
                title = getattr(entry,"title","").strip(); url = getattr(entry,"link","")
                if title and title not in seen:
                    seen.add(title)
                    post = self.make_post(title,url,"medium",1)
                    if post: posts.append(post)
            if len(posts)>=limit: break
        return posts[:limit]
def scrape_medium(limit=50): return MediumScraper().fetch(limit)
