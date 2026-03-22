from scraping.base_scraper import BaseScraper
INSTAGRAM_RSS_FEEDS = [("https://later.com/blog/feed/","instagram_later"),("https://blog.hootsuite.com/feed/","instagram_hootsuite"),("https://sproutsocial.com/insights/feed/","instagram_sprout"),("https://www.socialmediaexaminer.com/feed/","instagram_sme")]
INSTAGRAM_KEYWORDS = ["instagram","reel","story","hashtag","influencer","social media","engagement","content creator","visual","feed"]
KNOWN_HASHTAGS = ["AItools","TechStartup","MachineLearning","DevLife","StartupLife","ProductDesign","UXDesign","DataScience","WebDevelopment","Python"]
class InstagramScraper(BaseScraper):
    SOURCE_NAME = "instagram"
    def fetch(self, limit=50):
        posts = []
        for tag in KNOWN_HASHTAGS:
            post = self.make_post(f"#{tag} trending on Instagram",f"https://www.instagram.com/explore/tags/{tag}/","instagram_hashtag",1)
            if post: posts.append(post)
        seen = set()
        for url,source_name in INSTAGRAM_RSS_FEEDS:
            for entry in self.get_feed(url)[:15]:
                title = getattr(entry,"title","").strip(); link = getattr(entry,"link","")
                summary = getattr(entry,"summary","").lower()
                if not title or title in seen: continue
                combined = f"{title} {summary}".lower()
                if not any(kw in combined for kw in INSTAGRAM_KEYWORDS): continue
                seen.add(title)
                post = self.make_post(title,link,source_name,1)
                if post: posts.append(post)
        return posts[:limit]
def scrape_instagram(limit=50): return InstagramScraper().fetch(limit)
