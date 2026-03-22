from scraping.base_scraper import BaseScraper
LINKEDIN_RSS_SOURCES = [
    ("https://feeds.feedburner.com/TechCrunch","linkedin_tech"),
    ("https://www.infoq.com/feed","linkedin_infoq"),
    ("https://feeds.harvardbusiness.org/harvardbusiness/","linkedin_hbr"),
    ("https://news.ycombinator.com/rss","linkedin_hn"),
]
class LinkedInScraper(BaseScraper):
    SOURCE_NAME = "linkedin"
    def fetch(self, limit=50):
        posts = []; seen = set()
        for url,source_name in LINKEDIN_RSS_SOURCES:
            if len(posts)>=limit: break
            for entry in self.get_feed(url)[:10]:
                title = getattr(entry,"title","").strip(); link = getattr(entry,"link","")
                if not title or title in seen or len(title)<15: continue
                seen.add(title)
                post = self.make_post(title,link,source_name,1)
                if post: posts.append(post)
        return posts[:limit]
def scrape_linkedin(limit=50): return LinkedInScraper().fetch(limit)
