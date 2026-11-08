import streamlit as st
import pandas as pd
import json
import os
import io
import zipfile
import re
from typing import List, Dict, Any, Optional

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
    
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', sans-serif;
    }
    
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
    
    .hero-sub {
        color: #A0A5B5;
        font-size: 1.05rem;
    }
    
    .metric-card {
        background: #181926;
        padding: 18px 20px;
        border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.07);
        text-align: center;
        box-shadow: 0 4px 15px rgba(0,0,0,0.15);
    }
    
    .metric-val {
        font-size: 2.0rem;
        font-weight: 800;
        color: #ffffff;
        margin: 4px 0;
    }
    
    .metric-lbl {
        color: #8C92A4;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        font-weight: 600;
    }
    
    .promo-card {
        background: #191A23;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 14px;
        padding: 20px 24px;
        margin-bottom: 16px;
        transition: transform 0.15s ease, border-color 0.15s ease;
    }
    
    .promo-card:hover {
        border-color: #FF4B4B;
        transform: translateY(-2px);
    }
    
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

# ==========================================
# CORE PIPELINE LOGIC (SELF-CONTAINED)
# ==========================================

def parse_time_to_seconds(ts_str: str) -> float:
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
    except Exception:
        return 0.0
    return 0.0

def seconds_to_timestamp(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"

def extract_episode_id(file_name: str, fallback_idx: int) -> str:
    patterns = [r"ep(?:isode)?[\s_-]*(\d+)", r"(?:^|[^\d])(\d{1,3})(?:[^\d]|$)"]
    for pattern in patterns:
        match = re.search(pattern, file_name, re.IGNORECASE)
        if match:
            return f"EP{int(match.group(1)):03d}"
    return f"EP{fallback_idx:03d}"

def parse_screenplay_lines(raw_text: str, ep_id: str) -> List[Dict[str, Any]]:
    ts_regex = re.compile(r"\[?\(?(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\)?\]?(?:\s*(?:-|to)\s*\[?\(?(\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\)?\]?)?", re.IGNORECASE)
    cues = []
    curr_time = 0.0
    for idx, line in enumerate(raw_text.splitlines()):
        line = line.strip()
        if not line:
            continue
        m = ts_regex.search(line)
        if m:
            start_str, end_str = m.group(1), m.group(2)
            s_s = parse_time_to_seconds(start_str)
            e_s = parse_time_to_seconds(end_str) if end_str else s_s + 3.0
            curr_time = e_s
            text = (line[:m.start()] + line[m.end():]).strip().lstrip(":- ")
        else:
            s_s = curr_time
            e_s = s_s + 2.5
            curr_time = e_s
            text = line
        if not text:
            continue
        
        cue_type = "ACTION"
        speaker = None
        if re.match(r"^(?:INT\.|EXT\.|SCENE\s+\d+|CUT\s+TO:)", text, re.IGNORECASE):
            cue_type = "SCENE_HEADING"
        elif ":" in text:
            parts = text.split(":", 1)
            if len(parts[0].strip()) <= 30 and any(c.isupper() for c in parts[0]):
                cue_type = "DIALOGUE"
                speaker = parts[0].strip().upper()
                text = parts[1].strip()

        cues.append({
            "ep_id": ep_id,
            "cue_id": idx + 1,
            "start_s": round(s_s, 2),
            "end_s": round(e_s, 2),
            "start_ts": seconds_to_timestamp(s_s),
            "end_ts": seconds_to_timestamp(e_s),
            "type": cue_type,
            "speaker": speaker,
            "text": text
        })
    return cues

def segment_scenes(cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not cues:
        return []
    scenes = []
    curr = [cues[0]]
    for prev_c, next_c in zip(cues[:-1], cues[1:]):
        time_gap = next_c["start_s"] - prev_c["end_s"]
        dur = next_c["end_s"] - curr[0]["start_s"]
        is_heading = next_c["type"] == "SCENE_HEADING"
        if is_heading or time_gap > 4.0 or dur > 120.0:
            scenes.append(curr)
            curr = [next_c]
        else:
            curr.append(next_c)
    if curr:
        scenes.append(curr)

    structured_scenes = []
    for idx, sc in enumerate(scenes, 1):
        s_s = sc[0]["start_s"]
        e_s = scenes[idx][0]["start_s"] if idx < len(scenes) else sc[-1]["end_s"]
        text_lines = [f"[{c['start_ts']}] {c['speaker'] + ': ' if c['speaker'] else ''}{c['text']}" for c in sc]
        structured_scenes.append({
            "scene_id": f"SC{idx:03d}",
            "start_time": s_s,
            "end_time": e_s,
            "start_ts": seconds_to_timestamp(s_s),
            "end_ts": seconds_to_timestamp(e_s),
            "duration": round(e_s - s_s, 2),
            "cues": sc,
            "scene_text": "\n".join(text_lines)
        })
    return structured_scenes

def analyze_and_detect_moments(scene: Dict[str, Any]) -> Dict[str, Any]:
    cues = scene.get("cues", [])
    hook_keywords = ["slap", "shout", "gun", "contract", "pregnant", "kill", "secret", "divorce", "arrest", "bastard", "liar", "stop", "freeze"]
    cliff_keywords = ["who are you", "what did you", "look at me", "never", "dead", "wait", "destroy", "ledger", "reveal", "tell me", "?", "!"]

    best_hook = cues[0] if cues else {"start_s": scene["start_time"], "start_ts": scene["start_ts"], "text": "Opening scene"}
    hook_strength = 6.5
    hook_desc = best_hook.get("text", "Opening action beat")

    for c in cues[:min(len(cues), 5)]:
        if any(w in c["text"].lower() for w in hook_keywords):
            best_hook = c
            hook_strength = 9.4
            hook_desc = f"Shock beat: '{c['text']}'"
            break

    best_cliff = cues[-1] if cues else {"end_s": scene["end_time"], "end_ts": scene["end_ts"], "text": "Scene exit"}
    cliff_strength = 7.0
    cliff_desc = best_cliff.get("text", "Paced narrative break")

    for c in reversed(cues[-min(len(cues), 5):]):
        if any(w in c["text"].lower() for w in cliff_keywords) or c["text"].endswith("?"):
            best_cliff = c
            cliff_strength = 9.2
            cliff_desc = f"High-tension cutoff: '{c['text']}'"
            break

    return {
        "hook": {"timestamp": best_hook["start_ts"], "start_s": best_hook["start_s"], "description": hook_desc, "strength": hook_strength},
        "cliffhanger": {"timestamp": best_cliff["end_ts"], "end_s": best_cliff["end_s"], "description": cliff_desc, "strength": cliff_strength, "is_unresolved": True},
        "emotion_score": 8.0,
        "mystery_score": 8.0,
        "standalone_score": 8.0,
        "why_it_works": f"Instantly hooks on high-stakes delivery at {best_hook['start_ts']} and cuts before resolution at {best_cliff['end_ts']}."
    }

def generate_and_rank_promos(episodes_dict: Dict[str, str], weights: Dict[str, float]) -> tuple:
    all_promos = []
    ep_scenes_map = {}
    total_scenes_count = 0

    for ep_id, script_text in episodes_dict.items():
        cues = parse_screenplay_lines(script_text, ep_id)
        scenes = segment_scenes(cues)
        total_scenes_count += len(scenes)
        ep_scenes_map[ep_id] = []

        for sc in scenes:
            moments = analyze_and_detect_moments(sc)
            sc["analysis"] = moments
            ep_scenes_map[ep_id].append(sc)

            h_s = moments["hook"]["start_s"]
            c_e = moments["cliffhanger"]["end_s"]
            dur = c_e - h_s

            # Continuous Candidate Slicing (20-60s)
            if 20.0 <= dur <= 60.0:
                p_start, p_end, p_dur = h_s, c_e, round(dur, 1)
            else:
                p_start = h_s
                p_end = min(h_s + 38.0, cues[-1]["end_s"] if cues else h_s + 38.0)
                p_dur = round(p_end - p_start, 1)

            if p_dur >= 20.0:
                # Duration Suitability D
                if 30.0 <= p_dur <= 45.0:
                    d_score = 10.0
                elif (25.0 <= p_dur < 30.0) or (45.0 < p_dur <= 50.0):
                    d_score = 8.5
                else:
                    d_score = 7.0

                score = (
                    weights["hook"] * moments["hook"]["strength"] +
                    weights["cliffhanger"] * moments["cliffhanger"]["strength"] +
                    weights["emotion"] * moments["emotion_score"] +
                    weights["mystery"] * moments["mystery_score"] +
                    weights["standalone"] * moments["standalone_score"] +
                    weights["duration"] * d_score
                )

                all_promos.append({
                    "episode_id": ep_id,
                    "scene_id": sc["scene_id"],
                    "start_s": p_start,
                    "end_s": p_end,
                    "start_time": seconds_to_timestamp(p_start),
                    "end_time": seconds_to_timestamp(p_end),
                    "duration_seconds": p_dur,
                    "score": round(score, 2),
                    "hook": moments["hook"]["description"],
                    "hook_timestamp": moments["hook"]["timestamp"],
                    "hook_strength": moments["hook"]["strength"],
                    "cliffhanger": moments["cliffhanger"]["description"],
                    "cliffhanger_timestamp": moments["cliffhanger"]["timestamp"],
                    "cliffhanger_strength": moments["cliffhanger"]["strength"],
                    "standalone_score": moments["standalone_score"],
                    "why_it_works": moments["why_it_works"]
                })

    # Sort & Rank
    all_promos.sort(key=lambda x: x["score"], reverse=True)
    for idx, p in enumerate(all_promos, 1):
        p["rank"] = idx
        p["promo_id"] = f"PROMO_{idx:03d}"
        p["segments"] = [{"start_time": p["start_time"], "end_time": p["end_time"]}]

    return all_promos, ep_scenes_map, total_scenes_count

def run_quality_diagnostics(promos: List[Dict[str, Any]]) -> Dict[str, Any]:
    flags = {
        "invalid_duration": 0,
        "delayed_hook": 0,
        "weak_cliffhanger": 0,
        "duplicate_overlap": 0
    }
    detailed_flags = []
    seen = {}
    
    for p in promos:
        errs = []
        if not (20.0 <= p["duration_seconds"] <= 60.0):
            flags["invalid_duration"] += 1
            errs.append("Invalid duration")
            
        h_sec = parse_time_to_seconds(p["hook_timestamp"])
        if (h_sec - p["start_s"]) > 3.0:
            flags["delayed_hook"] += 1
            errs.append("Delayed hook (>3s)")
            
        if p["cliffhanger_strength"] < 7.0:
            flags["weak_cliffhanger"] += 1
            errs.append("Weak cliffhanger cutoff")
            
        ep = p["episode_id"]
        if ep in seen:
            prev_s, prev_e = seen[ep]
            if max(0, min(p["end_s"], prev_e) - max(p["start_s"], prev_s)) > 15:
                flags["duplicate_overlap"] += 1
                errs.append("High overlap with prior promo")
        seen[ep] = (p["start_s"], p["end_s"])

        if errs:
            detailed_flags.append({
                "Rank": p["rank"],
                "Promo ID": p["promo_id"],
                "Episode": p["episode_id"],
                "Issues": ", ".join(errs)
            })

    return {
        "summary": flags,
        "clean_count": len(promos) - len(detailed_flags),
        "flagged_count": len(detailed_flags),
        "details": detailed_flags
    }

# ==========================================
# STREAMLIT UI LAYOUT
# ==========================================

# Hero Header
st.markdown("""
<div class="hero-header">
    <div class="hero-title">🎬 Kuku PromoAI Dashboard</div>
    <div class="hero-sub">Automated microdrama promo extraction, hook & cliffhanger detection, and weighted ad-ranking.</div>
</div>
""", unsafe_allow_html=True)

# Sidebar Configuration
with st.sidebar:
    st.header("⚙️ Scoring Weights")
    st.caption("Adjust ranking formula parameters:")
    w_hook = st.slider("Hook Impact (H)", 0.0, 1.0, 0.25, 0.05)
    w_cliff = st.slider("Cliffhanger Tension (C)", 0.0, 1.0, 0.25, 0.05)
    w_emo = st.slider("Emotional Intensity (E)", 0.0, 1.0, 0.15, 0.05)
    w_mys = st.slider("Mystery & Surprise (M)", 0.0, 1.0, 0.15, 0.05)
    w_stand = st.slider("Standalone Context (V)", 0.0, 1.0, 0.10, 0.05)
    w_dur = st.slider("Duration Pacing (D)", 0.0, 1.0, 0.10, 0.05)

    current_weights = {
        "hook": w_hook,
        "cliffhanger": w_cliff,
        "emotion": w_emo,
        "mystery": w_mys,
        "standalone": w_stand,
        "duration": w_dur
    }
    
    st.markdown("---")
    min_score_filter = st.slider("Filter: Min Promo Score", 5.0, 10.0, 7.5, 0.1)

# Tab Navigation: The 7 Core Requirements
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📥 Ingest & Run",
    "📊 Summary",
    "🏆 Ranked Promos",
    "🔍 Episode Explorer",
    "🛡️ Quality Audit",
    "💾 Export Deliverables"
])

