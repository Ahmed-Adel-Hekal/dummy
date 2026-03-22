"""scraping/base_scraper.py"""
from __future__ import annotations
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from core.logger import get_logger
DEFAULT_TIMEOUT = 6   # Reduced from 10s — avoids blocking the pipeline
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Circuit breaker: track consecutive failures per source
_failures: dict = {}
_FAILURE_LIMIT = 3   # disable source after 3 consecutive timeouts

def _make_session(retries=2, backoff=0.3):
    """Tighter retry config — fail fast rather than stall the pipeline."""
    session = requests.Session()
    retry = Retry(
        total=retries, backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return session
class BaseScraper:
    SOURCE_NAME = "base"
    def __init__(self):
        self.session = _make_session()
        self.logger = get_logger(self.__class__.__name__)
    def _is_circuit_open(self):
        return _failures.get(self.SOURCE_NAME, 0) >= _FAILURE_LIMIT

    def _record_success(self):
        _failures.pop(self.SOURCE_NAME, None)

    def _record_failure(self):
        _failures[self.SOURCE_NAME] = _failures.get(self.SOURCE_NAME, 0) + 1

    def get_json(self, url, headers=None, params=None, timeout=DEFAULT_TIMEOUT):
        if self._is_circuit_open():
            self.logger.debug("[%s] Circuit open — skipping", self.SOURCE_NAME)
            return None
        try:
            r = self.session.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            self._record_success()
            return r.json()
        except Exception as e:
            self._record_failure()
            self.logger.debug("[%s] JSON fetch failed: %s", self.SOURCE_NAME, e)
            return None
    def get_html(self, url, timeout=DEFAULT_TIMEOUT):
        if self._is_circuit_open():
            return ""
        try:
            r = self.session.get(url, timeout=timeout)
            r.raise_for_status()
            self._record_success()
            return r.text
        except Exception as e:
            self._record_failure()
            self.logger.debug("[%s] HTML fetch failed: %s", self.SOURCE_NAME, e)
            return ""
    def get_feed(self, url):
        try:
            import feedparser; return feedparser.parse(url).entries
        except Exception as e:
            self.logger.error("[%s] Feed fetch failed: %s",self.SOURCE_NAME,e); return []
    @staticmethod
    def make_post(title, url, source, score=1):
        title = (title or "").strip()
        if not title: return None
        return {"title":title,"url":url or "","source":source,"score":score}
