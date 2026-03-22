from scraping.base_scraper import BaseScraper
CREATIVE_CENTER_URL = ("https://ads.tiktok.com/creative_radar_api/v1/popular_trend/hashtag/list?period=7&page=1&limit=50&country_code=US")
CREATIVE_CENTER_HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36","Referer":"https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en","Accept":"application/json"}
TIKTOK_RSS_FEEDS = [("https://www.socialmediatoday.com/rss.xml","tiktok_smt"),("https://www.theverge.com/rss/index.xml","tiktok_verge"),("https://techcrunch.com/feed/","tiktok_tc")]
TIKTOK_KEYWORDS = ["tiktok","viral","trending","short video","reel","creator","fyp"]
class TikTokScraper(BaseScraper):
    SOURCE_NAME = "tiktok"
    def _fetch_creative_center(self):
        data = self.get_json(CREATIVE_CENTER_URL,headers=CREATIVE_CENTER_HEADERS)
        if not data: return []
        posts = []
        hashtag_list = data.get("data",{}).get("list",[]) or data.get("data",[]) or []
        for item in hashtag_list:
            tag = (item.get("hashtag_name") or item.get("name") or "").strip()
            if not tag: continue
            views = item.get("video_views") or item.get("publish_cnt") or 0
            try: score = max(1,int(str(views).replace(",",""))//1_000_000)
            except Exception: score = 1
            post = self.make_post(f"#{tag} trending on TikTok",f"https://www.tiktok.com/tag/{tag}","tiktok_hashtag",score)
            if post: posts.append(post)
        return posts
    def _fetch_rss(self):
        posts = []; seen = set()
        for url,source_name in TIKTOK_RSS_FEEDS:
            for entry in self.get_feed(url)[:20]:
                title = getattr(entry,"title","").strip(); link = getattr(entry,"link","")
                summary = getattr(entry,"summary","").lower()
                if not title or title in seen: continue
                combined = f"{title} {summary}".lower()
                if not any(kw in combined for kw in TIKTOK_KEYWORDS): continue
                seen.add(title)
                post = self.make_post(title,link,source_name,1)
                if post: posts.append(post)
        return posts
    def fetch(self, limit=100):
        posts = self._fetch_creative_center()
        posts.extend(self._fetch_rss())
        return posts[:limit]
def scrape_tiktok(limit=100): return TikTokScraper().fetch(limit)
