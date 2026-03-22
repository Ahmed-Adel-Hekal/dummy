import re
from collections import Counter
from core.logger import get_logger
logger = get_logger("KeywordExtractor")
STOPWORDS = {"the","and","with","from","this","that","have","will","your","about","into","using","how","what","when","where","which","while","their","there","these","for","not","are","was","but","its","can","all"}
def extract_keywords(posts, top_k=10):
    tokens = []
    for post in posts:
        words = re.findall(r"\b[a-z]{3,}\b", post.get("title","").lower())
        tokens.extend(w for w in words if w not in STOPWORDS)
    result = [k for k,_ in Counter(tokens).most_common(top_k)]
    logger.info("Extracted keywords: %s", result)
    return result
