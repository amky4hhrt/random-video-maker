import os
import json
import time
import requests
from pathlib import Path
from google import genai
from google.genai import types

AI_PROVIDER = "together" # Options: "gemini", "together"
TOGETHER_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
# Other Together options: 
# - "deepseek-ai/DeepSeek-V4-Pro"
# - "meta-llama/Llama-3.3-70B-Instruct-Turbo"

CHUNK_DURATION_SECONDS = 180  # 3 minutes per chunk for Director Pass

# Assuming the user will set GEMINI_API_KEY in environment before running
def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("\u274C Error: GEMINI_API_KEY environment variable not set.")
    return genai.Client(api_key=api_key)

def _call_gemini_with_retry(client, system_instruction, user_content, response_schema, max_retries=5):
    current_sleep = 5
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    max_output_tokens=65536,
                )
            )
            if response and response.text:
                return json.loads(response.text)
            else:
                raise ValueError("Empty response")
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            print(f"  \u26A0\uFE0F Attempt {attempt + 1} failed. Retrying in {current_sleep}s... ({e})")
            time.sleep(current_sleep)
            current_sleep *= 2

def _schema_to_json_schema(s):
    res = {}
    if hasattr(s, 'type') and s.type:
        t = s.type
        if hasattr(t, 'name'):
            res['type'] = t.name.lower()
        else:
            res['type'] = str(t).lower().replace('type.', '')
    if hasattr(s, 'properties') and s.properties:
        res['properties'] = {k: _schema_to_json_schema(v) for k, v in s.properties.items()}
    if hasattr(s, 'items') and s.items:
        res['items'] = _schema_to_json_schema(s.items)
    if hasattr(s, 'required') and s.required:
        res['required'] = s.required
    if hasattr(s, 'enum') and s.enum:
        res['enum'] = s.enum
    return res

def _call_together_with_retry(system_instruction, user_content, response_schema, max_retries=5):
    api_key = os.environ.get("TOGETHER_API_KEY")
    if not api_key:
        raise ValueError("\u274C Error: TOGETHER_API_KEY environment variable not set.")
        
    json_schema = _schema_to_json_schema(response_schema)
    sys_prompt = system_instruction + """
    
CRITICAL INSTRUCTION:
You MUST respond ONLY with a valid JSON data instance of the following JSON Schema. 
Do NOT include schema definition fields like 'type: object' or 'properties' in your output. 
Just output the raw JSON data that satisfies the schema structure.

JSON SCHEMA:
""" + json.dumps(json_schema, indent=2)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": TOGETHER_MODEL,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_content}
        ],
        "response_format": {
            "type": "json_object",
            "schema": json_schema
        },
        "temperature": 0.5,
        "max_tokens": 8000
    }

    current_sleep = 5
    for attempt in range(max_retries):
        try:
            resp = requests.post("https://api.together.xyz/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            content = data["choices"][0]["message"]["content"]
            # Clean up potential markdown formatting around json
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
                
            try:
                return json.loads(content.strip())
            except json.JSONDecodeError as je:
                print(f"  \u26A0\uFE0F JSON Decode Error: {je}")
                print(f"  --- RAW OUTPUT START ---\n{content[:500]}...\n...{content[-500:]}\n  --- RAW OUTPUT END ---")
                raise je
                
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            print(f"  \u26A0\uFE0F Attempt {attempt + 1} failed. Retrying in {current_sleep}s... ({e})")
            time.sleep(current_sleep)
            current_sleep *= 2

def run_character_pass(story_text, output_path):
    print("  \U0001F3AD Running Character & Location Pass...")
    client = get_gemini_client()
    
    sys_inst = """You are a Casting Director, Character Designer, and Location Scout for 2D animated short films.
Identify every RECURRING character AND RECURRING location.
For each character, write a single reusable 'trait_tags' description. Handle age_stages if applicable.
For each location, write a single reusable 'trait_tags' description. Handle variants if applicable.
Output a shared negative_prompt for the whole film. KEEP THE negative_prompt UNDER 50 WORDS. Be concise — list only the most important things to avoid.
Write 'reference_prompt' for each stage/variant using: "High-quality graphic novel illustration, detailed comic book art style, dramatic cinematic lighting, gritty realism, masterpiece, highly detailed"
NAMING RULE (CRITICAL): For 'character_id' and 'name', use ONLY the character's FIRST NAME (e.g. "Meera", not "Meera Sharma"). NEVER use full names, surnames, or last names anywhere in your output. This applies to reference_prompt and trait_tags as well.
CONTENT POLICY (CRITICAL): The image generator has a strict safety filter. Your descriptions MUST NOT contain any gore, blood, violence, weapons, self-harm, NSFW, or real people. If the story has dark, violent, or mature themes, you MUST creatively SANITIZE the prompt. Focus on character expressions, dramatic lighting, and tense atmosphere rather than explicit physical injuries, weapons, or violent acts.
"""
    
    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "negative_prompt": types.Schema(type=types.Type.STRING),
            "characters": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "character_id": types.Schema(type=types.Type.STRING),
                        "name": types.Schema(type=types.Type.STRING),
                        "age_stages": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "stage_id": types.Schema(type=types.Type.STRING),
                                    "age_descriptor": types.Schema(type=types.Type.STRING),
                                    "trait_tags": types.Schema(type=types.Type.STRING),
                                    "reference_prompt": types.Schema(type=types.Type.STRING),
                                },
                                required=["stage_id", "age_descriptor", "trait_tags", "reference_prompt"]
                            )
                        ),
                    },
                    required=["character_id", "name", "age_stages"]
                )
            ),
            "locations": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "location_id": types.Schema(type=types.Type.STRING),
                        "name": types.Schema(type=types.Type.STRING),
                        "trait_tags": types.Schema(type=types.Type.STRING),
                        "variants": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "variant_id": types.Schema(type=types.Type.STRING),
                                    "condition_descriptor": types.Schema(type=types.Type.STRING),
                                    "trait_tags": types.Schema(type=types.Type.STRING),
                                    "reference_prompt": types.Schema(type=types.Type.STRING),
                                },
                                required=["variant_id", "condition_descriptor", "trait_tags", "reference_prompt"]
                            )
                        ),
                    },
                    required=["location_id", "name", "trait_tags", "variants"]
                )
            ),
        },
        required=["negative_prompt", "characters", "locations"]
    )
    
    # Character Pass ALWAYS uses Gemini — DeepSeek enters infinite repetition loops
    # on complex nested schemas (especially the negative_prompt free-text field).
    result = _call_gemini_with_retry(client, sys_inst, story_text, schema)
        
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4)
    return result

