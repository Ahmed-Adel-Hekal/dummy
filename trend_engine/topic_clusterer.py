from core.logger import get_logger
from trend_engine.embedding_cache import load_cache, save_cache
logger = get_logger("TopicClusterer")
try:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import KMeans
    _model = SentenceTransformer("all-MiniLM-L6-v2")
    _HAS_ML = True
except Exception:
    _model = None; _HAS_ML = False
_cache = load_cache()
def _embed(text):
    if text in _cache: return _cache[text]
    vec = _model.encode(text)
    _cache[text] = vec; save_cache(_cache)
    return vec
def cluster_topics(posts, n_clusters=10):
    if not posts: return posts
    if not _HAS_ML:
        n = max(1,min(n_clusters,len(posts)))
        for i,post in enumerate(posts): post["cluster"] = i%n
        return posts
    titles = [p["title"] for p in posts]
    embeddings = [_embed(t) for t in titles]
    labels = KMeans(n_clusters=n_clusters, n_init="auto").fit_predict(embeddings)
    for i,post in enumerate(posts): post["cluster"] = int(labels[i])
    return posts
