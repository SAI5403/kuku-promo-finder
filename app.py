%%writefile app.py
import streamlit as st
import pandas as pd
import json
import os
import io
import re
import zipfile
from typing import List, Dict, Any, Optional

# Optional Gemini client support
try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

# Set Page Config
st.set_page_config(
    page_title="Kuku PromoAI — Microdrama Ad Finder",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom High-End Styling
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] { font-family: 'Plus Jakarta Sans', sans-serif; }
    
    .hero-header {
        background: linear-gradient(135deg, #1e1e2f 0%, #11111b 100%);
        padding: 24px 30px;
        border-radius: 16px;
        color: #ffffff;
        margin-bottom: 24px;
        border: 1px solid rgba(255, 255, 255, 0.08);
        box-shadow: 0 10px 30px rgba(0,0,0,0.25);
    }
    .hero-title {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(90deg, #FF4B4B, #FF8F6B);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 6px;
    }
    .hero-sub { color: #A0A5B5; font-size: 1.05rem; }
    .metric-card {
        background: #181926;
        padding: 16px 18px;
        border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.07);
        text-align: center;
    }
    .metric-val { font-size: 1.9rem; font-weight: 800; color: #ffffff; margin: 4px 0; }
    .metric-lbl { color: #8C92A4; font-size: 0.8rem; text-transform: uppercase; font-weight: 600; }
    .promo-card {
        background: #191A23;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 14px;
        padding: 20px 24px;
        margin-bottom: 16px;
        transition: transform 0.15s ease, border-color 0.15s ease;
    }
    .promo-card:hover { border-color: #FF4B4B; transform: translateY(-2px); }
    .badge-score {
        background: rgba(255, 75, 75, 0.15);
        color: #FF5A5A;
        border: 1px solid rgba(255, 75, 75, 0.35);
        padding: 4px 10px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 0.9rem;
    }
    .badge-dur {
        background: rgba(0, 195, 255, 0.12);
        color: #38bdf8;
        border: 1px solid rgba(0, 195, 255, 0.3);
        padding: 4px 10px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# STAGES 1 - 9 CORE PIPELINE (MATCHING main.ipynb)
# ==============================================================================

def extract_episode_id(file_name: str, fallback_idx: int) -> str:
    patterns = [r"ep(?:isode)?[\s_-]*(\d+)", r"(?:^|[^\d])(\d{1,3})(?:[^\d]|$)"]
    for pattern in patterns:
        match = re.search(pattern, file_name, re.IGNORECASE)
        if match:
            return f"EP{int(match.group(1)):03d}"
    return f"EP{fallback_idx:03d}"

def timestamp_to_seconds(ts_str: str) -> float:
    if not ts_str:
        return 0.0
    ts_str = ts_str.strip().strip("[]()")
    parts = ts_str.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 1:
            return float(parts[0])
    except ValueError:
        return 0.0
    return 0.0

def seconds_to_timestamp(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"

def classify_line_content(text: str) -> Dict[str, Any]:
    clean_text = text.strip()
    heading_pattern = r"^(?:INT\.|EXT\.|INT/EXT\.|EXT/INT\.|SCENE\s+\d+|CUT\s+TO:)"
    if re.match(heading_pattern, clean_text, re.IGNORECASE):
        return {"type": "SCENE_HEADING", "character": None, "content": clean_text}
    dialogue_match = re.match(r"^([A-Z0-9\s\.\(\)\'\-]+?)\s*:\s*(.*)$", clean_text)
    if dialogue_match and not clean_text.upper().startswith(("NOTE:", "SCENE:", "TIME:")):
        speaker = dialogue_match.group(1).strip()
        speech = dialogue_match.group(2).strip()
        if any(c.isupper() for c in speaker) and len(speaker) <= 30:
            return {"type": "DIALOGUE", "character": speaker, "content": speech}
    return {"type": "ACTION", "character": None, "content": clean_text}

def parse_screenplay_to_cues(ep_id: str, raw_text: str) -> List[Dict[str, Any]]:
    ts_regex = re.compile(
        r"\[?\(?(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\)?\]?"
        r"(?:\s*(?:-|to)\s*\[?\(?(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\)?\]?)?",
        re.IGNORECASE
    )
    parsed_cues = []
    lines = raw_text.splitlines()
    current_time_s = 0.0
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = ts_regex.search(line)
        if match:
            start_str, end_str = match.group(1), match.group(2)
            start_s = timestamp_to_seconds(start_str)
            end_s = timestamp_to_seconds(end_str) if end_str else start_s + 3.0
            current_time_s = end_s
            content_text = line[:match.start()] + line[match.end():]
            content_text = content_text.strip().lstrip(":- ").strip()
        else:
            start_s = current_time_s
            end_s = start_s + 2.5
            current_time_s = end_s
            content_text = line

        if not content_text:
            continue
            
        classification = classify_line_content(content_text)
        parsed_cues.append({
            "ep_id": ep_id,
            "cue_id": len(parsed_cues) + 1,
            "start_s": round(start_s, 2),
            "end_s": round(end_s, 2),
            "start_ts": seconds_to_timestamp(start_s),
            "end_ts": seconds_to_timestamp(end_s),
            "type": classification["type"],
            "character": classification["character"],
            "text": classification["content"]
        })
    return parsed_cues

def segment_episode_into_scenes(
    ep_id: str, 
    cues: List[Dict[str, Any]], 
    max_scene_duration: float = 120.0,
    time_gap_threshold: float = 4.0
) -> List[Dict[str, Any]]:
    if not cues:
        return []
    raw_scenes = []
    current_cue_group = [cues[0]]
    
    for prev_cue, curr_cue in zip(cues[:-1], cues[1:]):
        is_heading = curr_cue["type"] == "SCENE_HEADING"
        time_gap = curr_cue["start_s"] - prev_cue["end_s"]
        is_time_gap = time_gap > time_gap_threshold
        accumulated_duration = curr_cue["end_s"] - current_cue_group[0]["start_s"]
        is_too_long = accumulated_duration > max_scene_duration
        
        if is_heading or is_time_gap or is_too_long:
            raw_scenes.append(current_cue_group)
            current_cue_group = [curr_cue]
        else:
            current_cue_group.append(curr_cue)
            
    if current_cue_group:
        raw_scenes.append(current_cue_group)
        
    structured_scenes = []
    for idx, group in enumerate(raw_scenes, start=1):
        scene_id = f"SC{idx:03d}"
        start_time = group[0]["start_s"]
        end_time = raw_scenes[idx][0]["start_s"] if idx < len(raw_scenes) else group[-1]["end_s"]
            
        formatted_lines = [
            f"[{c['start_ts']}] {c['character'] + ': ' if c['character'] else ''}{c['text']}"
            for c in group
        ]
        structured_scenes.append({
            "episode_id": ep_id,
            "scene_id": scene_id,
            "start_time": round(start_time, 2),
            "end_time": round(end_time, 2),
            "start_ts": seconds_to_timestamp(start_time),
            "end_ts": seconds_to_timestamp(end_time),
            "duration": round(end_time - start_time, 2),
            "cue_count": len(group),
            "cues": group,
            "scene_text": "\n".join(formatted_lines)
        })
    return structured_scenes

def heuristic_semantic_analyzer(scene_text: str) -> Dict[str, Any]:
    text_lower = scene_text.lower()
    conflict_words = ["slap", "shout", "gun", "kill", "liar", "betray", "divorce", "sue", "arrest", "destroy", "bastard"]
    surprise_words = ["what?!", "impossible", "gasp", "reveals", "shock", "unbelievable", "secret", "trunk", "discovered"]
    emotion_words = ["crying", "sobbing", "furious", "screaming", "trembling", "terrified", "smirks", "in tears"]
    mystery_words = ["who are you", "what did you", "offshore", "ledger", "hidden", "why", "where is", "?"]
    
    def score_density(words: List[str], base: float = 5.0, ceiling: float = 9.5) -> float:
        hits = sum(1 for w in words if w in text_lower)
        return min(base + hits * 1.5, ceiling)

    conflict_score = score_density(conflict_words, base=5.5)
    surprise_score = score_density(surprise_words, base=5.0)
    emotion_score = score_density(emotion_words, base=5.5)
    mystery_score = score_density(mystery_words, base=5.0)
    
    first_lines = "\n".join(scene_text.splitlines()[:3]).lower()
    hook_score = 6.0
    if any(w in first_lines for w in conflict_words + surprise_words):
        hook_score = 9.2
    elif "?" in first_lines or "!" in first_lines:
        hook_score = 8.0

    last_lines = "\n".join(scene_text.splitlines()[-3:]).lower()
    cliffhanger_score = 6.0
    if any(w in last_lines for w in mystery_words + conflict_words) or last_lines.endswith("?") or last_lines.endswith("!"):
        cliffhanger_score = 9.0

    return {
        "main_event": "Dramatic confrontation and unexpected discovery.",
        "conflict": "Verbal and physical escalation between key figures.",
        "emotion": "Anger, shock, and mounting tension.",
        "conflict_score": round(conflict_score, 1),
        "surprise_score": round(surprise_score, 1),
        "emotion_score": round(emotion_score, 1),
        "mystery_score": round(mystery_score, 1),
        "hook_score": round(hook_score, 1),
        "cliffhanger_score": round(cliffhanger_score, 1),
        "standalone_score": 8.0
    }

def heuristic_hook_cliffhanger_detector(scene: Dict[str, Any]) -> Dict[str, Any]:
    cues = scene.get("cues", [])
    if not cues:
        return {
            "hook": {"hook_description": "Scene opening", "hook_timestamp": scene["start_ts"], "hook_strength": 6.0},
            "cliffhanger": {"cliffhanger_description": "Scene conclusion", "cliffhanger_timestamp": scene["end_ts"], "cliffhanger_strength": 6.0, "is_unresolved": True},
            "one_line_why_it_works": "Standard narrative progression."
        }

    hook_triggers = ["slap", "shout", "gun", "contract", "pregnant", "kill", "secret", "divorce", "arrest", "bastard", "liar", "stop", "freeze"]
    cliff_triggers = ["who are you", "what did you", "look at me", "never", "dead", "wait", "destroy", "ledger", "reveal", "tell me", "?", "!"]

    best_hook_cue = cues[0]
    hook_desc = f"{best_hook_cue.get('character', 'Action')}: {best_hook_cue['text']}"
    hook_strength = 6.5

    for c in cues[:min(len(cues), 5)]:
        text_lower = c["text"].lower()
        if any(w in text_lower for w in hook_triggers):
            best_hook_cue = c
            hook_desc = f"Shocking confrontation: '{c['text']}'"
            hook_strength = 9.2
            break
        elif c["type"] == "ACTION" and any(act in text_lower for act in ["slap", "hits", "pulls", "enters"]):
            best_hook_cue = c
            hook_desc = f"Sudden action beat: {c['text']}"
            hook_strength = 8.8
            break

    best_cliff_cue = cues[-1]
    cliff_desc = f"Unresolved exit: '{best_cliff_cue['text']}'"
    cliff_strength = 7.0

    for c in reversed(cues[-min(len(cues), 5):]):
        text_lower = c["text"].lower()
        if any(w in text_lower for w in cliff_triggers) or c["text"].endswith("?"):
            best_cliff_cue = c
            cliff_desc = f"High-tension cliffhanger: '{c['text']}'"
            cliff_strength = 9.3
            break
        elif c["text"].endswith("..."):
            best_cliff_cue = c
            cliff_desc = f"Trailing dramatic pause: '{c['text']}'"
            cliff_strength = 8.5
            break

    return {
        "hook": {
            "hook_description": hook_desc,
            "hook_timestamp": best_hook_cue["start_ts"],
            "hook_strength": hook_strength
        },
        "cliffhanger": {
            "cliffhanger_description": cliff_desc,
            "cliffhanger_timestamp": best_cliff_cue["end_ts"],
            "cliffhanger_strength": cliff_strength,
            "is_unresolved": True
        },
        "one_line_why_it_works": f"Opens on immediate tension at {best_hook_cue['start_ts']} and cuts before resolution at {best_cliff_cue['end_ts']}."
    }

def generate_promo_candidates_for_episode(
    ep_id: str,
    scenes_in_ep: List[Dict[str, Any]],
    cues_in_ep: List[Dict[str, Any]],
    min_duration: float = 20.0,
    max_duration: float = 60.0
) -> List[Dict[str, Any]]:
    candidates = []
    hooks = []
    cliffhangers = []
    
    for sc in scenes_in_ep:
        pm = sc.get("promo_moments", {})
        ai = sc.get("ai_analysis", {})
        
        if "hook" in pm and pm["hook"]["hook_strength"] > 0:
            h = pm["hook"]
            h_sec = timestamp_to_seconds(h["hook_timestamp"])
            hooks.append({
                "scene_id": sc["scene_id"],
                "start_s": h_sec,
                "timestamp": h["hook_timestamp"],
                "desc": h["hook_description"],
                "strength": h["hook_strength"],
                "standalone_score": ai.get("standalone_score", 7.5),
                "why_it_works": pm.get("one_line_why_it_works", "")
            })
            
        if "cliffhanger" in pm and pm["cliffhanger"]["cliffhanger_strength"] > 0:
            c = pm["cliffhanger"]
            c_sec = timestamp_to_seconds(c["cliffhanger_timestamp"])
            cliffhangers.append({
                "scene_id": sc["scene_id"],
                "end_s": c_sec,
                "timestamp": c["cliffhanger_timestamp"],
                "desc": c["cliffhanger_description"],
                "strength": c["cliffhanger_strength"],
                "is_unresolved": c.get("is_unresolved", True)
            })

    seen_pairs = set()
    for h in hooks:
        for c in cliffhangers:
            if c["end_s"] <= h["start_s"]:
                continue
            duration = c["end_s"] - h["start_s"]
            if min_duration <= duration <= max_duration:
                start_cue = next((cue for cue in cues_in_ep if cue["start_s"] >= h["start_s"] - 1.0), None)
                end_cue = next((cue for cue in reversed(cues_in_ep) if cue["end_s"] <= c["end_s"] + 1.0), None)
                
                promo_start = start_cue["start_s"] if start_cue else h["start_s"]
                promo_end = end_cue["end_s"] if end_cue else c["end_s"]
                adj_duration = round(promo_end - promo_start, 2)
                
                if not (min_duration <= adj_duration <= max_duration):
                    continue

                hook_lead_time = h["start_s"] - promo_start
                if not (0.0 <= hook_lead_time <= 3.0):
                    promo_start = h["start_s"]
                    adj_duration = round(promo_end - promo_start, 2)
                    if not (min_duration <= adj_duration <= max_duration):
                        continue
                
                pair_key = (promo_start, promo_end)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                
                candidates.append({
                    "episode_id": ep_id,
                    "candidate_type": "single_continuous",
                    "start_s": promo_start,
                    "end_s": promo_end,
                    "start_ts": seconds_to_timestamp(promo_start),
                    "end_ts": seconds_to_timestamp(promo_end),
                    "duration": adj_duration,
                    "segments": [
                        {
                            "segment_order": 1,
                            "episode_id": ep_id,
                            "start_ts": seconds_to_timestamp(promo_start),
                            "end_ts": seconds_to_timestamp(promo_end),
                            "duration": adj_duration
                        }
                    ],
                    "hook": {
                        "timestamp": h["timestamp"],
                        "description": h["desc"],
                        "strength": h["strength"]
                    },
                    "cliffhanger": {
                        "timestamp": c["timestamp"],
                        "description": c["desc"],
                        "strength": c["strength"],
                        "is_unresolved": c["is_unresolved"]
                    },
                    "standalone_score": h["standalone_score"],
                    "one_line_why_it_works": h["why_it_works"] or f"Strong {adj_duration:.0f}s arc hooking within 3s and cutting on unresolved tension."
                })

    if not candidates and hooks:
        max_ep_time = cues_in_ep[-1]["end_s"] if cues_in_ep else 120.0
        target_windows = [30.0, 40.0, 50.0, 20.0, 60.0]
        for h in hooks[:2]:
            for target_w in target_windows:
                p_start = h["start_s"]
                p_end = min(p_start + target_w, max_ep_time)
                dur = round(p_end - p_start, 2)
                if min_duration <= dur <= max_duration and (p_start, p_end) not in seen_pairs:
                    seen_pairs.add((p_start, p_end))
                    candidates.append({
                        "episode_id": ep_id,
                        "candidate_type": "single_continuous",
                        "start_s": p_start,
                        "end_s": p_end,
                        "start_ts": seconds_to_timestamp(p_start),
                        "end_ts": seconds_to_timestamp(p_end),
                        "duration": dur,
                        "segments": [
                            {
                                "segment_order": 1,
                                "episode_id": ep_id,
                                "start_ts": seconds_to_timestamp(p_start),
                                "end_ts": seconds_to_timestamp(p_end),
                                "duration": dur
                            }
                        ],
                        "hook": {
                            "timestamp": h["timestamp"],
                            "description": h["desc"],
                            "strength": h["strength"]
                        },
                        "cliffhanger": {
                            "timestamp": seconds_to_timestamp(p_end),
                            "description": "Timed pacing exit at scene cutoff",
                            "strength": 7.0,
                            "is_unresolved": True
                        },
                        "standalone_score": h["standalone_score"],
                        "one_line_why_it_works": f"Fixed {dur:.0f}s vertical ad window anchored on high-energy hook."
                    })
                    break
    return candidates

def compute_duration_suitability(duration: float) -> float:
    if 30.0 <= duration <= 45.0:
        return 10.0
    elif (25.0 <= duration < 30.0) or (45.0 < duration <= 50.0):
        return 8.5
    elif (20.0 <= duration < 25.0) or (50.0 < duration <= 60.0):
        return 7.0
    else:
        return 3.0

def calculate_promo_score(
    hook_strength: float,
    cliffhanger_strength: float,
    emotion_score: float,
    mystery_score: float,
    standalone_score: float,
    duration_score: float,
    weights: Dict[str, float] = None
) -> float:
    if weights is None:
        weights = {
            "hook": 0.25,
            "cliffhanger": 0.25,
            "emotion": 0.15,
            "mystery": 0.15,
            "standalone": 0.10,
            "duration": 0.10
        }
    score = (
        weights["hook"] * hook_strength +
        weights["cliffhanger"] * cliffhanger_strength +
        weights["emotion"] * emotion_score +
        weights["mystery"] * mystery_score +
        weights["standalone"] * standalone_score +
        weights["duration"] * duration_score
    )
    return round(score, 2)

def score_and_rank_candidates(
    candidates: List[Dict[str, Any]],
    scenes_lookup: Dict[str, List[Dict[str, Any]]],
    custom_weights: Dict[str, float] = None
) -> List[Dict[str, Any]]:
    scored_list = []
    for cand in candidates:
        ep_id = cand["episode_id"]
        ep_scenes = scenes_lookup.get(ep_id, [])
        cand_start = cand["start_s"]
        
        matched_scene = next(
            (s for s in ep_scenes if s["start_time"] <= cand_start <= s["end_time"]),
            ep_scenes[0] if ep_scenes else {}
        )
        ai_data = matched_scene.get("ai_analysis", {})
        
        H = float(cand["hook"]["strength"])
        C = float(cand["cliffhanger"]["strength"])
        E = float(ai_data.get("emotion_score", 7.0))
        mystery_val = ai_data.get("mystery_score", 7.0)
        surprise_val = ai_data.get("surprise_score", 7.0)
        M = round((mystery_val + surprise_val) / 2.0, 2)
        V = float(cand.get("standalone_score", ai_data.get("standalone_score", 7.5)))
        D = compute_duration_suitability(cand["duration"])
        
        final_score = calculate_promo_score(H, C, E, M, V, D, custom_weights)
        
        cand_scored = {
            **cand,
            "score_breakdown": {
                "H_hook": H,
                "C_cliffhanger": C,
                "E_emotion": E,
                "M_mystery_surprise": M,
                "V_standalone": V,
                "D_duration": D
            },
            "final_score": final_score
        }
        scored_list.append(cand_scored)
        
    scored_list.sort(key=lambda x: x["final_score"], reverse=True)
    for rank_idx, item in enumerate(scored_list, start=1):
        item["rank"] = rank_idx
        item["promo_id"] = f"PROMO_{rank_idx:03d}"
    return scored_list

def format_final_promos(ranked_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    final_outputs = []
    for item in ranked_candidates:
        promo_id = item.get("promo_id", f"PROMO_{item['rank']:03d}")
        segments = [
            {
                "start_time": s.get("start_ts", seconds_to_timestamp(s["start_s"])),
                "end_time": s.get("end_ts", seconds_to_timestamp(s["end_s"]))
            }
            for s in item.get("segments", [{
                "start_s": item["start_s"], 
                "end_s": item["end_s"],
                "start_ts": item["start_ts"],
                "end_ts": item["end_ts"]
            }])
        ]
        structured_record = {
            "rank": item["rank"],
            "promo_id": promo_id,
            "episode_id": item["episode_id"],
            "segments": segments,
            "duration_seconds": round(item["duration"], 1),
            "hook": item["hook"]["description"],
            "hook_timestamp": item["hook"]["timestamp"],
            "cliffhanger": item["cliffhanger"]["description"],
            "cliffhanger_timestamp": item["cliffhanger"]["timestamp"],
            "score": item["final_score"],
            "why_it_works": item["one_line_why_it_works"],
            "score_breakdown": item["score_breakdown"]
        }
        final_outputs.append(structured_record)
    return final_outputs

def calculate_time_overlap(p1_start: float, p1_end: float, p2_start: float, p2_end: float) -> float:
    inter_start = max(p1_start, p2_start)
    inter_end = min(p1_end, p2_end)
    inter = max(0.0, inter_end - inter_start)
    union = (p1_end - p1_start) + (p2_end - p2_start) - inter
    return inter / union if union > 0 else 0.0

def run_automated_quality_audit(
    promos: List[Dict[str, Any]], 
    iou_duplicate_threshold: float = 0.60
) -> Dict[str, Any]:
    audit_results = []
    failure_counts = {
        "INVALID_DURATION": 0,
        "INVALID_TIMESTAMPS": 0,
        "DELAYED_HOOK (>3s)": 0,
        "WEAK_HOOK (<6.0)": 0,
        "RESOLVED_OR_WEAK_CLIFFHANGER": 0,
        "HIGH_CONTEXT_DEPENDENCY": 0,
        "NEAR_DUPLICATE_OVERLAP": 0
    }
    accepted_by_episode = {}

    for p in promos:
        errors = []
        warnings = []
        ep_id = p["episode_id"]
        
        dur = p["duration_seconds"]
        if not (20.0 <= dur <= 60.0):
            errors.append(f"Invalid duration ({dur:.1f}s, outside 20-60s)")
            failure_counts["INVALID_DURATION"] += 1
            
        s_sec = timestamp_to_seconds(p["segments"][0]["start_time"])
        e_sec = timestamp_to_seconds(p["segments"][0]["end_time"])
        if s_sec >= e_sec:
            errors.append(f"Invalid timestamp interval ({s_sec} >= {e_sec})")
            failure_counts["INVALID_TIMESTAMPS"] += 1

        hook_sec = timestamp_to_seconds(p["hook_timestamp"])
        hook_delay = hook_sec - s_sec
        if hook_delay > 3.0:
            warnings.append(f"Delayed hook (occurs at +{hook_delay:.1f}s)")
            failure_counts["DELAYED_HOOK (>3s)"] += 1

        hook_score = p.get("score_breakdown", {}).get("H_hook", 7.0)
        if hook_score < 6.0:
            warnings.append(f"Weak hook score ({hook_score:.1f}/10)")
            failure_counts["WEAK_HOOK (<6.0)"] += 1

        cliff_score = p.get("score_breakdown", {}).get("C_cliffhanger", 7.0)
        if cliff_score < 6.0:
            warnings.append(f"Weak cliffhanger score ({cliff_score:.1f}/10)")
            failure_counts["RESOLVED_OR_WEAK_CLIFFHANGER"] += 1

        context_score = p.get("score_breakdown", {}).get("V_standalone", 7.5)
        if context_score < 6.0:
            warnings.append(f"High lore requirement (Standalone: {context_score:.1f}/10)")
            failure_counts["HIGH_CONTEXT_DEPENDENCY"] += 1

        is_duplicate = False
        if ep_id in accepted_by_episode:
            for prev_p in accepted_by_episode[ep_id]:
                prev_s = timestamp_to_seconds(prev_p["segments"][0]["start_time"])
                prev_e = timestamp_to_seconds(prev_p["segments"][0]["end_time"])
                overlap = calculate_time_overlap(s_sec, e_sec, prev_s, prev_e)
                if overlap > iou_duplicate_threshold:
                    is_duplicate = True
                    warnings.append(f"Near-duplicate of higher-ranked {prev_p['promo_id']} (Overlap: {overlap*100:.0f}%)")
                    failure_counts["NEAR_DUPLICATE_OVERLAP"] += 1
                    break

        if not is_duplicate:
            accepted_by_episode.setdefault(ep_id, []).append(p)

        audit_results.append({
            "Rank": p["rank"],
            "Promo ID": p["promo_id"],
            "Episode": p["episode_id"],
            "Duration (s)": dur,
            "Score": p["score"],
            "Status": "FAIL" if errors else ("WARNING" if warnings else "PASS"),
            "Issues": ", ".join(errors + warnings) if (errors + warnings) else "None"
        })

    return {
        "total_promos": len(promos),
        "passed_clean": sum(1 for r in audit_results if r["Status"] == "PASS"),
        "has_warnings": sum(1 for r in audit_results if r["Status"] == "WARNING"),
        "hard_failures": sum(1 for r in audit_results if r["Status"] == "FAIL"),
        "failure_counts": failure_counts,
        "details": audit_results
    }

def execute_complete_colab_pipeline(episodes_dict: Dict[str, str], custom_weights: Dict[str, float] = None):
    parsed_episodes = {ep_id: parse_screenplay_to_cues(ep_id, text) for ep_id, text in episodes_dict.items()}
    all_scenes = {ep_id: segment_episode_into_scenes(ep_id, cues) for ep_id, cues in parsed_episodes.items()}
    flat_scene_list = [sc for scs in all_scenes.values() for sc in scs]
    
    for sc in flat_scene_list:
        sc["ai_analysis"] = heuristic_semantic_analyzer(sc["scene_text"])
        sc["promo_moments"] = heuristic_hook_cliffhanger_detector(sc)
        
    all_candidates = []
    for ep_id, scenes in all_scenes.items():
        cues = parsed_episodes.get(ep_id, [])
        ep_candidates = generate_promo_candidates_for_episode(ep_id, scenes, cues)
        all_candidates.extend(ep_candidates)
        
    ranked_promos = score_and_rank_candidates(all_candidates, all_scenes, custom_weights)
    final_promos = format_final_promos(ranked_promos)
    audit_report = run_automated_quality_audit(final_promos)
    
    return final_promos, all_scenes, len(flat_scene_list), audit_report

# ==============================================================================
# STREAMLIT UI
# ==============================================================================

st.markdown("""
<div class="hero-header">
    <div class="hero-title">🎬 Kuku PromoAI Dashboard</div>
    <div class="hero-sub">Exact Stage 1–9 Colab Pipeline Engine: Ad extraction, hook/cliffhanger detection, and ranking.</div>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Stage 7 Formula Weights")
    w_hook = st.slider("Hook (H)", 0.0, 1.0, 0.25, 0.05)
    w_cliff = st.slider("Cliffhanger (C)", 0.0, 1.0, 0.25, 0.05)
    w_emo = st.slider("Emotion (E)", 0.0, 1.0, 0.15, 0.05)
    w_mys = st.slider("Mystery/Surprise (M)", 0.0, 1.0, 0.15, 0.05)
    w_stand = st.slider("Standalone (V)", 0.0, 1.0, 0.10, 0.05)
    w_dur = st.slider("Duration (D)", 0.0, 1.0, 0.10, 0.05)

    current_weights = {
        "hook": w_hook, "cliffhanger": w_cliff, "emotion": w_emo,
        "mystery": w_mys, "standalone": w_stand, "duration": w_dur
    }
    min_score_filter = st.slider("Filter: Min Final Score", 0.0, 10.0, 7.0, 0.1)

tab_run, tab_summary, tab_promos, tab_episodes, tab_audit, tab_export = st.tabs([
    "📥 Ingest & Run", "📊 Summary", "🏆 Ranked Promos", 
    "🔍 Episode Explorer", "🛡️ Quality Audit", "💾 Export Deliverables"
])

if "episodes_data" not in st.session_state:
    st.session_state.episodes_data = {}
if "promos_results" not in st.session_state:
    st.session_state.promos_results = []
if "all_scenes" not in st.session_state:
    st.session_state.all_scenes = {}
if "total_scenes_count" not in st.session_state:
    st.session_state.total_scenes_count = 0
if "audit_report" not in st.session_state:
    st.session_state.audit_report = {}

with tab_run:
    c_up, c_dem = st.columns([2, 1])
    with c_up:
        st.subheader("1. Upload Screenplays (Stage 1)")
        uploaded_zip = st.file_uploader("Upload ZIP pack containing 50 screenplay episodes", type=["zip"])
    with c_dem:
        st.subheader("Demo Fallback")
        if st.button("Load 3 Demo Episodes", use_container_width=True):
            st.session_state.episodes_data = {
                "EP001": """[00:00 - 00:03] INT. LUXURY PENTHOUSE - NIGHT\n[00:03 - 00:06] ELENA: You signed the divorce papers without telling me?!\n[00:07 - 00:11] MARK: (Smirks, throws glass) You never deserved the family billions.\n[00:12 - 00:20] ELENA: (SLAPS MARK across the face) I built this empire with my father!\n[00:21 - 00:35] MARK: Look at security outside. You are leaving with nothing.\n[00:36 - 00:48] ELENA: (Whispers into phone) Release the offshore ledger. Destroy him.\n[00:49 - 00:52] MARK: (Freezes in terror) Who are you talking to?!""",
                "EP002": """[00:00 - 00:04] EXT. RAINY STREET - CONTINUOUS\n[00:04 - 00:08] POLICE SIREN blares. Three black SUVs screech to a halt.\n[00:09 - 00:17] DETECTIVE: Elena Vance! Step away from the vehicle with your hands up!\n[00:18 - 00:28] ELENA: Check the trunk before you arrest me. Look who is inside.\n[00:29 - 00:42] DETECTIVE: (Opens trunk, gasps in shock) It's... the Chairman?!\n[00:43 - 00:50] CHAIRMAN: (Gasping) She didn't kidnap me... he did!""",
                "EP003": """[00:00 - 00:05] INT. BOARDROOM - DAY\n[00:05 - 00:10] MARK: As interim CEO, I call for the immediate liquidation of Vance Assets.\n[00:11 - 00:22] BOARD MEMBER: Elena owns fifty-one percent of common voting stock.\n[00:23 - 00:38] MARK: Not anymore. I had the power of attorney transferred this morning.\n[00:39 - 00:52] Elena kicks the double doors open. Armed guards flank behind her.\n[00:53 - 00:58] ELENA: Check the notary signature again, Mark. You just signed your own confession."""
            }
            st.success("Loaded 3 demo episodes successfully!")

    if uploaded_zip is not None:
        try:
            with zipfile.ZipFile(uploaded_zip, 'r') as z:
                ep_dict = {}
                for idx, fname in enumerate(sorted(z.namelist()), 1):
                    if fname.endswith(('.txt', '.md', '.json')) and not fname.startswith(('__MACOSX', '.')):
                        content = z.read(fname).decode('utf-8', errors='ignore')
                        if content.strip():
                            ep_id = extract_episode_id(fname, fallback_idx=idx)
                            ep_dict[ep_id] = content
                st.session_state.episodes_data = dict(sorted(ep_dict.items()))
                st.success(f"Successfully loaded {len(st.session_state.episodes_data)} episodes from ZIP file.")
        except Exception as e:
            st.error(f"Error reading ZIP: {e}")

    st.markdown("---")
    st.subheader("2. Run Pipeline (Stages 2 -> 9)")
    if st.session_state.episodes_data:
        st.info(f"Loaded {len(st.session_state.episodes_data)} episodes ready for analysis.")
        if st.button("🚀 Execute Full Pipeline", type="primary", use_container_width=True):
            with st.spinner("Executing Stages 2 to 9 identically to Colab..."):
                final_promos, all_scenes, total_scenes, audit_report = execute_complete_colab_pipeline(
                    st.session_state.episodes_data, current_weights
                )
                st.session_state.promos_results = final_promos
                st.session_state.all_scenes = all_scenes
                st.session_state.total_scenes_count = total_scenes
                st.session_state.audit_report = audit_report
            st.success(f"Pipeline complete! Extracted {len(final_promos)} ranked promos across {total_scenes} scenes.")
    else:
        st.warning("Upload a ZIP file or load demo episodes above to begin.")

has_results = len(st.session_state.promos_results) > 0

with tab_summary:
    if has_results:
        promos = st.session_state.promos_results
        top_score = promos[0]["score"] if promos else 0.0
        avg_score = sum(p["score"] for p in promos) / max(len(promos), 1)
        
        st.subheader("📊 Executive Metrics")
        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Episodes</div><div class="metric-val">{len(st.session_state.episodes_data)}</div></div>', unsafe_allow_html=True)
        with m2:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Scenes Analyzed</div><div class="metric-val">{st.session_state.total_scenes_count}</div></div>', unsafe_allow_html=True)
        with m3:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Promos Found</div><div class="metric-val">{len(promos)}</div></div>', unsafe_allow_html=True)
        with m4:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Top Score</div><div class="metric-val" style="color:#FF4B4B;">{top_score:.2f}</div></div>', unsafe_allow_html=True)
        with m5:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Avg Score</div><div class="metric-val" style="color:#38bdf8;">{avg_score:.2f}</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("Duration Distribution (20s - 60s)")
        dur_df = pd.DataFrame([{"Duration (s)": p["duration_seconds"], "Score": p["score"]} for p in promos])
        st.bar_chart(dur_df.set_index("Duration (s)"))
    else:
        st.info("Run the pipeline in the first tab to view summary metrics.")

with tab_promos:
    if has_results:
        filtered = [p for p in st.session_state.promos_results if p["score"] >= min_score_filter]
        st.subheader(f"🏆 Ranked Promos ({len(filtered)} matching score >= {min_score_filter})")
        for p in filtered:
            st.markdown(f"""
            <div class="promo-card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <div>
                        <span style="font-size:1.2rem; font-weight:800; color:#fff;">#{p['rank']} · {p['promo_id']}</span>
                        <span style="margin-left:12px; font-weight:600; color:#8C92A4;">Episode {p['episode_id']}</span>
                    </div>
                    <div>
                        <span class="badge-dur">⏱️ {p['duration_seconds']}s ({p['segments'][0]['start_time']} - {p['segments'][0]['end_time']})</span>
                        <span class="badge-score" style="margin-left:8px;">★ Score {p['score']}</span>
                    </div>
                </div>
                <div style="font-size:0.95rem; margin-bottom:6px;">
                    <strong style="color:#FF7B7B;">🪝 Hook ({p['hook_timestamp']}):</strong> <span style="color:#E2E8F0;">{p['hook']}</span>
                </div>
                <div style="font-size:0.95rem; margin-bottom:6px;">
                    <strong style="color:#38bdf8;">⚡ Cliffhanger ({p['cliffhanger_timestamp']}):</strong> <span style="color:#E2E8F0;">{p['cliffhanger']}</span>
                </div>
                <div style="font-size:0.88rem; color:#94A3B8; background:rgba(255,255,255,0.03); padding:8px 12px; border-radius:8px; margin-top:8px;">
                    💡 <em>Why It Works:</em> {p['why_it_works']}
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("Run the pipeline in the first tab to view ranked promos.")

with tab_episodes:
    if has_results:
        ep_list = sorted(list(st.session_state.all_scenes.keys()))
        selected_ep = st.selectbox("Select Episode to Inspect:", ep_list)
        
        scenes_for_ep = st.session_state.all_scenes.get(selected_ep, [])
        promos_for_ep = [p for p in st.session_state.promos_results if p["episode_id"] == selected_ep]
        
        c_sc, c_pr = st.columns([1, 1])
        with c_sc:
            st.markdown(f"#### 🎬 Scenes in {selected_ep} ({len(scenes_for_ep)})")
            for sc in scenes_for_ep:
                with st.expander(f"Scene {sc['scene_id']} ({sc['start_ts']} - {sc['end_ts']} · {sc['duration']}s)"):
                    st.text(sc["scene_text"])
                    st.caption(f"Hook: {sc['promo_moments']['hook']['hook_description']}")
                    st.caption(f"Cliffhanger: {sc['promo_moments']['cliffhanger']['cliffhanger_description']}")
        with c_pr:
            st.markdown(f"#### 🎯 Promos in {selected_ep} ({len(promos_for_ep)})")
            for pr in promos_for_ep:
                st.info(f"**{pr['promo_id']} (Rank #{pr['rank']})** · Score: `{pr['score']}`\n\nWindow: `{pr['segments'][0]['start_time']} - {pr['segments'][0]['end_time']}` ({pr['duration_seconds']}s)\n\n_{pr['why_it_works']}_")
    else:
        st.info("Execute pipeline to inspect episode scenes.")

with tab_audit:
    if has_results:
        audit = st.session_state.audit_report
        st.subheader("🛡️ Stage 9 Quality Evaluation Report")
        
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Clean Pass", audit["passed_clean"])
        q2.metric("With Warnings", audit["has_warnings"])
        q3.metric("Hard Failures", audit["hard_failures"])
        q4.metric("Delayed Hooks (>3s)", audit["failure_counts"]["DELAYED_HOOK (>3s)"])
        
        st.markdown("#### Diagnostic Issue Log")
        st.dataframe(pd.DataFrame(audit["details"]), use_container_width=True)
    else:
        st.info("Execute pipeline to inspect quality diagnostics.")

with tab_export:
    if has_results:
        st.subheader("💾 Export Deliverables")
        st.markdown("Download unedited deliverables identical to your Colab outputs:")
        
        df_export = pd.DataFrame(st.session_state.promos_results)
        
        csv_buffer = io.StringIO()
        df_export.to_csv(csv_buffer, index=False)
        st.download_button(
            "📥 Download promos_ranked.csv",
            csv_buffer.getvalue(),
            "promos_ranked.csv",
            "text/csv",
            use_container_width=True
        )
        
        json_str = json.dumps(st.session_state.promos_results, indent=2)
        st.download_button(
            "📥 Download promo_results.json",
            json_str,
            "promo_results.json",
            "application/json",
            use_container_width=True
        )
    else:
        st.info("Run the pipeline first to generate download files.")
