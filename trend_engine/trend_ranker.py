from core.logger import get_logger
logger = get_logger("TrendRanker")
class TrendRanker:
    def rank(self, posts):
        if not posts: return {"exploding":[],"growing":[],"future":[],"stable":[]}
        buckets = {"exploding":[],"growing":[],"future":[],"stable":[]}
        for post in posts:
            post["rank_score"] = post.get("trend_score",0)
            state = post.get("trend_state","stable"); forecast = post.get("forecast","stable")
            if state=="exploding": buckets["exploding"].append(post)
            elif state=="growing": buckets["growing"].append(post)
            elif forecast=="future_trend": buckets["future"].append(post)
            else: buckets["stable"].append(post)
        for key in buckets:
            buckets[key] = sorted(buckets[key], key=lambda x:x["rank_score"], reverse=True)[:10]
        return buckets
