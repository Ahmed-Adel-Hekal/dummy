from scraping.base_scraper import BaseScraper
SUBREDDITS = ["programming","technology","MachineLearning","artificial","webdev","devops","startups"]
class RedditScraper(BaseScraper):
    SOURCE_NAME = "reddit"
    def fetch(self, limit=100):
        posts = []; per_sub = max(limit//len(SUBREDDITS),10)
        for sub in SUBREDDITS:
            data = self.get_json(f"https://www.reddit.com/r/{sub}/top.json",
                headers={"User-Agent":"ai-trend-engine/1.0"},params={"limit":per_sub,"t":"week"})
            if not data: continue
            for item in data.get("data",{}).get("children",[]):
                p = item.get("data",{})
                post = self.make_post(p.get("title",""),p.get("url",""),f"reddit/r/{sub}",p.get("score",0))
                if post: posts.append(post)
        return posts[:limit]
def scrape_reddit(limit=100): return RedditScraper().fetch(limit)