def _split_transcript_into_chunks(transcript_data, chunk_duration=CHUNK_DURATION_SECONDS):
    """Split transcript words into time-based chunks at sentence boundaries.
    
    Returns a list of chunks, each containing:
      - 'words': the raw word-level transcript entries for that chunk
      - 'start_time' / 'end_time': the time boundaries
      - 'chunk_index': for logging
    """
    words = transcript_data.get("words", transcript_data) if isinstance(transcript_data, dict) else transcript_data
    if not words:
        return []
    
    total_duration = words[-1]['end'] - words[0]['start']
    
    # If short enough, return a single chunk (no splitting needed)
    if total_duration <= chunk_duration * 1.3:
        return [{
            "words": words,
            "start_time": words[0]['start'],
            "end_time": words[-1]['end'],
            "chunk_index": 0
        }]
    
    # Group words into sentences first (using punctuation and pause detection)
    punctuation_marks = ['.', '।', '!', '?', '...']
    sentences = []  # Each: {"words": [...], "start": float, "end": float}
    current_words = []
    sentence_start = None
    
    for i, w in enumerate(words):
        if sentence_start is None:
            sentence_start = w['start']
        current_words.append(w)
        
        is_end = False
        if any(w['word'].endswith(p) for p in punctuation_marks):
            is_end = True
        elif i < len(words) - 1 and (words[i+1]['start'] - w['end']) > 0.8:
            is_end = True
        elif i == len(words) - 1:
            is_end = True
        
        if is_end:
            sentences.append({
                "words": list(current_words),
                "start": sentence_start,
                "end": w['end']
            })
            current_words = []
            sentence_start = None
    
    # Now group sentences into chunks of ~chunk_duration seconds
    chunks = []
    current_chunk_words = []
    chunk_start_time = sentences[0]['start'] if sentences else 0.0
    chunk_accumulated_dur = 0.0
    
    for sent in sentences:
        sent_dur = sent['end'] - sent['start']
        current_chunk_words.extend(sent['words'])
        chunk_accumulated_dur += sent_dur
        
        # Cut a new chunk if we've exceeded the target duration
        if chunk_accumulated_dur >= chunk_duration:
            chunks.append({
                "words": list(current_chunk_words),
                "start_time": chunk_start_time,
                "end_time": sent['end'],
                "chunk_index": len(chunks)
            })
            current_chunk_words = []
            chunk_start_time = sent['end']
            chunk_accumulated_dur = 0.0
    
    # Don't forget the last chunk
    if current_chunk_words:
        chunks.append({
            "words": list(current_chunk_words),
            "start_time": chunk_start_time,
            "end_time": current_chunk_words[-1]['end'],
            "chunk_index": len(chunks)
        })
    
    return chunks


