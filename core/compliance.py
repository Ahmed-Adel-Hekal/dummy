"""core/compliance.py"""
from __future__ import annotations
import copy, re

class ContentComplianceGuard:
    PROHIBITED_TERMS = {"hate","kill","racist","terrorist","suicide","nazi"}
    CLAIM_REPLACEMENTS = {
        r"\bguaranteed\b": "can help",
        r"\b100%\b": "highly",
        r"\bno risk\b": "lower risk",
        r"\binstant results?\b": "faster results",
        r"\bcure\b": "improve",
        r"\bget rich quick\b": "grow steadily",
    }
    RISKY_PATTERNS = [
        ("harmful_term",     re.compile(r"\b(hate|kill|terrorist|suicide|nazi|racist)\b", re.I), "high"),
        ("medical_claim",    re.compile(r"\b(cure|treat disease|guaranteed recovery)\b",  re.I), "medium"),
        ("financial_promise",re.compile(r"\b(100% return|guaranteed profit|get rich quick)\b",re.I),"medium"),
    ]

    def __init__(self, language="English"):
        self.language = language

    def _normalize_ws(self, text):
        return " ".join(str(text or "").split())

    def _sanitize_text(self, text):
        updated = self._normalize_ws(text)
        issues = []
        for pattern, replacement in self.CLAIM_REPLACEMENTS.items():
            if re.search(pattern, updated, re.I):
                updated = re.sub(pattern, replacement, updated, flags=re.I)
                issues.append({"issue":"claim_softened","severity":"medium"})
        for term in self.PROHIBITED_TERMS:
            if re.search(rf"\b{re.escape(term)}\b", updated, re.I):
                updated = re.sub(rf"\b{re.escape(term)}\b", "***", updated, flags=re.I)
                issues.append({"issue":"harmful_term_masked","severity":"high"})
        return self._normalize_ws(updated), issues

    def _scan_severity(self, text):
        order = {"none":0,"low":1,"medium":2,"high":3}
        max_lvl = "none"
        for _, regex, severity in self.RISKY_PATTERNS:
            if regex.search(str(text or "")):
                if order[severity] > order[max_lvl]:
                    max_lvl = severity
        return max_lvl

    def _normalize_hashtags(self, hashtags):
        seen, out = set(), []
        for tag in hashtags or []:
            cleaned = re.sub(r"[^A-Za-z0-9_]","",str(tag).replace("#",""))[:32]
            low = cleaned.lower()
            if cleaned and low not in seen:
                seen.add(low)
                out.append(cleaned)
        return out[:12]

    def _safe_static(self, topic, idx):
        return {"hook":f"Simple framework for {topic} (Idea {idx})","post_copy":f"A practical approach to {topic}.","hashtags":["marketing","content","strategy"],"image_description":f"Clean brand-safe visual about {topic}","visual_direction":"clean, positive, professional"}

    def _safe_video(self, topic, idx):
        return {"hook":{"text":f"{topic}: practical tips (Idea {idx})","duration_seconds":3},"script":[{"scene":1,"visuals":f"Context around {topic}","voiceover":"Let us break this down simply.","duration_seconds":8},{"scene":2,"visuals":"Three clear steps on screen","voiceover":"Use this framework.","duration_seconds":8},{"scene":3,"visuals":"Positive CTA slide","voiceover":"Save this and apply it today.","duration_seconds":8}],"caption":f"Practical tips for {topic}.","hashtags":["marketing","content","socialmedia"],"cta":{"text":"Save for later","placement":"end"},"estimated_duration_seconds":24,"visual_direction":{"pacing":"medium","transitions":"cut","color_usage":"clean"}}

    def moderate_content(self, payload, content_type, topic):
        moderated = copy.deepcopy(payload if isinstance(payload,dict) else {"ideas":[]})
        ideas = moderated.setdefault("ideas",[])
        report = {"total_ideas":len(ideas),"sanitized_count":0,"replaced_count":0,"issues":[],"status":"passed"}
        for idx, idea in enumerate(ideas):
            local_issues = []
            if content_type == "video":
                hook = idea.get("hook",{})
                if isinstance(hook,dict):
                    text,iss = self._sanitize_text(hook.get("text",""))
                    hook["text"] = text
                    local_issues += [{"field":"hook.text",**i} for i in iss]
                caption,iss = self._sanitize_text(idea.get("caption",""))
                idea["caption"] = caption
                local_issues += [{"field":"caption",**i} for i in iss]
                for si,scene in enumerate(idea.get("script",[]) or []):
                    v,iss = self._sanitize_text(scene.get("voiceover",""))
                    scene["voiceover"] = v
                    local_issues += [{"field":f"script[{si}].voiceover",**i} for i in iss]
                    vs,iss = self._sanitize_text(scene.get("visuals",""))
                    scene["visuals"] = vs
                    local_issues += [{"field":f"script[{si}].visuals",**i} for i in iss]
            else:
                hook,iss = self._sanitize_text(idea.get("hook",""))
                idea["hook"] = hook
                local_issues += [{"field":"hook",**i} for i in iss]
                copy_,iss = self._sanitize_text(idea.get("post_copy",""))
                idea["post_copy"] = copy_
                local_issues += [{"field":"post_copy",**i} for i in iss]
            idea["hashtags"] = self._normalize_hashtags(idea.get("hashtags",[]))
            combined = " ".join([str(idea.get("hook","")),str(idea.get("post_copy","")),str(idea.get("caption",""))])
            if self._scan_severity(combined) == "high":
                report["replaced_count"] += 1
                ideas[idx] = (self._safe_video(topic,idx+1) if content_type=="video" else self._safe_static(topic,idx+1))
                local_issues.append({"field":"idea","issue":"replaced_for_compliance","severity":"high"})
            elif local_issues:
                report["sanitized_count"] += 1
            if local_issues:
                report["issues"].append({"idea_index":idx,"details":local_issues})
        if report["replaced_count"] > 0: report["status"] = "adjusted"
        elif report["sanitized_count"] > 0: report["status"] = "sanitized"
        return moderated, report
