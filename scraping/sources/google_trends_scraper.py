from scraping.base_scraper import BaseScraper
REGIONS = ["united_states","egypt","united_kingdom"]
class GoogleTrendsScraper(BaseScraper):
    SOURCE_NAME = "google_trends"
    def fetch(self, limit=50):
        try:
            from pytrends.request import TrendReq
        except ImportError:
            return []
        posts = []; seen = set()
        try:
            pt = TrendReq(hl="en-US",tz=360,timeout=(10,25))
            for region in REGIONS:
                try:
                    df = pt.trending_searches(pn=region)
                    for kw in df[0].tolist():
                        kw = str(kw).strip()
                        if kw and kw not in seen:
                            seen.add(kw)
                            post = self.make_post(kw,"https://trends.google.com/","google_trends",1)
                            if post: posts.append(post)
                except Exception: pass
        except Exception: pass
        return posts[:limit]
def scrape_google_trends(limit=50): return GoogleTrendsScraper().fetch(limit)