def run_director_pass(story_text, transcript_data, char_data, output_path, is_short=False):
    print("  \U0001F3AC Running Director Pass...")
    client = get_gemini_client() if AI_PROVIDER == "gemini" else None
    
    # Build reference blocks
    char_refs = "\\n".join(
        f"- character_id: {c.get('character_id')} | stage_id: {s.get('stage_id')} | trait_tags: {s.get('trait_tags')}"
        for c in char_data.get("characters", []) for s in c.get("age_stages", [])
    ) or "(none)"
    
    loc_refs = "\\n".join(
        f"- location_id: {l.get('location_id')} | variant_id: {v.get('variant_id')} | trait_tags: {v.get('trait_tags')}"
        for l in char_data.get("locations", []) for v in l.get("variants", [])
    ) or "(none)"
    
    pacing_rule = "FLEXIBLE 7-10 SECOND TARGET"
    if is_short:
        pacing_rule = "FAST-PACED VERTICAL (9:16) 1-2 SECOND TARGET. Frame for vertical orientation."
    
    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "scenes": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "scene_id": types.Schema(type=types.Type.INTEGER),
                        "start_time": types.Schema(type=types.Type.NUMBER),
                        "end_time": types.Schema(type=types.Type.NUMBER),
                        "scene_type": types.Schema(type=types.Type.STRING, enum=["intro", "dialogue", "discovery", "emotional", "description", "action"]),
                        "characters_present": types.Schema(
                            type=types.Type.ARRAY, items=types.Schema(
                                type=types.Type.OBJECT, properties={"character_id": types.Schema(type=types.Type.STRING), "stage_id": types.Schema(type=types.Type.STRING)}
                            )
                        ),
                        "locations_present": types.Schema(
                            type=types.Type.ARRAY, items=types.Schema(
                                type=types.Type.OBJECT, properties={"location_id": types.Schema(type=types.Type.STRING), "variant_id": types.Schema(type=types.Type.STRING)}
                            )
                        ),
                        "visual_prompt": types.Schema(type=types.Type.STRING),
                    },
                    required=["scene_id", "start_time", "end_time", "scene_type", "visual_prompt"]
                )
            )
        },
        required=["scenes"]
    )
    
    # Split transcript into manageable chunks
    chunks = _split_transcript_into_chunks(transcript_data)
    total_chunks = len(chunks)
    
    if total_chunks > 1:
        print(f"    📦 Transcript split into {total_chunks} chunks (~{CHUNK_DURATION_SECONDS}s each)")
    
    all_scenes = []
    scene_counter = 1
    prev_scenes_context = []  # Last 2 scenes from previous chunk for continuity
    
    for chunk in chunks:
        chunk_idx = chunk['chunk_index']
        chunk_words = chunk['words']
        
        if total_chunks > 1:
            print(f"    → Chunk {chunk_idx+1}/{total_chunks} "
                  f"[{chunk['start_time']:.1f}s - {chunk['end_time']:.1f}s]...")
        
        # Build context carryover from previous chunk
        context_block = ""
        if prev_scenes_context:
            prev_summary = "\n".join(
                f"  Scene {s.get('scene_id')}: [{s.get('start_time')}s-{s.get('end_time')}s] "
                f"type={s.get('scene_type')} camera={s.get('camera_movement')} "
                f"prompt=\"{s.get('visual_prompt', '')[:100]}...\""
                for s in prev_scenes_context
            )
            context_block = f"\n\nPREVIOUS SCENES (maintain visual continuity, do NOT repeat these):\n{prev_summary}\n"
        
        sys_inst = f"""You are an Elite Executive Video Director. Segment the timestamped script into sequential visual scenes.
LOCKED CHARACTERS:\\n{char_refs}\\nLOCKED LOCATIONS:\\n{loc_refs}

RULES:
1. PACING: {pacing_rule}. Set 'start_time' and 'end_time' strictly from the transcript.
2. PROMPT FORMAT (CRITICAL): You MUST format every single `visual_prompt` EXACTLY using this strict comma-separated template. Do NOT write natural prose. Do NOT include any art style descriptions. Fill in every section perfectly and separate them with commas: `Character's exact trait_tags and appearance, Single frozen action, Surrounding environment and location trait_tags, Character emotion, Camera angle/shot type`.
3. CHARACTERS: Maximum 3 characters present in any single scene.
4. COVERAGE: You MUST cover the ENTIRE transcript provided. Your first scene must start at the first word's timestamp and your last scene must end at the last word's timestamp. Do not skip any part of the transcript.
5. NAMES (CRITICAL): In visual_prompt, ONLY use first names for characters. NEVER use full names or surnames (e.g. write "Meera" not "Meera Sharma"). This is required by the image generator's content policy.
8. CONTENT POLICY (CRITICAL): The image generator has a strict safety filter. Your visual_prompts MUST NOT contain any gore, blood, violence, weapons, self-harm, NSFW, or real people. If the story has dark, violent, or mature themes, you MUST creatively SANITIZE the prompt. Focus on character expressions, dramatic lighting, abstract metaphors, and tense atmosphere rather than explicit physical injuries, weapons, or violent acts.
9. SINGLE ACTION RULE (CRITICAL): An image generator can only render a single frozen moment in time. Your visual_prompt MUST NOT describe sequential actions or multiple events (e.g. "he throws a punch and turns around"). Describe exactly ONE static snapshot using present progressive tense (e.g. "he is in the middle of throwing a punch, arm extended mid-air"). Avoid describing sequences.
{context_block}"""
        
        # Format only this chunk's transcript
        fmt_input = "\\n".join(f"[{w['start']}s - {w['end']}s]: {w['word']}" for w in chunk_words)
        user_content = f"Story (full narrative for context):\\n{story_text}\\n\\nTranscript (generate scenes ONLY for this section):\\n{fmt_input}"
        
        # Director Pass ALWAYS uses Gemini — DeepSeek overflows on complex schemas
        # with long visual_prompt strings and nested character/location arrays.
        if client is None:
            client = get_gemini_client()
        result = _call_gemini_with_retry(client, sys_inst, user_content, schema)
        
        chunk_scenes = result.get("scenes", [])
        
        global_style = "High-quality graphic novel illustration, detailed comic book art style, dramatic cinematic lighting, gritty realism, masterpiece, highly detailed"
        
        # Renumber scene_ids to be globally sequential and inject the global style
        for s in chunk_scenes:
            s["scene_id"] = scene_counter
            vp = s.get("visual_prompt", "")
            vp_clean = vp.replace("[", "").replace("]", "")
            s["visual_prompt"] = f"{global_style}, {vp_clean}, no text box"
            start_t = s.get("start_time", 0.0)
            end_t = s.get("end_time", 0.0)
            s["duration"] = round(end_t - start_t, 3)
            scene_counter += 1
        
        all_scenes.extend(chunk_scenes)
        
        # Save last 2 scenes for context carryover to next chunk
        prev_scenes_context = chunk_scenes[-2:] if len(chunk_scenes) >= 2 else chunk_scenes[:]
        
        if total_chunks > 1:
            print(f"      ✅ Got {len(chunk_scenes)} scenes (total so far: {len(all_scenes)})")
    
    # Save the complete stitched blueprint
    final_result = {"scenes": all_scenes}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_result, f, indent=4)
    
    print(f"  ✅ Director Pass complete! {len(all_scenes)} scenes across {total_chunks} chunk(s).")
    return final_result

