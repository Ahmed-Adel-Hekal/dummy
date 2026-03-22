from core.logger import get_logger
logger = get_logger("TrendClassifier")
EXPLODING_THRESHOLD = 20; GROWING_THRESHOLD = 10
def classify_trends(posts):
    if not posts: return posts
    for post in posts:
        score = post.get("trend_score",0)
        post["trend_state"] = "exploding" if score>EXPLODING_THRESHOLD else "growing" if score>GROWING_THRESHOLD else "stable"
    return posts
