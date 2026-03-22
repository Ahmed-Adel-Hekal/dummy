from scraping.base_scraper import BaseScraper
CHANNELS = {"UC_x5XG1OV2P6uZZ5FSM9Ttw":"Google Developers","UCnUYZLuoy1rq1aVMwx4aTzw":"Google Cloud","UCVHFbw7woebKtX3KiNIOJiA":"Fireship"}
class YouTubeScraper(BaseScraper):
    SOURCE_NAME = "youtube"
    def fetch(self, limit=50):
        posts = []; seen = set(); per_ch = max(limit//len(CHANNELS),5)
        for channel_id in CHANNELS:
            for entry in self.get_feed(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")[:per_ch]:
                title = getattr(entry,"title","").strip(); url = getattr(entry,"link","")
                if title and title not in seen:
                    seen.add(title)
                    post = self.make_post(title,url,"youtube",1)
                    if post: posts.append(post)
            if len(posts)>=limit: break
        return posts[:limit]
def scrape_youtube(limit=50): return YouTubeScraper().fetch(limit)