def _normalize_word(w):
    import re
    return re.sub(r"[^a-z0-9']", "", w.lower().strip())

def _resolve_triggers(trigger_words, words, search_after_index=-1):
    for candidate in trigger_words or []:
        nc = _normalize_word(candidate)
        if not nc: continue
        for idx, w in enumerate(words):
            if idx <= search_after_index: continue
            if _normalize_word(w["word"]) == nc:
                return {"matched_word": w["word"], "word_index": idx, "start_time": w["start"], "end_time": w["end"]}
    return None

def run_music_pass(story_text, transcript_data, output_path, sfx_lib_path):
    print("  \U0001F3B5 Running Music & SFX Pass...")
    client = get_gemini_client()
        
    sfx_lib = {}
    if os.path.exists(sfx_lib_path):
        with open(sfx_lib_path, "r") as f: sfx_lib = json.load(f)
        
    s_menu = "\\n".join(f"- {k}: {v}" for k, v in sfx_lib.items()) or "None"
    
    sys_inst = f"""You are an Elite Film Composer & Sound Designer. Score this video by breaking the narration into multiple emotional movements.
1. INVENT completely new, unique `music_id`s for this video. Generate a highly descriptive `music_prompt` for each distinct emotional phase so the user can generate the track using an AI music tool. Do NOT reuse tracks.
2. Use 'start_trigger_words' to precisely map exactly when a new music track should fade in and take over the score.
3. Strategically place `duck_cues` to dynamically lower the volume of the music during intense dialogue or SFX moments.
4. For SFX, use existing library IDs if appropriate, or invent new ones. Provide an 'sfx_description' that precisely describes the acoustic properties.
EXISTING SFX: {s_menu}
"""
    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "music_tracks": types.Schema(
                type=types.Type.ARRAY, items=types.Schema(
                    type=types.Type.OBJECT, properties={
                        "music_id": types.Schema(type=types.Type.STRING),
                        "music_prompt": types.Schema(type=types.Type.STRING),
                        "emotion": types.Schema(type=types.Type.STRING),
                        "start_trigger_words": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                    }, required=["music_id", "music_prompt", "emotion", "start_trigger_words"]
                )
            ),
            "sfx_cues": types.Schema(
                type=types.Type.ARRAY, items=types.Schema(
                    type=types.Type.OBJECT, properties={
                        "sfx_id": types.Schema(type=types.Type.STRING),
                        "trigger_words": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                        "reason": types.Schema(type=types.Type.STRING),
                        "sfx_description": types.Schema(type=types.Type.STRING),
                    }, required=["sfx_id", "trigger_words", "reason", "sfx_description"]
                )
            ),
            "duck_cues": types.Schema(
                type=types.Type.ARRAY, items=types.Schema(
                    type=types.Type.OBJECT, properties={
                        "cue_id": types.Schema(type=types.Type.INTEGER),
                        "trigger_words": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                        "volume_direction": types.Schema(type=types.Type.STRING, enum=["increase", "decrease"]),
                    }, required=["cue_id", "trigger_words", "volume_direction"]
                )
            ),
        }, required=["music_tracks", "sfx_cues", "duck_cues"]
    )
    
    # Music Pass ALWAYS uses Gemini for reliable structured output.
    result = _call_gemini_with_retry(client, sys_inst, story_text, schema)
    
    # Resolve timestamps
    words = transcript_data.get("words", transcript_data) if isinstance(transcript_data, dict) else transcript_data
    resolved_tracks = []
    last_idx = -1
    for mt in result.get("music_tracks", []):
        r = _resolve_triggers(mt["start_trigger_words"], words, last_idx)
        if r:
            last_idx = r["word_index"]
            mt["start_time"] = r["start_time"]
            resolved_tracks.append(mt)
                
    resolved_sfx = []
    last_idx = -1
    for sfx in result.get("sfx_cues", []):
        r = _resolve_triggers(sfx["trigger_words"], words, last_idx)
        if r:
            last_idx = r["word_index"]
            sfx["start_time"] = r["start_time"]
            resolved_sfx.append(sfx)
            if sfx["sfx_id"] not in sfx_lib:
                sfx_lib[sfx["sfx_id"]] = sfx.get("sfx_description", sfx.get("reason", ""))
                
    resolved_duck = []
    last_idx = -1
    for duck in result.get("duck_cues", []):
        r = _resolve_triggers(duck["trigger_words"], words, last_idx)
        if r:
            last_idx = r["word_index"]
            duck["start_time"] = r["start_time"]
            resolved_duck.append(duck)
            
    final_blueprint = {
        "music_tracks": resolved_tracks,
        "sfx_cues": resolved_sfx,
        "duck_cues": resolved_duck
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_blueprint, f, indent=4)
        
    with open(sfx_lib_path, "w", encoding="utf-8") as f:
        json.dump(sfx_lib, f, indent=4)
        
    return final_blueprint

