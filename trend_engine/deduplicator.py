from core.logger import get_logger
logger = get_logger("Deduplicator")
def deduplicate_posts(posts):
    seen, unique = set(), []
    for post in posts:
        title = (post.get("title") or "").strip()
        if not title or title in seen: continue
        seen.add(title); unique.append(post)
    logger.info("Deduplicated: %d → %d posts", len(posts), len(unique))
    return unique
