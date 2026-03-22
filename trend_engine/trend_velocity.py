from collections import Counter
from core.logger import get_logger
logger = get_logger("TrendVelocity")
def calculate_velocity(posts):
    if not posts: return posts
    counts = Counter(p.get("cluster",0) for p in posts)
    for post in posts: post["trend_velocity"] = int(counts[post.get("cluster",0)])
    return posts