def generate_blueprints(project_dir, is_short=False):
    """
    Main entry point for the supervisor to generate blueprints.
    Assumes transcript.json and story.txt exist in project_dir.
    """
    proj = Path(project_dir)
    story_file = proj / "story.txt"
    transcript_file = proj / "transcript.json"
    
    if not story_file.exists() or not transcript_file.exists():
        print("  \u274C Missing story.txt or transcript.json")
        return False
        
    with open(story_file, "r", encoding="utf-8") as f: story_text = f.read()
    with open(transcript_file, "r", encoding="utf-8") as f: transcript_data = json.load(f)
    
    char_out = proj / "character_prompts.json"
    vid_out = proj / "video_blueprint.json"
    mus_out = proj / "music_blueprint.json"
    
    # Paths to global libraries
    base_dir = proj.parent
    mus_lib = base_dir / "music" / "music_library.json"
    sfx_lib = base_dir / "sfx" / "sfx_library.json"
    
    try:
        char_data = run_character_pass(story_text, char_out)
        run_director_pass(story_text, transcript_data, char_data, vid_out, is_short=is_short)
        
        # Shorts might not need a complex music pass in the same way, but let's run it anyway
        run_music_pass(story_text, transcript_data, mus_out, sfx_lib)
        
        print("  \u2705 Blueprints successfully generated!")
        return True
    except Exception as e:
        print(f"  \u274C Blueprint Generation failed: {e}")
        return False

