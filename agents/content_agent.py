"""agents/content_agent.py — Content generation pipeline"""
from __future__ import annotations
import os, random, datetime
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.gemini_client import Agent
from core.compliance import ContentComplianceGuard
from media.video_generator import parse_llm_json

try:
    from media.video_generator import VideoGenerator
except Exception:
    VideoGenerator = None

try:
    from media.static_post import StaticPostGenerator
except Exception:
    StaticPostGenerator = None

VEO3_CLIP_MAX_SECONDS  = 8
VEO3_EXTENSION_SECONDS = 7
VEO3_MAX_EXTENSIONS    = 20
VEO3_MAX_TOTAL_SECONDS = VEO3_CLIP_MAX_SECONDS + (VEO3_EXTENSION_SECONDS * VEO3_MAX_EXTENSIONS)

PLATFORM_DURATION = {
    "TikTok":    {"min":15,"max":60,"ideal":30},
    "Instagram": {"min":8,"max":60,"ideal":30},
    "X":         {"min":8,"max":140,"ideal":30},
    "Facebook":  {"min":15,"max":90,"ideal":45},
    "LinkedIn":  {"min":15,"max":90,"ideal":45},
}

def get_target_duration(platforms):
    if not platforms:
        return {"ideal_total":32,"min_total":8,"max_total":60,"scene_count":4,"seconds_each":VEO3_CLIP_MAX_SECONDS}
    ideals = [PLATFORM_DURATION.get(p,{"ideal":30})["ideal"] for p in platforms]
    mins   = [PLATFORM_DURATION.get(p,{"min":8})["min"]    for p in platforms]
    maxs   = [PLATFORM_DURATION.get(p,{"max":60})["max"]   for p in platforms]
    ideal  = min(ideals); total = min(ideal,VEO3_MAX_TOTAL_SECONDS)
    scene_count = max(1, round(total/VEO3_CLIP_MAX_SECONDS))
    return {"ideal_total":scene_count*VEO3_CLIP_MAX_SECONDS,"min_total":min(mins),"max_total":min(max(maxs),VEO3_MAX_TOTAL_SECONDS),"scene_count":scene_count,"seconds_each":VEO3_CLIP_MAX_SECONDS}

@dataclass
class AgentConfig:
    llm_provider:    str        = "google"
    llm_api_key:     str | None = None
    video_content:   bool       = False
    images:          bool       = True
    brand_color:     list       = field(default_factory=lambda: ["#3B82F6"])
    brand_img:       str | None = None
    target_platform: list       = field(default_factory=lambda: ["Instagram"])
    model:           str        = "gemini-2.5-flash"
    language:        str        = "English"
    number_idea:     int        = 3

TONES = ["bold and provocative","warm and conversational","witty and playful","inspirational and motivational","educational and informative","urgent and FOMO-driven","minimalist and premium","storytelling / narrative-first","raw and unfiltered","humorous and self-aware","controversial and debate-sparking","empathetic and validating","authoritative and expert","nostalgic and sentimental","futuristic and visionary"]
CONTENT_ANGLES = ["problem → solution","before & after transformation","myth-busting","behind the scenes","customer pain point empathy","social proof via unexpected story","trending moment tie-in","question-led curiosity hook","listicle format","unpopular opinion","day-in-the-life","the mistake most people make","comparison: old way vs new way","founder story","future vision","3-2-1 framework","user-generated content style","trend forecast"]
VISUAL_STYLES = ["flat lay","lifestyle in-use","close-up macro","editorial fashion-forward","outdoor golden hour","dark moody studio","vibrant street/urban","cozy indoor warm-tone","minimalist white void","split-before-after","infographic overlay","motion blur/kinetic","pastel dreamscape","gritty film grain","bold typographic","collage mixed media"]
VIDEO_PACING = ["rapid-fire cuts","medium flow","slow cinematic","punchy hook then slow reveal"]
VIDEO_TRANSITIONS = ["smash cuts","whip pans","match cuts","slow zoom in","fade through black","j-cut"]
VIDEO_OPENERS = ["open on a question","open mid-action","open with shocking result","open with a close-up","open with bold text on black","open with ambient sound"]
NARRATIVE_STRUCTURES = ["problem → agitate → solve","hook → proof → offer","story → lesson → CTA","before → after → bridge","question → exploration → answer","myth → reality → implication"]

