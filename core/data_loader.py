"""core/data_loader.py — loads static JSON data files for fallback."""
from __future__ import annotations
import json
from pathlib import Path

class DataLoader:
    def load_competitor_posts(self, path="data/competitor_posts.json",
                               platform=None, limit=50) -> list[dict]:
        file_path = Path(path)
        if not file_path.exists():
            return []
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
        except Exception:
            return []
        if platform:
            data = [p for p in data if str(p.get("platform","")).lower()==platform.lower()]
        return data[:max(limit,0)]

    def load_trends(self, path="data/trends.json", platform=None,
                    niche=None, limit=30) -> list[dict]:
        file_path = Path(path)
        if not file_path.exists():
            return []
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
        except Exception:
            return []
        if platform:
            data = [t for t in data if str(t.get("platform","")).lower()==platform.lower()]
        if niche:
            data = [t for t in data if str(t.get("niche","")).lower()==niche.lower()]
        return data[:max(limit,0)]