def run_hindi_director_pass(hin_story_text, hin_transcript_data, eng_vid_bp, output_path, is_short=False):
    print("  \U0001F3AC Running Hindi Director Pass (Index-Based Semantic Mapping)...")
    
    eng_scenes = eng_vid_bp.get("video_blueprint", []) or eng_vid_bp.get("scenes", [])
    
    eng_block = []
    for s in eng_scenes:
        eng_block.append(f"ENG_SCENE {s.get('scene_id')} [{s.get('start_time')}s - {s.get('end_time')}s] ({s.get('scene_type')}): {s.get('visual_prompt')}")
    blueprint_block = "\n".join(eng_block)
    
    # 2. Reconstruct story from transcript and build index memory
    transcript_words = hin_transcript_data.get("words", hin_transcript_data) if isinstance(hin_transcript_data, dict) else hin_transcript_data
    
    sentences = []
    current_sentence_words = []
    sentence_start_time = None
    punctuation_marks = ['.', '।', '!', '?', '...']
    
    for i, w in enumerate(transcript_words):
        if sentence_start_time is None:
            sentence_start_time = w['start']
            
        word_text = w['word']
        current_sentence_words.append(word_text)
        
        is_end = False
        if any(word_text.endswith(p) for p in punctuation_marks):
            is_end = True
        elif i < len(transcript_words) - 1 and (transcript_words[i+1]['start'] - w['end']) > 0.8:
            is_end = True
        elif i == len(transcript_words) - 1:
            is_end = True
            
        if is_end:
            sentences.append({
                "text": " ".join(current_sentence_words),
                "start": sentence_start_time,
                "end": w['end']
            })
            current_sentence_words = []
            sentence_start_time = None

    fmt_input = "\\n".join([f"[{idx}] {s['text']}" for idx, s in enumerate(sentences)])
    
    # 3. AI Instructions & Schema
    sys_inst = f"""You are an Elite Executive Video Director adapting a Hindi video timeline using existing images from the English version.
The English pipeline has already generated still images and camera movements for each scene. Your job is to RE-MAP those existing scenes onto the Hindi timeline by mapping the Hindi sentences to the correct english_source_scene_id.

ENGLISH VIDEO BLUEPRINT (the source storyboard you are adapting):
{blueprint_block}

RULES:
1. SEMANTIC MATCHING (CRITICAL): Read the Hindi Story provided below, which is broken down into numbered sentences (e.g., [0], [1], [2]). Identify the specific topics/events being spoken about. Find the 'english_source_scene_id' from the English Video Blueprint that best matches that topic.
2. FAST PACING (CRITICAL): The English video uses rapid scene changes. You MUST use as many of the provided English scenes as possible to maintain this fast pacing. Do not group too many sentences together. Switch images frequently, typically every 1 or 2 sentences!
3. OUTPUT: For each scene, output a sequentially increasing 'scene_id' (1, 2, 3...), the 'english_source_scene_id' you are linking to, and the index number of the first sentence ('start_index') and the index number of the last sentence ('end_index') that belong to that scene. Ensure every sentence from 0 to {len(sentences)-1} is covered!
4. REUSE RULES: You may reuse the same english_source_scene_id later if the topic returns, but DO NOT use the same ID in consecutive scenes.
5. NO INVENTED IDS (CRITICAL): You MUST ONLY use the exact integer 'english_source_scene_id' values provided in the English Video Blueprint. DO NOT invent new IDs.
"""
    
    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "scenes": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "scene_id": types.Schema(type=types.Type.INTEGER),
                        "english_source_scene_id": types.Schema(type=types.Type.INTEGER),
                        "start_index": types.Schema(type=types.Type.INTEGER),
                        "end_index": types.Schema(type=types.Type.INTEGER),
                    },
                    required=["scene_id", "english_source_scene_id", "start_index", "end_index"]
                )
            )
        },
        required=["scenes"]
    )
    
    user_content = f"Numbered Hindi Story:\\n{fmt_input}"
    client = get_gemini_client() if AI_PROVIDER == "gemini" else None
    
    print("    -> Sending story to AI for semantic mapping...")
    if AI_PROVIDER == "gemini":
        result = _call_gemini_with_retry(client, sys_inst, user_content, schema)
    else:
        result = _call_together_with_retry(sys_inst, user_content, schema, max_retries=3)
        
    ai_scenes = result.get("scenes", [])
    
    # 4. Post-Processing: Map indices back to timestamps and copy metadata
    final_scenes = []
    eng_dict = {s.get("scene_id"): s for s in eng_scenes}
    
    for s in ai_scenes:
        s_idx = s.get("start_index", 0)
        e_idx = s.get("end_index", s_idx)
        
        # Safety bounds
        s_idx = max(0, min(s_idx, len(sentences) - 1))
        e_idx = max(0, min(e_idx, len(sentences) - 1))
        
        if s_idx > e_idx:
            s_idx, e_idx = e_idx, s_idx
            
        real_start = sentences[s_idx]["start"]
        real_end = sentences[e_idx]["end"]
        
        eng_src_id = s.get("english_source_scene_id")
        src = eng_dict.get(eng_src_id, {})
        
        final_scenes.append({
            "scene_id": s.get("scene_id"),
            "english_source_scene_id": eng_src_id,
            "start_time": real_start,
            "end_time": real_end,
            "scene_type": src.get("scene_type", "description"),
            "characters_present": src.get("characters_present", []),
            "locations_present": src.get("locations_present", []),
            "visual_prompt": src.get("visual_prompt", ""),
            "camera_movement": src.get("camera_movement", "push_in"),
            "transition_type": src.get("transition_type", "cut"),
            "transition_duration": src.get("transition_duration", 0.0)
        })
        
    final_result = {"scenes": final_scenes}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_result, f, indent=4)
        
    print(f"  \u2705 Mapped {len(final_scenes)} scenes perfectly to timestamps!")
    return final_result

