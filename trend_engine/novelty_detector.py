from collections import Counter
from core.logger import get_logger
logger = get_logger("NoveltyDetector")
def detect_novelty(posts):
    if not posts: return posts
    counts = Counter(p.get("cluster",0) for p in posts)
    max_count = max(counts.values()) if counts else 1
    for post in posts:
        cluster_size = counts.get(post.get("cluster",0),1)
        post["novelty_score"] = float(round(1-(cluster_size/max_count),3))
    return posts