def build_variation_context():
    import time
    random.seed(int(time.time()*1_000_000)%(2**32))
    return {
        "tone":               random.choice(TONES),
        "angle":              random.choice(CONTENT_ANGLES),
        "visual_style":       random.choice(VISUAL_STYLES),
        "pacing":             random.choice(VIDEO_PACING),
        "transition":         random.choice(VIDEO_TRANSITIONS),
        "video_opener":       random.choice(VIDEO_OPENERS),
        "narrative_structure":random.choice(NARRATIVE_STRUCTURES),
        "timestamp":          datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
    }

def _format_competitor_context(comp_insight):
    if not comp_insight: return ""
    if isinstance(comp_insight,str): return f"Competitor Insights: {comp_insight[:800]}"
    if not isinstance(comp_insight,dict): return ""
    lines = ["=== COMPETITOR INTELLIGENCE ==="]
    if comp_insight.get("brand_overview"): lines.append(f"Overview: {comp_insight['brand_overview']}")
    if comp_insight.get("top_hooks"):
        lines.append("Top hooks used by competitors:")
        lines += [f"  - {h}" for h in comp_insight["top_hooks"][:5]]
    if comp_insight.get("content_patterns"):
        lines.append("Content patterns:")
        lines += [f"  - {p}" for p in comp_insight["content_patterns"][:4]]
    if comp_insight.get("gap_opportunities"):
        lines.append("Gap opportunities:")
        lines += [f"  - {g}" for g in comp_insight["gap_opportunities"][:4]]
    if comp_insight.get("tone_summary"): lines.append(f"Competitor tone: {comp_insight['tone_summary']}")
    if comp_insight.get("keyword_cloud"): lines.append(f"Key terms: {', '.join(comp_insight['keyword_cloud'][:12])}")
    lines.append("=== USE THIS TO CREATE BETTER CONTENT ===")
    return "\n".join(lines)

