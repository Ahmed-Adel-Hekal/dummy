from collections import defaultdict
from core.logger import get_logger
logger = get_logger("TrendTimeAnalyzer")
EXPLODING_THRESHOLD = 15; GROWING_THRESHOLD = 8
class TrendTimeAnalyzer:
    def enrich(self, posts):
        if not posts: return posts
        if "cluster" not in posts[0]: return posts
        if "trend_velocity" not in posts[0]: return posts
        clusters = defaultdict(list)
        for post in posts: clusters[post["cluster"]].append(post)
        cluster_states = {}
        for cid,items in clusters.items():
            avg_v = sum(p.get("trend_velocity",0) for p in items)/len(items)
            avg_n = sum(p.get("novelty_score",0) for p in items)/len(items)
            c_score = round(avg_v*0.6+avg_n*0.4,3)
            state = "exploding" if c_score>EXPLODING_THRESHOLD else "growing" if c_score>GROWING_THRESHOLD else "stable"
            cluster_states[cid] = {"cluster_score":c_score,"cluster_state":state,"cluster_size":len(items)}
        for post in posts:
            c = cluster_states.get(post["cluster"],{})
            post["cluster_score"]=float(c.get("cluster_score",0))
            post["cluster_state"]=c.get("cluster_state","stable")
            post["cluster_size"]=c.get("cluster_size",1)
        return posts
