"""media/video_generator.py — Veo 3.1 video generation via AIML API"""
import os, json, re, time, subprocess, requests
from typing import Optional
from dataclasses import dataclass


@dataclass
class VideoResult:
    idea_index:    int
    scene_index:   int
    generation_id: str
    status:        str
    video_url:     Optional[str] = None
    error:         Optional[str] = None


def parse_llm_json(raw):
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if match:
        raw = match.group(1)
    else:
        start = raw.find("{"); end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
    raw = raw.strip()

    def fix_string_newlines(text):
        result = []; i = 0; in_str = False
        while i < len(text):
            ch = text[i]
            if in_str and ch == "\\" and i + 1 < len(text):
                result.append(ch); result.append(text[i + 1]); i += 2; continue
            if ch == '"':
                in_str = not in_str; result.append(ch)
            elif in_str and ch in ("\n", "\r"):
                result.append(" ")
            else:
                result.append(ch)
            i += 1
        return "".join(result)

    raw = fix_string_newlines(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    try:
        from json_repair import repair_json
        return json.loads(repair_json(cleaned))
    except Exception:
        pass
    raise ValueError(f"Could not parse JSON from LLM output.\nFirst 300 chars:\n{raw[:300]}")


class VeoPromptBuilder:
    """
    Builds per-scene prompts for Veo 3.1.

    Strategy:
      - If ``image_url`` is provided (product/brand image):
          • The image is sent as the i2v reference every scene automatically
            via the API payload — no need to describe the character in text.
          • We still inject a short "match the reference image" instruction
            so Veo knows to treat the uploaded frame as the visual anchor.
          • Character-anchor text block is OMITTED to avoid conflicts.
      - If ``image_url`` is absent:
          • Full character-anchor text is injected into EVERY scene's prompt
            so Veo has the best possible chance of keeping the person consistent.
          • Style-lock from scene 1 is still applied to scenes 2+.
    """

    # ── Character anchor (text-only fallback) ─────────────────────────────────
    @staticmethod
    def _build_character_text(character: dict, anchor: str = "") -> str:
        """
        Returns a CHARACTER ANCHOR block for use when no image_url is available.
        If ``anchor`` is already built (from scene 1), it is returned directly.
        """
        if anchor:
            return anchor
        if not character:
            return ""
        parts = []
        for key in ("gender", "age", "skin", "hair", "eye_color",
                    "facial_details", "physical_details", "outfit"):
            val = character.get(key) or character.get(key.replace("_", " "))
            if val:
                parts.append(str(val))
        if not parts:
            return ""
        description = ", ".join(parts)
        expr = character.get("facial_expression", "")
        return (
            f"CHARACTER ANCHOR — this exact person appears in EVERY scene: {description}. "
            f"The character's face, hair, skin tone, build, and outfit are IDENTICAL across all scenes. "
            f"{'Current expression: ' + expr + '. ' if expr else ''}"
            f"This is the same continuous person throughout the entire video."
        )

    # ── Image reference hint (when image_url IS provided) ────────────────────
    @staticmethod
    def _build_image_ref_hint(is_first_scene: bool) -> str:
        """
        Short instruction injected when an image_url is supplied to the API.
        Tells Veo to treat the reference frame as the visual anchor.
        """
        if is_first_scene:
            return (
                "Use the provided reference image as the exact visual anchor for this video. "
                "Match its subject, appearance, color palette, and style precisely in every scene."
            )
        return (
            "Maintain perfect visual consistency with the reference image provided. "
            "The subject, environment style, and color palette must remain identical to scene 1."
        )

    # ── Lighting / cinematography block ───────────────────────────────────────
    @staticmethod
    def _build_lighting(lighting: dict) -> str:
        if not lighting:
            return ""
        parts = []
        for k, label in [
            ("camera_angle",    "camera angle"),
            ("camera_type",     "camera"),
            ("lighting_mode",   "lighting"),
            ("lighting_position", "light position"),
            ("camera_movement", "movement"),
        ]:
            val = lighting.get(k) or lighting.get(k.replace("_", " "))
            if val:
                parts.append(f"{label}: {val}")
        return "Cinematography — " + ", ".join(parts) + "." if parts else ""

    # ── Voiceover block ───────────────────────────────────────────────────────
    @staticmethod
    def _build_voiceover_style(vo_props: dict, language: str, voiceover_text: str) -> str:
        if not voiceover_text:
            return ""
        gender = (vo_props or {}).get("gender", "Female")
        tone   = (vo_props or {}).get("tone",   "confident")
        return (
            f'Voiceover: {gender or "Female"} voice, {tone or "confident"} tone, '
            f'speaking in {language}: "{voiceover_text}".'
        )

    # ── Main build method ─────────────────────────────────────────────────────
    @classmethod
    def build(
        cls,
        scene:            dict,
        hook:             dict,
        cta:              dict,
        visual_direction: dict,
        brand_colors:     list,
        language:         str,
        image_url:        str  = "",       # ← NEW: passed through from VideoGenerator
        character:        dict = None,
        character_anchor: str  = "",
        style_anchor:     str  = "",
        lighting:         dict = None,
        vo_props:         dict = None,
        is_first_scene:   bool = False,
        is_last_scene:    bool = False,
    ):
        visual_direction = visual_direction or {}
        visuals      = scene.get("visuals", "")
        voiceover    = scene.get("voiceover", "")
        text_overlay = scene.get("text_overlay", "")
        pacing       = visual_direction.get("pacing", "medium")
        transitions  = visual_direction.get("transitions", "cut")
        color_notes  = visual_direction.get("color_usage", "")
        brand_color  = brand_colors[0] if brand_colors else "#FF0000"

        # ── Block 1: Character / image reference ──────────────────────────────
        has_image = bool(image_url and image_url.strip())
        if has_image:
            # Image is sent in the API payload — just tell Veo to use it
            char_block = cls._build_image_ref_hint(is_first_scene)
        else:
            # No image → full text character anchor in EVERY scene
            char_block = cls._build_character_text(character or {}, anchor=character_anchor)

        # ── Block 2: Lighting / cinematography ────────────────────────────────
        if style_anchor and not is_first_scene:
            lighting_block = f"[LOCKED STYLE from scene 1] {style_anchor}"
        else:
            lighting_block = cls._build_lighting(lighting or {})

        # ── Block 3: Voiceover ────────────────────────────────────────────────
        vo_block = cls._build_voiceover_style(vo_props or {}, language, voiceover)

        # ── Block 4: Hook overlay (scene 1 only) ──────────────────────────────
        hook_block = ""
        if is_first_scene and hook:
            hook_block = (
                f'OPENING HOOK ({hook.get("duration_seconds", 3)}s): bold on-screen text reads '
                f'"{hook.get("text", "")}" — eye-catching, high contrast, centered.'
            )

        # ── Block 5: CTA overlay (last scene only) ────────────────────────────
        cta_block = ""
        if is_last_scene and cta:
            cta_block = (
                f'END CALL-TO-ACTION: overlay text "{cta.get("text", "")}" '
                f'appears at {cta.get("placement", "end")} of video.'
            )

        # ── Block 6: Scene visuals ────────────────────────────────────────────
        visual_block = f"Scene visuals: {visuals}."

        # ── Block 7: Text overlay ─────────────────────────────────────────────
        overlay_block = f'On-screen text overlay: "{text_overlay}".' if text_overlay else ""

        # ── Block 8: Brand / style ────────────────────────────────────────────
        style_block = (
            f"Brand color {brand_color} used. {color_notes} "
            f"Pacing: {pacing}. Transitions: {transitions}. "
            f"Vertical 9:16 format, professional social-media quality."
        )

        flat = " ".join(filter(None, [
            char_block,
            lighting_block,
            hook_block,
            visual_block,
            vo_block,
            overlay_block,
            cta_block,
            style_block,
        ])).strip()

        return flat, {"flat_prompt": flat, "scene": scene.get("scene", 1)}


class VideoJoiner:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.concat_dir = os.path.join(output_dir, "concat")
        os.makedirs(self.concat_dir, exist_ok=True)

    @staticmethod
    def _ffmpeg_available():
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _write_concat_list(self, scene_paths, idea_idx):
        list_path = os.path.join(self.concat_dir, f"idea_{idea_idx + 1}_concat.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for path in scene_paths:
                f.write(f"file '{os.path.abspath(path).replace(chr(92), '/')}'\n")
        return list_path

    def _run_ffmpeg(self, list_path, output_path):
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if result.returncode == 0:
            return True
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c:v", "libx264", "-preset", "fast",
             "-crf", "18", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        return result.returncode == 0

    def join(self, scene_paths, idea_idx):
        valid = [p for p in scene_paths if p and os.path.isfile(p)]
        if len(valid) == 0:
            return None
        if len(valid) == 1:
            return valid[0]
        if not self._ffmpeg_available():
            return None
        output_path = os.path.join(self.output_dir, f"idea_{idea_idx + 1}_full.mp4")
        list_path   = self._write_concat_list(valid, idea_idx)
        if not self._run_ffmpeg(list_path, output_path):
            return None
        return output_path


class VideoGenerator:
    MODEL    = "google/veo-3.1-i2v"
    BASE_URL = "https://api.aimlapi.com/v2"

    def __init__(
        self,
        api_key,
        image_url,
        language    = "Egyptian Arabic",
        brand_colors = None,
        aspect_ratio = "9:16",
        poll_interval = 20,
        output_dir  = "output_videos",
        model       = None,
    ):
        self.api_key       = api_key
        self.image_url     = image_url or ""
        self.language      = language
        self.brand_colors  = brand_colors or [None]
        self.aspect_ratio  = aspect_ratio
        self.poll_interval = poll_interval
        self.output_dir    = output_dir
        self.video_model   = model or self.MODEL
        os.makedirs(output_dir, exist_ok=True)
        self.joiner = VideoJoiner(output_dir)

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }

    def _submit(self, prompt: str) -> Optional[str]:
        """
        Submit a scene prompt to the AIML API.

        When self.image_url is set the model receives the image as its i2v
        reference frame — this is the primary visual anchor.  The prompt
        still contains a short "match the reference image" hint.
        When self.image_url is empty the text-only character anchor in the
        prompt is the only consistency mechanism.
        """
        payload: dict = {
            "model":        self.video_model,
            "prompt":       prompt,
            "aspect_ratio": self.aspect_ratio,
        }
        # Only include image_url when it is actually set
        if self.image_url:
            payload["image_url"] = self.image_url

        try:
            resp = requests.post(
                f"{self.BASE_URL}/video/generations",
                json=payload, headers=self._headers(), timeout=60,
            )
            if resp.status_code >= 400:
                print(f"    [!] Submit error {resp.status_code}: {resp.text}")
                return None
            return resp.json().get("id")
        except requests.RequestException as e:
            print(f"    [!] Submit request failed: {e}")
            return None

    def _poll(self, gen_id: str) -> Optional[str]:
        print(f"    [~] Polling {gen_id}", end="", flush=True)
        while True:
            time.sleep(self.poll_interval)
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/video/generations",
                    params={"generation_id": gen_id},
                    headers=self._headers(), timeout=30,
                )
                if resp.status_code >= 400:
                    print(f"\n    [!] Poll error: {resp.text}")
                    return None
                data   = resp.json()
                status = data.get("status", "")
                print(".", end="", flush=True)
                if status == "completed":
                    print(" ✓")
                    return data.get("video", {}).get("url")
                elif status in ("failed", "error"):
                    print(" ✗")
                    return None
            except requests.RequestException as e:
                print(f"\n    [!] Poll failed: {e}")
                return None

    def _download(self, url: str, filename: str) -> str:
        path = os.path.join(self.output_dir, filename)
        try:
            resp = requests.get(url, timeout=120)
            with open(path, "wb") as f:
                f.write(resp.content)
            return path
        except requests.RequestException as e:
            print(f"    [!] Download failed: {e}")
            return url

    @staticmethod
    def _safe_get(idea, *keys, default=None):
        for key in keys:
            val = idea.get(key)
            if val is not None and val != {} and val != []:
                return val
        return default if default is not None else {}

    @staticmethod
    def _merge_scene_delta(scene: dict, prev_scene: dict) -> dict:
        if not prev_scene:
            return scene
        merged = dict(prev_scene)
        merged.update(scene)
        if scene.get("use_character") is False:
            merged.pop("character_details", None)
            return merged
        for key in ("character_details", "lighting_conditions", "visual_direction"):
            prev_val = prev_scene.get(key) or {}
            curr_val = scene.get(key) or {}
            if prev_val or curr_val:
                merged[key] = {**prev_val, **curr_val}
        return merged

    def _save_idea_json(self, idea: dict, idea_idx: int, scenes: list) -> str:
        caption  = idea.get("caption", "")
        hashtags = idea.get("hashtags", [])
        if isinstance(caption, list):
            caption = " ".join(str(c) for c in caption)
        idea_data = {
            "idea_index":       idea_idx + 1,
            "caption":          str(caption),
            "hashtags":         hashtags,
            "hook":             idea.get("hook", {}),
            "cta":              idea.get("cta", {}),
            "script":           idea.get("script", []),
            "generated_scenes": [s for s in scenes if s.get("status") == "completed"],
        }
        json_path = os.path.join(self.output_dir, f"idea_{idea_idx + 1}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(idea_data, f, ensure_ascii=False, indent=2)
        return json_path

    def generate_all(self, content_json: dict) -> list[VideoResult]:
        ideas   = content_json.get("ideas", [])
        results = []
        builder = VeoPromptBuilder()
        idea_scene_paths: dict = {}

        has_image = bool(self.image_url and self.image_url.strip())
        print(
            f"\n🎬 Starting video generation for {len(ideas)} idea(s)…"
            f"  [image ref: {'✓ ' + self.image_url[:60] if has_image else '✗ text-only'}]\n"
        )

        for idea_idx, idea in enumerate(ideas):
            hook             = idea.get("hook", {})
            script           = idea.get("script", [])
            cta              = idea.get("cta", {})
            n_scenes         = len(script)
            visual_direction = self._safe_get(idea, "visual_direction", "visual direction")
            character        = self._safe_get(idea, "character_details",
                                              "charachter details", "character details")
            lighting         = self._safe_get(idea, "lighting_conditions",
                                              "Lighting condition ", "Lighting condition")
            vo_props         = self._safe_get(idea, "voiceover_properties",
                                              "Voice over property", "voiceover_props")

            print(f"\n━━━ Idea {idea_idx + 1}/{len(ideas)} | {n_scenes} scene(s) ━━━")

            scenes_output: list = []
            prev_scene:    dict = {}
            idea_scene_paths[idea_idx] = []

            character_anchor = ""
            style_anchor     = ""

            for scene_idx, scene in enumerate(script):
                is_first = scene_idx == 0
                is_last  = scene_idx == n_scenes - 1
                scene_num = scene.get("scene", scene_idx + 1)

                full_scene      = self._merge_scene_delta(scene, prev_scene)
                prev_scene      = full_scene
                scene_character = full_scene.get("character_details") or character or {}
                scene_lighting  = full_scene.get("lighting_conditions") or lighting or {}

                # Build anchors on first scene (text-only path)
                if is_first and not has_image:
                    character_anchor = VeoPromptBuilder._build_character_text(scene_character)
                    style_parts = []
                    if scene_lighting:
                        style_parts.append(VeoPromptBuilder._build_lighting(scene_lighting))
                    vd = visual_direction or {}
                    if vd.get("pacing"):
                        style_parts.append(f"pacing: {vd['pacing']}")
                    style_anchor = " | ".join(filter(None, style_parts))

                print(f"\n  ▶ Scene {scene_num}/{n_scenes}"
                      f"  [{'image ref' if has_image else 'text anchor'}]")

                prompt, _ = builder.build(
                    scene            = full_scene,
                    hook             = hook,
                    cta              = cta,
                    visual_direction = visual_direction,
                    brand_colors     = self.brand_colors,
                    language         = self.language,
                    image_url        = self.image_url,      # ← passed through
                    character        = scene_character,
                    character_anchor = character_anchor,
                    style_anchor     = style_anchor,
                    lighting         = scene_lighting,
                    vo_props         = vo_props,
                    is_first_scene   = is_first,
                    is_last_scene    = is_last,
                )

                print(f"  📝 Prompt ({len(prompt)} chars): {prompt[:120]}…")

                gen_id = self._submit(prompt)
                if not gen_id:
                    scenes_output.append({
                        "scene": scene_num, "status": "failed",
                        "error": "Submission failed",
                    })
                    results.append(VideoResult(
                        idea_index=idea_idx, scene_index=scene_idx,
                        generation_id="", status="failed",
                        error="Submission failed",
                    ))
                    continue

                video_url = self._poll(gen_id)
                if not video_url:
                    scenes_output.append({
                        "scene": scene_num, "status": "failed",
                        "generation_id": gen_id, "error": "Generation failed",
                    })
                    results.append(VideoResult(
                        idea_index=idea_idx, scene_index=scene_idx,
                        generation_id=gen_id, status="failed",
                        error="Generation failed",
                    ))
                    continue

                filename   = f"idea{idea_idx + 1}_scene{scene_num}.mp4"
                local_path = self._download(video_url, filename)
                print(f"  ✅ Saved → {local_path}")

                scenes_output.append({
                    "scene": scene_num, "status": "completed",
                    "generation_id": gen_id, "video_path": local_path,
                })
                results.append(VideoResult(
                    idea_index=idea_idx, scene_index=scene_idx,
                    generation_id=gen_id, status="completed",
                    video_url=local_path,
                ))
                idea_scene_paths[idea_idx].append(local_path)

            json_path       = self._save_idea_json(idea, idea_idx, scenes_output)
            full_video_path = self.joiner.join(idea_scene_paths[idea_idx], idea_idx)

            if full_video_path:
                try:
                    with open(json_path, "r") as f:
                        d = json.load(f)
                    d["full_video_path"] = full_video_path
                    with open(json_path, "w") as f:
                        json.dump(d, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        done   = [r for r in results if r.status == "completed"]
        failed = [r for r in results if r.status == "failed"]
        print(f"\n📊 SUMMARY — ✅ {len(done)} scenes completed | ❌ {len(failed)} failed")
        return results