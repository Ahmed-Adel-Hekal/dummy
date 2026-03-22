import asyncio
from scraping.base_scraper import BaseScraper
BASE_URL = "https://hacker-news.firebaseio.com/v0"
CONCURRENT_LIMIT = 20
try:
    import aiohttp
    _TIMEOUT = aiohttp.ClientTimeout(total=10)
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False
class HackerNewsScraper(BaseScraper):
    SOURCE_NAME = "hackernews"
    def fetch(self, limit=100):
        if not _HAS_AIOHTTP: return []
        try: return asyncio.run(self._scrape(limit))
        except Exception as e: self.logger.error("HackerNews failed: %s",e); return []
    async def _scrape(self, limit):
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            ids = await self._fetch_json(session,f"{BASE_URL}/topstories.json")
            if not ids: return []
            sem = asyncio.Semaphore(CONCURRENT_LIMIT)
            items = await asyncio.gather(*[self._fetch_item(session,sem,i) for i in ids[:limit]])
            posts = []
            for item in items:
                if not item: continue
                post = self.make_post(item.get("title",""),item.get("url",""),"hackernews",item.get("score",0))
                if post: posts.append(post)
            return posts
    async def _fetch_json(self, session, url):
        try:
            async with session.get(url) as r:
                if r.status==200: return await r.json()
        except Exception: pass
        return None
    async def _fetch_item(self, session, sem, item_id):
        async with sem: return await self._fetch_json(session,f"{BASE_URL}/item/{item_id}.json")
def scrape_hackernews(limit=100): return HackerNewsScraper().fetch(limit)
