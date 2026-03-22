from core.logger import get_logger
logger = get_logger("TrendScorer")
def score_trends(posts):
    if not posts: return posts
    for post in posts:
        velocity = post.get("trend_velocity",0); novelty = post.get("novelty_score",0)
        post["trend_score"] = float(round(velocity*0.7+novelty*0.3,3))
    return posts