class ContentAgent(Agent):
    def __init__(self, config):
        super().__init__(provider=config.llm_provider, model=config.model, api_key=config.llm_api_key)
        self.config = config; self.full_prompt = None; self.brand_file = None

    def _build_schema(self, content_type, duration_info=None):
        base = f"Generate {self.config.number_idea} high-converting posts in JSON format in {self.config.language} matching this schema exactly:\n"
        if content_type == "Video":
            sc = duration_info["scene_count"]  if duration_info else 4
            se = duration_info["seconds_each"] if duration_info else 8
            it = duration_info["ideal_total"]  if duration_info else 32
            hd = min(3,se)
            return base + f'''
RULES: Each scene = exactly {se}s | exactly {sc} scenes per idea | exactly {self.config.number_idea} ideas total
{{"ideas":[{{"hook":{{"text":"...","duration_seconds":{hd}}},"voiceover_properties":{{"gender":"male/female","tone":"..."}},"cta":{{"text":"...","placement":"end"}},"caption":"...","hashtags":["#tag1","#tag2"],"estimated_duration_seconds":{it},"script":[{{"scene":1,"visuals":"full description","voiceover":"exact words","duration_seconds":{se},"use_character":true,"character_details":{{"eye_color":"...","facial_details":"...","physical_details":"...","facial_expression":"..."}},"lighting_conditions":{{"camera_angle":"medium shot","camera_type":"DSLR","lighting_mode":"soft natural light","lighting_position":"side-lit","camera_movement":"static"}},"visual_direction":{{"color_usage":"","transition":"cut","pacing":"medium"}}}},{{"scene":2,"visuals":"...","voiceover":"...","duration_seconds":{se},"character_details":{{"facial_expression":"hopeful smile"}},"lighting_conditions":{{"camera_angle":"close-up","camera_movement":"slow zoom"}},"visual_direction":{{"color_usage":"brand color on prop","transition":"whip pan","pacing":"medium"}}}}]}}]}}'''
        else:
            return base + '''
{{"ideas":[{{"hook":"short punchy opening line","post_copy":"full social media post. engaging, conversational, on-brand. 3-5 sentences with emojis.","image_description":"detailed cinematic visual description","hashtags":["#tag1","#tag2","#tag3","#tag4","#tag5"],"visual_direction":"how to use brand colors in the image"}}]}}'''

    def generate_prompt(self, user_topic, comp_insight=None, trend_insight=None,
                        variation=None, duration_info=None, brand_block=None, features_block=None):
        content_type = "Video" if self.config.video_content else "Static"
        variation_block = ""
        if variation:
            variation_block = (f"\nCreative Direction (MANDATORY):\n- Tone: {variation['tone']}\n- Angle: {variation['angle']}\n- Visual Style: {variation['visual_style']}\n- Narrative Structure: {variation.get('narrative_structure','problem → solution')}\n")
            if content_type == "Video":
                variation_block += f"- Video Opener: {variation.get('video_opener','open on a question')}\n- Pacing: {variation['pacing']}\n- Transitions: {variation['transition']}\n"
            variation_block += f"- Unique seed: {variation['timestamp']}\n"
        duration_block = ""
        if content_type == "Video" and duration_info:
            duration_block = f"\nPlatform Duration:\n- Target: {', '.join(self.config.target_platform)}\n- Ideal total: {duration_info['ideal_total']}s\n- Scenes: {duration_info['scene_count']} × {duration_info['seconds_each']}s\n"
        comp_block = _format_competitor_context(comp_insight)
        _ar_note = ""
        if "arabic" in self.config.language.lower():
            _ar_note = "\nCRITICAL: Output MUST be in Arabic. Hooks, captions, hashtags — ALL in Arabic. Use natural, native-sounding Arabic (not translation). Hashtags should be in Arabic with # prefix."
        system = f"You are an expert Social Media Content Creator.\nTarget Platforms: {', '.join(self.config.target_platform)}\nBrand Colors: {self.config.brand_color}, Type: {content_type}\nLanguage: {self.config.language}{_ar_note}\n{variation_block}{duration_block}"
        # Only include sections that have actual data — reduces token count & latency
        context_parts = [f"User Topic: {user_topic}"]
        if brand_block and len(brand_block.strip()) > 20:
            context_parts.insert(0, brand_block)
        if features_block and len(features_block.strip()) > 20:
            context_parts.append(features_block)
        if comp_block and len(comp_block.strip()) > 30:
            context_parts.append(comp_block)
        if trend_insight and len(str(trend_insight).strip()) > 20:
            context_parts.append(f"Current Trends: {trend_insight}")
        context = "\n".join(context_parts)
        schema  = self._build_schema(content_type, duration_info)
        gold_static = '''GOLD EXAMPLE (static post — match this quality):
{"ideas":[{"hook":"You're not behind — you're just one framework away.","post_copy":"Stop chasing every new tool. The brands winning right now picked ONE system and ran it for 90 days. Here's the 3-step loop we use: 1) Test the hook. 2) Repurpose the winner. 3) Kill what doesn't convert. Simple. Boring. Works every time.","hashtags":["#ContentStrategy","#MarketingTips","#GrowthHacking"],"image_description":"Clean overhead desk shot: notebook with 'Day 90' written in bold marker, surrounded by scattered sticky notes — one circled in red marker","visual_direction":"Warm candlelit tones, minimal clutter, brand accent on the circled sticky note"}]}'''
        gold_video = '''GOLD EXAMPLE (video — match this quality):
{"ideas":[{"hook":{"text":"Nobody tells you this about going viral","duration_seconds":3},"script":[{"scene":1,"visuals":"Creator sitting at desk, leaning toward camera with conspiratorial look","voiceover":"Nobody tells you this about going viral..."},{"scene":2,"visuals":"Split-screen: left exhausted creator posting daily; right calm creator posting weekly but studying analytics","voiceover":"It's not posting more — it's knowing which 20% of your content drives 80% of your growth."}],"caption":"Work smarter, not louder. Save this before your next content sprint.","hashtags":["#ContentCreator","#VideoMarketing","#ViralStrategy"],"cta":{"text":"Save this","placement":"end"},"estimated_duration_seconds":16}]}'''
        gold_example = gold_video if self.config.video_content else gold_static
        critique = f"""CRITICAL RULES:
1. Follow the Creative Direction above exactly — tone, angle, style are MANDATORY.
2. The hook must stop a fast scroll — NO generic openers, NO "Are you..." questions.
3. NEVER use: game-changer, revolutionary, unlock, dive into, leverage, seamless, cutting-edge, delve.
4. Every sentence must sound like a real person wrote it — not an AI.
5. Your response is ONLY valid JSON — no markdown, no preamble, no explanation.
{gold_example}
"""
        self.full_prompt = f"{system}\n{context}\n{schema}\n{critique}"
        return self.full_prompt

    def generate(self, topic, comp_insight=None, trend_insight=None, brand_block=None, features_block=None):
        variation     = build_variation_context()
        duration_info = None
        if self.config.video_content:
            duration_info = get_target_duration(self.config.target_platform)
        self.generate_prompt(topic, comp_insight, trend_insight,
                             variation=variation, duration_info=duration_info,
                             brand_block=brand_block, features_block=features_block)
        # Use higher temperature for creativity, strict JSON schema needs lower
        temperature = 0.88 if self.config.video_content else 0.82
        raw = self.ask(self.full_prompt, max_tokens=4096, temperature=temperature)
        try:
            with open("result.txt","w",encoding="utf-8") as f: f.write(raw)
        except Exception:
            pass
        return raw