def generate_hindi_blueprints(eng_visual_dir, hin_audio_dir, is_short=False):
    hin_proj = Path(hin_audio_dir)
    eng_proj = Path(eng_visual_dir)
    
    hin_story_file = hin_proj / "story.txt"
    hin_transcript_file = hin_proj / "transcript.json"
    eng_vid_bp_file = eng_proj / "video_blueprint.json"
    
    if not hin_story_file.exists() or not hin_transcript_file.exists() or not eng_vid_bp_file.exists():
        print("  \u274C Missing Hindi text/transcript or English video_blueprint.json")
        return False
        
    with open(hin_story_file, "r", encoding="utf-8") as f: hin_story_text = f.read()
    with open(hin_transcript_file, "r", encoding="utf-8") as f: hin_transcript_data = json.load(f)
    with open(eng_vid_bp_file, "r", encoding="utf-8") as f: eng_vid_bp = json.load(f)
    
    hin_vid_out = hin_proj / "video_blueprint.json"
    hin_mus_out = hin_proj / "music_blueprint.json"
    
    base_dir = hin_proj.parent
    mus_lib = base_dir / "music" / "music_library.json"
    sfx_lib = base_dir / "sfx" / "sfx_library.json"
    
    try:
        run_hindi_director_pass(hin_story_text, hin_transcript_data, eng_vid_bp, hin_vid_out, is_short=is_short)
        print("  \u2705 Hindi Video Blueprint successfully generated!")
        
        run_music_pass(hin_story_text, hin_transcript_data, hin_mus_out, sfx_lib)
        print("  \u2705 Hindi Music Blueprint successfully generated!")
        return True
    except Exception as e:
        print(f"  \u274C Hindi Blueprint Generation failed: {e}")
        return False
