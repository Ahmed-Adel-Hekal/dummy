from core.logger import get_logger
logger = get_logger("TrendForecaster")
class TrendForecaster:
    def forecast(self, posts):
        if not posts: return posts
        for post in posts:
            state = post.get("trend_state","stable"); score = post.get("trend_score",0)
            if state=="exploding": post["forecast"]="viral"
            elif state=="growing" and score>12: post["forecast"]="future_trend"
            else: post["forecast"]="stable"
        return posts