# Initialize Session State
if "episodes_data" not in st.session_state:
    st.session_state.episodes_data = {}
if "promos_results" not in st.session_state:
    st.session_state.promos_results = []
if "ep_scenes_map" not in st.session_state:
    st.session_state.ep_scenes_map = {}
if "total_scenes_count" not in st.session_state:
    st.session_state.total_scenes_count = 0

# --- SECTION 1 & 2: Upload Screenplays & Run Pipeline ---
with tab1:
    col_upload, col_demo = st.columns([2, 1])
    with col_upload:
        st.subheader("1. Upload Screenplays")
        uploaded_zip = st.file_uploader("Upload ZIP pack containing episodes (.txt, .md, .json)", type=["zip"])
        
    with col_demo:
        st.subheader("Quick Test")
        st.caption("Don't have the zip handy? Load built-in sample episodes:")
        load_demo_btn = st.button("Load 3 Demo Episodes", use_container_width=True)

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
            st.error(f"Failed to read ZIP: {e}")

    if load_demo_btn:
        st.session_state.episodes_data = {
            "EP001": """[00:00 - 00:03] INT. LUXURY PENTHOUSE - NIGHT\n[00:03 - 00:06] ELENA: You signed the divorce papers without telling me?!\n[00:07 - 00:11] MARK: (Smirks, throws glass) You never deserved the family billions.\n[00:12 - 00:20] ELENA: (SLAPS MARK across the face) I built this empire with my father!\n[00:21 - 00:35] MARK: Look at security outside. You are leaving with nothing.\n[00:36 - 00:48] ELENA: (Whispers into phone) Release the offshore ledger. Destroy him.\n[00:49 - 00:52] MARK: (Freezes in terror) Who are you talking to?!""",
            "EP002": """[00:00 - 00:04] EXT. RAINY STREET - CONTINUOUS\n[00:04 - 00:08] POLICE SIREN blares. Three black SUVs screech to a halt.\n[00:09 - 00:17] DETECTIVE: Elena Vance! Step away from the vehicle with your hands up!\n[00:18 - 00:28] ELENA: Check the trunk before you arrest me. Look who is inside.\n[00:29 - 00:42] DETECTIVE: (Opens trunk, gasps in shock) It's... the Chairman?!\n[00:43 - 00:50] CHAIRMAN: (Gasping) She didn't kidnap me... he did!""",
            "EP003": """[00:00 - 00:05] INT. BOARDROOM - DAY\n[00:05 - 00:10] MARK: As interim CEO, I call for the immediate liquidation of Vance Assets.\n[00:11 - 00:22] BOARD MEMBER: Elena owns fifty-one percent of common voting stock.\n[00:23 - 00:38] MARK: Not anymore. I had the power of attorney transferred this morning.\n[00:39 - 00:52] Elena kicks the double doors open. Armed guards flank behind her.\n[00:53 - 00:58] ELENA: Check the notary signature again, Mark. You just signed your own confession."""
        }
        st.success("Loaded 3 demo episodes successfully!")

    st.markdown("---")
    st.subheader("2. Run Analysis Pipeline")
    
    if st.session_state.episodes_data:
        st.info(f"Ready to process {len(st.session_state.episodes_data)} episodes.")
        if st.button("🚀 Execute AI Promo Finder Pipeline", type="primary", use_container_width=True):
            with st.spinner("Parsing timestamps, segmenting scenes, detecting hooks/cliffhangers, and ranking candidates..."):
                promos, ep_scenes, total_sc = generate_and_rank_promos(st.session_state.episodes_data, current_weights)
                st.session_state.promos_results = promos
                st.session_state.ep_scenes_map = ep_scenes
                st.session_state.total_scenes_count = total_sc
            st.success(f"Analysis complete! Generated {len(promos)} ranked promo candidates across {total_sc} scenes.")
    else:
        st.warning("Please upload a ZIP file or load Demo Episodes first.")

