import os, pickle
CACHE_FILE = "data/processed/embedding_cache.pkl"
def load_cache():
    if not os.path.exists(CACHE_FILE): return {}
    try:
        with open(CACHE_FILE,"rb") as f: return pickle.load(f)
    except Exception: return {}
def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE,"wb") as f: pickle.dump(cache, f)