def _build_fallback_payload(topic, content_type, number_idea):
    ideas = []
    for i in range(1,max(1,number_idea)+1):
        if content_type == "video":
            ideas.append({"hook":{"text":f"{topic}: 3 things people miss (Idea {i})","duration_seconds":3},"script":[{"scene":1,"visuals":f"Problem context for {topic}","voiceover":f"Most people miss this about {topic}.","duration_seconds":8},{"scene":2,"visuals":"Actionable steps","voiceover":"Here is the framework.","duration_seconds":8},{"scene":3,"visuals":"CTA","voiceover":"Follow for more.","duration_seconds":8}],"caption":f"Quick breakdown for {topic}.","hashtags":["#marketing","#ai"],"cta":{"text":"Follow for more","placement":"end"},"estimated_duration_seconds":24})
        else:
            ideas.append({"hook":f"Stop guessing your {topic} strategy (Idea {i})","post_copy":f"Use this sequence for {topic}: 1) strong hook, 2) one insight, 3) one action.","hashtags":["marketing","content","socialmedia"],"image_description":f"Modern social media graphic about {topic}","visual_direction":"minimal, high-contrast, brand-accented"})
    return {"ideas":ideas}

def _generate_one_idea(idx, topic, content_type, config, comp_insight, trend_insight, brand_block, features_block):
    try:
        one_config = AgentConfig(llm_provider=config.llm_provider, llm_api_key=config.llm_api_key, video_content=config.video_content, images=config.images, brand_color=config.brand_color, brand_img=config.brand_img, target_platform=config.target_platform, model=config.model, language=config.language, number_idea=1)
        agent = ContentAgent(config=one_config)
        raw   = agent.generate(topic, comp_insight=comp_insight, trend_insight=trend_insight, brand_block=brand_block, features_block=features_block)
        if not raw: return idx, None
        parsed = parse_llm_json(raw)
        if not isinstance(parsed,dict): return idx, None
        ideas = parsed.get("ideas",[])
        return idx, ideas[0] if ideas else None
    except Exception as e:
        from core.logger import get_logger
        get_logger("ContentAgent").warning("Idea %d generation failed: %s", idx, e)
        return idx, None

