from scraping.base_scraper import BaseScraper
NITTER_INSTANCES = ["nitter.net","nitter.privacydev.net","nitter.poast.org"]
HASHTAGS = ["AItools","MachineLearning","LLM","DevOps","WebDev","Startup","OpenSource","SoftwareEngineering"]
class TwitterScraper(BaseScraper):
    SOURCE_NAME = "twitter"
    def _fetch_hashtag(self, hashtag, instance):
        posts = []
        entries = self.get_feed(f"https://{instance}/search/rss?q=%23{hashtag}&f=tweets")
        for entry in entries[:15]:
            title = getattr(entry,"title","").strip(); link = getattr(entry,"link","")
            if title.startswith("RT "): title = title[3:].strip()
            if len(title)<10: continue
            post = self.make_post(title,link,f"twitter/#{hashtag}",1)
            if post: posts.append(post)
        return posts
    def fetch(self, limit=100):
        posts = []; seen = set()
        for hashtag in HASHTAGS:
            for instance in NITTER_INSTANCES:
                try:
                    new_posts = self._fetch_hashtag(hashtag,instance)
                    if new_posts:
                        for p in new_posts:
                            if p["title"] not in seen:
                                seen.add(p["title"]); posts.append(p)
                        break
                except Exception: pass
            if len(posts)>=limit: break
        return posts[:limit]
def scrape_twitter(limit=100): return TwitterScraper().fetch(limit)
