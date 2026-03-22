from scraping.base_scraper import BaseScraper
class DevToScraper(BaseScraper):
    SOURCE_NAME = "devto"
    def fetch(self, limit=50):
        data = self.get_json("https://dev.to/api/articles",params={"per_page":limit,"top":7})
        if not data: return []
        posts = []
        for item in data[:limit]:
            post = self.make_post(item.get("title",""),item.get("url",""),"devto",item.get("positive_reactions_count",0)+item.get("comments_count",0))
            if post: posts.append(post)
        return posts
def scrape_devto(limit=50): return DevToScraper().fetch(limit)