def run_content_pipeline(topic, platforms, content_type, language, brand_color, brand_img,
        number_idea, comp_insight, trend_insight, brand_block="", features_block="",
        output_dir="output_posts", image_url="", aspect_ratio="9:16",
        llm_provider="google", llm_model="gemini-2.5-flash",
        image_model="gemini-3.1-flash-image-preview", video_model="google/veo-3.1-i2v",
        llm_api_key=None, image_api_key=None, video_api_key=None, human_review=False):
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
    try:
        mapped = ["X" if p in ("Twitter/X","Twitter","X") else p for p in platforms]
        platform_literals = [p for p in mapped if p in ("X","Facebook","Instagram","LinkedIn","TikTok")]
        config = AgentConfig(llm_provider=llm_provider, llm_api_key=llm_api_key,
            video_content=(content_type=="video"), images=True, brand_color=brand_color,
            brand_img=brand_img, target_platform=platform_literals,
            model=llm_model or "gemini-2.5-flash", language=language, number_idea=1)
        n = max(1,min(5,number_idea)); ideas_by_idx = {}
        with ThreadPoolExecutor(max_workers=min(n,4)) as pool:
            futures = {pool.submit(_generate_one_idea,i,topic,content_type,config,comp_insight,trend_insight or "",brand_block or "",features_block or ""):i for i in range(n)}
            for fut in _as_completed(futures):
                idx,idea = fut.result()
                if idea is not None: ideas_by_idx[idx] = idea
                else:
                    fb = _build_fallback_payload(topic,content_type,1)
                    ideas_by_idx[idx] = fb.get("ideas",[{}])[0]
        ideas = [ideas_by_idx[i] for i in range(n) if i in ideas_by_idx]
        parsed = {"ideas":ideas}
        warnings = []
        guard = ContentComplianceGuard(language=language)
        parsed, compliance_report = guard.moderate_content(parsed, content_type=content_type, topic=topic)
        if human_review:
            output = {"type":content_type,"ideas":parsed.get("ideas",[]),"results":[],"raw_json":parsed,"compliance_report":compliance_report,"status":"awaiting_approval"}
            return output
        base_dir = os.path.dirname(os.path.abspath(__file__))
        out_dir  = os.path.join(base_dir,"..","..","outputs" if "/agents" in base_dir else output_dir)
        # use the passed output_dir directly if it looks absolute
        if os.path.isabs(output_dir): out_dir = output_dir
        results = run_media_generation(parsed=parsed,content_type=content_type,language=language,brand_color=brand_color,image_url=image_url,aspect_ratio=aspect_ratio,out_dir=output_dir,image_model=image_model,video_model=video_model,llm_api_key=llm_api_key,image_api_key=image_api_key,video_api_key=video_api_key,warnings=warnings)
        output = {"type":content_type,"ideas":parsed.get("ideas",[]),"results":results["results"],"raw_json":parsed,"compliance_report":compliance_report,"status":"completed"}
        if warnings or results.get("warnings"):
            output["warning"] = " | ".join(warnings+results.get("warnings",[]))
        return output
    except Exception as exc:
        return {"type":content_type,"ideas":[],"results":[],"raw_json":{"ideas":[]},"error":str(exc),"status":"failed"}

def run_media_generation(parsed, content_type, language, brand_color, image_url, aspect_ratio,
        out_dir, image_model="gemini-3.1-flash-image-preview", video_model="google/veo-3.1-i2v",
        llm_api_key=None, image_api_key=None, video_api_key=None, warnings=None):
    from dataclasses import asdict, is_dataclass
    def _to_dict(obj):
        if is_dataclass(obj) and not isinstance(obj,type): return asdict(obj)
        if isinstance(obj,list): return [_to_dict(i) for i in obj]
        return obj
    warnings = warnings or []; results = []
    if content_type == "video":
        key = (video_api_key or llm_api_key or os.environ.get("AIML_API_KEY",""))
        if VideoGenerator is None or not key:
            warnings.append("Video generation unavailable: missing AIML_API_KEY.")
            results = [{"idea_index":i,"status":"mock_only","error":"Video generation unavailable"} for i,_ in enumerate(parsed.get("ideas",[]))]
        else:
            gen = VideoGenerator(api_key=key,image_url=image_url,language=language,brand_colors=brand_color,aspect_ratio=aspect_ratio,output_dir=out_dir,model=video_model)
            results = _to_dict(gen.generate_all(parsed))
    else:
        key = (image_api_key or llm_api_key or os.environ.get("GEMINI_API_KEY",""))
        if StaticPostGenerator is None or not key:
            warnings.append("Image generation unavailable: missing GEMINI_API_KEY.")
            results = [{"idea_index":i,"status":"mock_only","error":"Image generation unavailable"} for i,_ in enumerate(parsed.get("ideas",[]))]
        else:
            gen = StaticPostGenerator(gemini_api_key=key,brand_colors=brand_color,output_dir=out_dir,aspect_ratio="4:5",model=image_model)
            results = _to_dict(gen.generate_all(parsed))
    return {"results":results,"warnings":warnings}
