from scraping.base_scraper import BaseScraper
class StackOverflowScraper(BaseScraper):
    SOURCE_NAME = "stackoverflow"
    def fetch(self, limit=50):
        data = self.get_json("https://api.stackexchange.com/2.3/questions",
            params={"order":"desc","sort":"hot","site":"stackoverflow","pagesize":limit,"filter":"default"})
        if not data: return []
        posts = []
        for item in data.get("items",[])[:limit]:
            post = self.make_post(item.get("title",""),item.get("link",""),"stackoverflow",
                item.get("score",0)+item.get("answer_count",0))
            if post: posts.append(post)
        return posts
def scrape_stackoverflow(limit=50): return StackOverflowScraper().fetch(limit)
