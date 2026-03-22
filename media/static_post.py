"""media/static_post.py — Gemini image generation for static posts."""
import os, json
from dataclasses import dataclass
from typing import Optional

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None; types = None

@dataclass
class PostResult:
    idea_index: int; status: str
    image_path: Optional[str] = None
    json_path:  Optional[str] = None
    error:      Optional[str] = None

class ImagePromptBuilder:
    _COLOR_KEYWORDS = ("brand color","brand colours","brand accent","hex","color scheme","colour scheme","use the color","use the colour","incorporate color","incorporate colour")
    @classmethod
    def _should_use_brand_colors(cls, visual_dir):
        return any(kw in visual_dir.lower() for kw in cls._COLOR_KEYWORDS)
    @classmethod
    def build(cls, idea, brand_colors):
        image_desc = str(idea.get("image_description","") or "")
        visual_dir = str(idea.get("visual_direction","")  or "")
        color_instruction = ""
        if brand_colors and cls._should_use_brand_colors(visual_dir):
            color_str = ", ".join(brand_colors)
            color_instruction = f"Subtly incorporate brand accent color(s) {color_str} as a background element or prop color if it fits naturally. "
        no_text = "CRITICAL: Do NOT include any text, words, letters, numbers, hashtags, captions, watermarks, logos, or overlays anywhere in the image. "
        prompt = f"{image_desc} Visual style: {visual_dir} {color_instruction}High-quality social media post image, vertical 4:5 format, professional photography, cinematic lighting, sharp focus, modern aesthetic. {no_text}"
        return prompt.strip()

class StaticPostGenerator:
    IMAGE_MODEL = "gemini-3.1-flash-image-preview"
    def __init__(self, gemini_api_key, brand_colors=None, output_dir="output_posts", aspect_ratio="9:16", model=None):
        self.brand_colors = brand_colors or ["#EE3322"]
        self.output_dir   = output_dir
        self.aspect_ratio = aspect_ratio
        self.image_model  = model or self.IMAGE_MODEL
        if genai and gemini_api_key:
            self.client = genai.Client(api_key=gemini_api_key)
        else:
            self.client = None
        os.makedirs(output_dir, exist_ok=True)

    @staticmethod
    def _safe_str(idea, key, default=""):
        value = idea.get(key, default)
        if value is None: return default
        if isinstance(value,list): return " ".join(str(v) for v in value)
        return str(value)

    def _generate_image(self, prompt, filename):
        if not self.client or not types: return None
        try:
            response = self.client.models.generate_content(
                model=self.image_model, contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio=self.aspect_ratio)))
            for part in response.parts:
                if part.inline_data:
                    image_path = os.path.join(self.output_dir, filename)
                    try:
                        from PIL import Image; import io
                        img = Image.open(io.BytesIO(part.inline_data.data))
                        img.save(image_path)
                    except ImportError:
                        with open(image_path,"wb") as f: f.write(part.inline_data.data)
                    return image_path
            return None
        except Exception as e:
            print(f"    [!] Generation error: {e}"); return None

    def _save_json(self, idea, idea_idx, image_path):
        post_data = {"idea_index":idea_idx+1,"hook":self._safe_str(idea,"hook"),"post_copy":self._safe_str(idea,"post_copy"),"hashtags":idea.get("hashtags",[]),"visual_direction":self._safe_str(idea,"visual_direction"),"image_description":self._safe_str(idea,"image_description"),"image_path":image_path}
        json_path = os.path.join(self.output_dir, f"idea_{idea_idx+1}.json")
        with open(json_path,"w",encoding="utf-8") as f: json.dump(post_data,f,ensure_ascii=False,indent=2)
        return json_path

    def _generate_one(self, args):
        """Worker for parallel image generation."""
        idea_idx, idea = args
        builder = ImagePromptBuilder()
        prompt     = builder.build(idea, self.brand_colors)
        image_path = self._generate_image(prompt, f"idea_{idea_idx+1}.png")
        json_path  = self._save_json(idea, idea_idx, image_path)
        return PostResult(
            idea_index=idea_idx,
            status="completed" if image_path else "partial",
            image_path=image_path,
            json_path=json_path,
            error=None if image_path else "Image generation failed",
        )

    def generate_all(self, content_json):
        """Generate all images IN PARALLEL — 3x faster than serial."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ideas = content_json.get("ideas", [])
        if not ideas:
            return []
        n = len(ideas)
        print(f"\n🖼️  Generating {n} image(s) in parallel...")
        results_by_idx = {}
        # Cap at 5 workers — Gemini API has per-key concurrency limits
        with ThreadPoolExecutor(max_workers=min(n, 5)) as pool:
            futures = {pool.submit(self._generate_one, (i, idea)): i
                       for i, idea in enumerate(ideas)}
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    results_by_idx[res.idea_index] = res
                    print(f"  ✅ Idea {res.idea_index+1} done")
                except Exception as exc:
                    idx = futures[fut]
                    print(f"  ❌ Idea {idx+1} failed: {exc}")
                    results_by_idx[idx] = PostResult(
                        idea_index=idx, status="partial",
                        error=str(exc),
                    )
        # Return in original order
        return [results_by_idx[i] for i in range(n) if i in results_by_idx]