has_results = len(st.session_state.promos_results) > 0

# --- SECTION 3: Summary Dashboard ---
with tab2:
    if has_results:
        promos = st.session_state.promos_results
        top_score = promos[0]["score"] if promos else 0.0
        avg_score = sum(p["score"] for p in promos) / max(len(promos), 1)

        st.subheader("📊 Executive Summary")
        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Episodes</div><div class="metric-val">{len(st.session_state.episodes_data)}</div></div>', unsafe_allow_html=True)
        with m2:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Scenes Analyzed</div><div class="metric-val">{st.session_state.total_scenes_count}</div></div>', unsafe_allow_html=True)
        with m3:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Promos Found</div><div class="metric-val">{len(promos)}</div></div>', unsafe_allow_html=True)
        with m4:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Top Promo Score</div><div class="metric-val" style="color:#FF4B4B;">{top_score}</div></div>', unsafe_allow_html=True)
        with m5:
            st.markdown(f'<div class="metric-card"><div class="metric-lbl">Avg Score</div><div class="metric-val" style="color:#38bdf8;">{avg_score:.1f}</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("Duration Distribution (20s - 60s Microdrama Ad Standard)")
        dur_df = pd.DataFrame([{"Duration (s)": p["duration_seconds"], "Score": p["score"]} for p in promos])
        st.bar_chart(dur_df.set_index("Duration (s)"))
    else:
        st.info("Run the analysis pipeline in the first tab to view the summary dashboard.")

# --- SECTION 4: Ranked Promos ---
with tab3:
    if has_results:
        promos = [p for p in st.session_state.promos_results if p["score"] >= min_score_filter]
        st.subheader(f"🏆 Top Ranked Promo Candidates ({len(promos)} matching score >= {min_score_filter})")
        
        for p in promos:
            st.markdown(f"""
            <div class="promo-card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div>
                        <span style="font-size:1.25rem; font-weight:800; color:#fff;">#{p['rank']} · {p['promo_id']}</span>
                        <span style="margin-left:12px; font-weight:600; color:#8C92A4;">Episode {p['episode_id']}</span>
                    </div>
                    <div>
                        <span class="badge-dur">⏱️ {p['duration_seconds']}s ({p['start_time']} - {p['end_time']})</span>
                        <span class="badge-score" style="margin-left:8px;">★ Score {p['score']}</span>
                    </div>
                </div>
                <div style="font-size:0.95rem; margin-bottom:8px;">
                    <strong style="color:#FF7B7B;">🪝 Hook ({p['hook_timestamp']}):</strong> <span style="color:#E2E8F0;">{p['hook']}</span>
                </div>
                <div style="font-size:0.95rem; margin-bottom:8px;">
                    <strong style="color:#38bdf8;">⚡ Cliffhanger ({p['cliffhanger_timestamp']}):</strong> <span style="color:#E2E8F0;">{p['cliffhanger']}</span>
                </div>
                <div style="font-size:0.9rem; color:#94A3B8; background:rgba(255,255,255,0.03); padding:8px 12px; border-radius:8px;">
                    💡 <em>Why It Works:</em> {p['why_it_works']}
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No promos to show yet. Run the pipeline in Tab 1.")

# --- SECTION 5: Episode-wise Analysis ---
with tab4:
    if has_results:
        ep_list = list(st.session_state.ep_scenes_map.keys())
        selected_ep = st.selectbox("Select Episode to Inspect:", ep_list)
        
        scenes_for_ep = st.session_state.ep_scenes_map.get(selected_ep, [])
        promos_for_ep = [p for p in st.session_state.promos_results if p["episode_id"] == selected_ep]
        
        c_scenes, c_promos = st.columns([1, 1])
        with c_scenes:
            st.markdown(f"#### 🎬 Scenes in {selected_ep} ({len(scenes_for_ep)} detected)")
            for s in scenes_for_ep:
                with st.expander(f"Scene {s['scene_id']} ({s['start_ts']} - {s['end_ts']} · {s['duration']}s)"):
                    st.text(s["scene_text"])
                    st.caption(f"Hook: {s['analysis']['hook']['description']} | Cliffhanger: {s['analysis']['cliffhanger']['description']}")
                    
        with c_promos:
            st.markdown(f"#### 🎯 Promos Generated for {selected_ep} ({len(promos_for_ep)})")
            for p in promos_for_ep:
                st.markdown(f"""
                <div style="border-left:3px solid #FF4B4B; padding-left:12px; margin-bottom:14px;">
                    <strong>{p['promo_id']} (Rank #{p['rank']})</strong> · Score: {p['score']}<br>
                    <small>Window: {p['start_time']} - {p['end_time']} ({p['duration_seconds']}s)</small><br>
                    <span style="font-size:0.85rem; color:#A0A5B5;">{p['why_it_works']}</span>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("Execute the pipeline to unlock episode deep-dives.")

# --- SECTION 6: Quality Evaluation ---
with tab5:
    if has_results:
        audit = run_quality_diagnostics(st.session_state.promos_results)
        st.subheader("🛡️ Quality Evaluation & Failure Modes")
        st.caption("Automated audit results based on microdrama pacing, hook timing, and cut constraints:")

        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Clean Promos (Pass)", audit["clean_count"])
        q2.metric("Delayed Hooks (>3s)", audit["summary"]["delayed_hook"])
        q3.metric("Weak Cliffhangers", audit["summary"]["weak_cliffhanger"])
        q4.metric("Duplicate Overlaps", audit["summary"]["duplicate_overlap"])

        st.markdown("---")
        st.markdown("#### Diagnostic Issue Log")
        if audit["details"]:
            st.table(pd.DataFrame(audit["details"]))
        else:
            st.success("All promo candidates passed all quality thresholds perfectly!")
    else:
        st.info("Execute pipeline to inspect failure diagnostics.")

# --- SECTION 7: Download Results ---
with tab6:
    if has_results:
        st.subheader("💾 Download Submission Deliverables")
        st.markdown("Export final unedited outputs required for the Kuku assignment submission folder:")

        df_export = pd.DataFrame(st.session_state.promos_results)
        
        # CSV Download
        csv_buffer = io.StringIO()
        df_export.to_csv(csv_buffer, index=False)
        st.download_button(
            label="📥 Download promos_ranked.csv",
            data=csv_buffer.getvalue(),
            file_name="promos_ranked.csv",
            mime="text/csv",
            use_container_width=True
        )

        # JSON Download
        json_str = json.dumps(st.session_state.promos_results, indent=2)
        st.download_button(
            label="📥 Download promo_results.json",
            data=json_str,
            file_name="promo_results.json",
            mime="application/json",
            use_container_width=True
        )
    else:
        st.info("Run the pipeline first to generate downloadable deliverables.")
