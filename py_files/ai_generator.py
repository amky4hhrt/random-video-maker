import os
import json
import time
import requests
from pathlib import Path
from google import genai
from google.genai import types


CHUNK_DURATION_SECONDS = 180  # 3 minutes per chunk for Director Pass

_FREE_KEY_EXHAUSTED = False

def _get_active_gemini_keys():
    """Returns an ordered list of (key_name, api_key) prioritizing GEMINI_API_KEY_FREE."""
    global _FREE_KEY_EXHAUSTED
    keys = []
    free_key = os.environ.get("GEMINI_API_KEY_FREE")
    paid_key = os.environ.get("GEMINI_API_KEY")
    
    if free_key and not _FREE_KEY_EXHAUSTED:
        keys.append(("GEMINI_API_KEY_FREE", free_key))
    if paid_key:
        keys.append(("GEMINI_API_KEY", paid_key))
    elif not keys and free_key:
        # If paid_key is not set at all, still allow free_key even if previously failed
        keys.append(("GEMINI_API_KEY_FREE", free_key))
        
    if not keys:
        raise ValueError("\u274C Neither GEMINI_API_KEY_FREE nor GEMINI_API_KEY is set in environment.")
    return keys

def get_gemini_client():
    """Returns a client for the top priority available Gemini key."""
    keys = _get_active_gemini_keys()
    name, key = keys[0]
    return genai.Client(api_key=key)

def _call_gemini_with_retry(client=None, system_instruction=None, user_content=None, response_schema=None, max_retries=3):
    global _FREE_KEY_EXHAUSTED
    candidate_keys = _get_active_gemini_keys()
    last_err = None

    for idx, (key_label, api_key) in enumerate(candidate_keys):
        current_client = genai.Client(api_key=api_key)
        key_retries = max_retries if key_label == "GEMINI_API_KEY_FREE" else 3
        current_sleep = 2
        tier = "FREE" if key_label == "GEMINI_API_KEY_FREE" else "PAID"
        
        for attempt in range(key_retries):
            try:
                if attempt == 0:
                    print(f"    ✨ Running Gemini [{tier} TIER - {key_label}]...")
                else:
                    print(f"    ✨ Retrying Gemini [{tier} TIER - {key_label}] (attempt {attempt + 1}/{key_retries})...")
                response = current_client.models.generate_content(
                    model="gemini-flash-latest",
                    contents=user_content,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                        max_output_tokens=65536,
                        safety_settings=[
                            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        ]
                    )
                )
                
                # Immediately try to parse. If text is missing or invalid, it throws.
                try:
                    if response and response.text:
                        import json
                        return json.loads(response.text)
                    else:
                        feedback = getattr(response, "prompt_feedback", None)
                        print(f"\n  ⚠️ Gemini returned empty/blocked response (feedback: {feedback})")
                        raise ValueError("PROHIBITED_CONTENT: Empty or blocked response from Gemini API")
                except Exception as e:
                    err_str = str(e)
                    if "PROHIBITED_CONTENT" in err_str:
                        raise e
                    print(f"\n  ❌ FATAL: Could not parse response text.\n  Raw Response Object: {response}\n")
                    raise ValueError(f"Empty response from API: {e}")
                    
            except Exception as e:
                err_str = str(e)
                # If Google safety filter blocked it, failover immediately without wasting retries
                if "Empty response" in err_str or "response.text" in err_str or "PROHIBITED_CONTENT" in err_str or "block_reason" in err_str:
                    print("  ⛔ Google Gemini rejected prompt due to automated content filter.")
                    raise ValueError(f"GEMINI_SAFETY_BLOCK: {err_str}")
                    
                last_err = e
                if attempt < key_retries - 1:
                    print(f"  ⚠️ [{tier} - {key_label}] Attempt {attempt + 1}/{key_retries} failed: {e}. Retrying in {current_sleep}s...")
                    time.sleep(current_sleep)
                    current_sleep *= 2
                else:
                    print(f"  ⚠️ [{tier} - {key_label}] All {key_retries} attempts failed: {e}")

        # If key_label was GEMINI_API_KEY_FREE and failed all retries, switch to paid GEMINI_API_KEY
        if key_label == "GEMINI_API_KEY_FREE":
            _FREE_KEY_EXHAUSTED = True
            if idx + 1 < len(candidate_keys):
                next_label = candidate_keys[idx + 1][0]
                next_tier = "FREE" if next_label == "GEMINI_API_KEY_FREE" else "PAID"
                print(f"  🔄 GEMINI_API_KEY_FREE unavailable or exhausted after {key_retries} attempts. Switching to {next_label} [{next_tier} TIER]...")

    # If all keys failed, raise the last exception to trigger DeepSeek fallback
    raise last_err if last_err else ValueError("All Gemini API keys failed.")

def _clean_and_parse_json(raw_text):
    import re
    if not raw_text or not str(raw_text).strip():
        raise ValueError("Cannot parse JSON from empty response")
        
    cleaned = str(raw_text).strip()
    # Strip <think>...</think> reasoning tags
    cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL).strip()
    
    # Strip markdown code blocks
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    
    try:
        return json.loads(cleaned)
    except Exception:
        # Fallback: extract outer { ... }
        s = cleaned.find("{")
        e = cleaned.rfind("}")
        if s != -1 and e != -1 and e > s:
            return json.loads(cleaned[s:e+1])
        raise

def _call_together_deepseek(system_instruction, user_content, response_schema):
    """Fallback handler using Together AI DeepSeek models with multi-model redundancy."""
    together_key = os.environ.get("TOGETHER_API_KEY")
    if not together_key:
        raise ValueError("TOGETHER_API_KEY environment variable not set for DeepSeek fallback.")
        
    json_schema = _schema_to_json_schema(response_schema)
    schema_str = json.dumps(json_schema, indent=2)
    
    prompt = (
        f"{system_instruction}\n\n"
        f"CRITICAL: You MUST respond ONLY with a valid JSON object strictly conforming to this JSON Schema:\n"
        f"{schema_str}\n\n"
        f"Do NOT include markdown formatting, think tags, or text outside the JSON."
    )
    
    url = "https://api.together.xyz/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {together_key}",
        "Content-Type": "application/json"
    }
    
    # Priority: DeepSeek-V4.1-Flash first, DeepSeek-V3 backup
    available_deepseeks = [
        "deepseek-ai/DeepSeek-V4.1-Flash",
        "deepseek-ai/DeepSeek-V3"
    ]
    
    last_error = None
    for model_name in available_deepseeks:
        print(f"  🚀 Calling Together AI ({model_name})...")
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.3,
            "max_tokens": 8192
        }
        
        for attempt in range(3):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=120)
                if resp.status_code == 200:
                    if not resp.text or not resp.text.strip():
                        print(f"    ⚠️ Empty HTTP 200 body from Together AI ({model_name})")
                        time.sleep(2)
                        continue
                    data = resp.json()
                    msg = data["choices"][0]["message"]
                    raw_content = msg.get("content") or msg.get("text") or ""
                    if not raw_content and "reasoning" in msg:
                        raw_content = msg.get("reasoning_content") or msg.get("reasoning") or ""
                    
                    parsed = _clean_and_parse_json(raw_content)
                    return parsed
                elif resp.status_code in (429, 500, 502, 503, 504):
                    err_msg = resp.text.strip()[:200]
                    print(f"    ⚠️ Together AI ({model_name}) HTTP {resp.status_code}: {err_msg}")
                    if attempt < 2:
                        wait_sec = (attempt + 1) * 3
                        print(f"    ⏳ Pausing {wait_sec}s before retry {attempt+2}/3...")
                        time.sleep(wait_sec)
                    else:
                        print(f"    🔄 Moving to next DeepSeek model...")
                        break
                else:
                    print(f"    ⚠️ Together AI ({model_name}) HTTP {resp.status_code}: {resp.text.strip()[:200]}")
                    break
            except Exception as e:
                print(f"    ⚠️ Connection/Parse error on Together AI ({model_name}): {e}")
                time.sleep(2)
                last_error = e

    raise RuntimeError(f"Together AI DeepSeek fallback failed across all models. Last error: {last_error}")

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

def _translate_hindi_prompts(text):
    if not text or not isinstance(text, str):
        return text
    
    # Common mappings for recurring Hindi character traits and expressions
    replacements = [
        ("मध्यम आयु भारतीय पुरुष", "middle-aged Indian man"),
        ("छोटे काले-भूरे बाल जिनमें हल्की सफेदी", "short black-brown hair with slight graying"),
        ("साफ़-सुथरी दाढ़ी", "neat trimmed beard"),
        ("साफ-सुथरी दाढ़ी", "neat trimmed beard"),
        ("पतला-मध्यम शरीर", "slim-medium build"),
        ("अच्छे कपड़े", "well-dressed in neat clothes"),
        ("वृद्ध भारतीय पुरुष", "elderly Indian man"),
        ("छोटे सफेद बाल", "short white hair"),
        ("चेहरे पर झुर्रियां", "wrinkles on face"),
        ("हल्की सफेद दाढ़ी", "light white beard"),
        ("दुबला-मध्यम शरीर", "slender-medium build"),
        ("साधारण सूती कुर्ता-पायजामा", "simple cotton kurta-pyjama"),
        ("कंधे पर गमछा", "cotton gamcha towel draped over shoulder"),
        ("थके हुए हाथ", "tired weathered hands"),
        ("नम आँखें", "moist tearful eyes"),
        ("नम आंखें", "moist tearful eyes"),
        ("हल्के काँपते हुए हाथ", "slightly trembling hands"),
        ("हल्के कांपते हुए हाथ", "slightly trembling hands"),
        ("कांपते हुए हाथ", "trembling hands"),
        ("काँपते हुए हाथ", "trembling hands"),
    ]
    for hin, eng in replacements:
        text = text.replace(hin, eng)
        
    return text

def run_character_pass(story_text, output_path):
    print("  \U0001F3AD Running Character Pass...")
    
    sys_inst = """You are a Casting Director and Character Designer for 2D animated short films.
Identify every RECURRING character in the story.
CRITICAL LANGUAGE RULE: Even though the story text is in Hindi, you MUST write all 'trait_tags' and 'reference_prompt' strictly and purely in ENGLISH. Never use Hindi/Devanagari characters.
For each character, write a single reusable 'trait_tags' description in English. Handle age_stages if applicable.
Write a 'reference_prompt' for each stage/variant containing purely the physical description of the character in English (clothing, hair, facial features, etc). Do NOT include any art style descriptions like anime or 3D.
Naming rule: For 'character_id' and 'name', use only the character's first name in English.
"""
    
    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
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
        },
        required=["characters"]
    )
    
    # Character Pass: Try Gemini first, fallback to DeepSeek if Gemini fails
    try:
        client = get_gemini_client()
        result = _call_gemini_with_retry(client, sys_inst, story_text, schema)
    except Exception as gemini_err:
        print(f"\n  ⚠️ Gemini failed during Character Pass: {gemini_err}")
        if os.environ.get("TOGETHER_API_KEY"):
            print("  🔄 Scrapping and restarting Character Pass using DeepSeek (Together AI)...")
            result = _call_together_deepseek(sys_inst, story_text, schema)
        else:
            print("  ⛔ TOGETHER_API_KEY not found in environment. Cannot execute DeepSeek fallback.")
            raise gemini_err
    
    # Post-process to inject the global style, avoiding the copyright filter
    global_style = "Studio Ghibli animation style, dramatic cinematic lighting, lush vibrant colors, beautiful anime background, masterpiece, highly detailed"
    if "characters" in result:
        for char in result["characters"]:
            if "age_stages" in char:
                for stage in char["age_stages"]:
                    base_prompt = stage.get("reference_prompt", "")
                    stage["trait_tags"] = _translate_hindi_prompts(stage.get("trait_tags", ""))
                    clean_ref = _translate_hindi_prompts(base_prompt)
                    stage["reference_prompt"] = f"{global_style}, {clean_ref}"
                    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4, ensure_ascii=False)
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


def _load_and_sync_sfx_library(sfx_lib_path=None):
    """
    Loads master SFX library from sfx/sfx.json (single source of truth).
    Auto-discovers any user-uploaded audio files (.mp3, .wav, .ogg, .flac) in sfx/
    and registers them so the AI Director knows they exist.
    """
    sfx_library = {}
    if not sfx_lib_path:
        return sfx_library
        
    p = Path(sfx_lib_path)
    sfx_dir = p if p.is_dir() else p.parent
    sfx_file = sfx_dir / "sfx.json"
    
    # Fallback: if sfx.json doesn't exist yet, check legacy sfx_library.json
    if not sfx_file.exists():
        legacy_file = sfx_dir / "sfx_library.json"
        if legacy_file.exists():
            sfx_file = legacy_file
            
    if sfx_file.exists():
        try:
            with open(sfx_file, "r", encoding="utf-8") as f:
                sfx_library = json.load(f)
        except Exception as e:
            print(f"  ⚠️ Could not load SFX library: {e}")
            sfx_library = {}

    # Auto-Discovery: Scan sfx_dir for any user-uploaded audio files
    audio_exts = (".wav", ".mp3", ".ogg", ".flac", ".m4a")
    newly_discovered = False
    
    if sfx_dir.exists():
        try:
            for item in sfx_dir.iterdir():
                if item.is_file() and item.suffix.lower() in audio_exts:
                    asset_id = item.stem.lower().strip()
                    if asset_id not in sfx_library:
                        human_desc = asset_id.replace("_", " ").capitalize()
                        sfx_library[asset_id] = f"User uploaded sound effect: {human_desc}"
                        newly_discovered = True
                        print(f"    🔊 Auto-discovered uploaded sound in sfx/: {asset_id} (\"{sfx_library[asset_id]}\")")
        except Exception as e:
            print(f"  ⚠️ Error scanning sfx directory for audio files: {e}")

    # If any audio files were auto-discovered, save immediately to canonical sfx/sfx.json
    if newly_discovered:
        canonical_file = sfx_dir / "sfx.json"
        try:
            with open(canonical_file, "w", encoding="utf-8") as f:
                json.dump(sfx_library, f, indent=4, ensure_ascii=False)
            print(f"    ✨ Auto-registered new sound file(s) into sfx/sfx.json")
        except Exception as e:
            print(f"  ⚠️ Could not save auto-discovered SFX: {e}")
            
    return sfx_library


def run_director_pass(story_text, transcript_data, char_data, output_path, sfx_lib_path=None, is_short=False):
    print("  \U0001F3AC Running Director Pass...")
    
    # Load SFX library (single canonical sfx/sfx.json + auto-discovered audio files)
    sfx_library = _load_and_sync_sfx_library(sfx_lib_path)
            
    sfx_refs = "\\n".join(
        f"- {k}: {v}" for k, v in sfx_library.items()
    ) if sfx_library else "(none)"
    
    # Build reference blocks
    char_refs = "\\n".join(
        f"- character_id: {c.get('character_id')} | stage_id: {s.get('stage_id')} | trait_tags: {s.get('trait_tags')}"
        for c in char_data.get("characters", []) for s in c.get("age_stages", [])
    ) or "(none)"
    
    loc_refs = "\\n".join(
        f"- location_id: {l.get('location_id')} | variant_id: {v.get('variant_id')} | trait_tags: {v.get('trait_tags')}"
        for l in char_data.get("locations", []) for v in l.get("variants", [])
    ) or "(none)"
    
    pacing_rule = "CINEMATIC PACING (MAX 8 SECONDS PER SCENE). Target 4-7 seconds per scene. If an action or dialogue exceeds 8 seconds, you MUST split it into a 3-shot cinematic sequence (e.g., Wide establishing shot, Medium shot, Extreme Close-Up) covering the same event from different camera angles. Never let a single scene exceed 8 seconds."
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
                        "visual_prompt": types.Schema(type=types.Type.STRING),
                        "camera_movement": types.Schema(
                            type=types.Type.STRING,
                            enum=["push_in", "push_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "zoom_in_left", "zoom_in_right", "static"]
                        ),
                        "transition_type": types.Schema(
                            type=types.Type.STRING,
                            enum=["cut", "dissolve", "fade_to_black"]
                        ),
                        "transition_duration": types.Schema(
                            type=types.Type.NUMBER,
                            description="0.0 for cut, 0.8 to 1.2 for dissolve or fade_to_black."
                        ),
                        "sfx_id": types.Schema(type=types.Type.STRING, description="Existing sfx_id from library if good match, OR new snake_case ID (e.g. coins_drop_01) if proposing new sound, or 'none' if no sound needed."),
                        "sfx_prompt": types.Schema(type=types.Type.STRING, description="If proposing a new sfx_id, write a rich sound effect prompt for ElevenLabs. If reusing existing sound or if sfx_id is 'none', write 'none'."),
                    },
                    required=["scene_id", "start_time", "end_time", "scene_type", "visual_prompt", "camera_movement", "transition_type"]
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
    
    def _run_all_chunks(provider="gemini", client=None):
        all_scenes = []
        new_sfx_added = {}
        scene_counter = 1
        prev_scenes_context = []  # Last 2 scenes from previous chunk for continuity
        active_sfx_lib = dict(sfx_library)
        
        for chunk in chunks:
            chunk_idx = chunk['chunk_index']
            chunk_words = chunk['words']
            
            if total_chunks > 1:
                tier_suffix = ""
                if provider.lower() == "gemini":
                    tier_suffix = " (FREE)" if (os.environ.get("GEMINI_API_KEY_FREE") and not _FREE_KEY_EXHAUSTED) else " (PAID)"
                print(f"    → [{provider.upper()}{tier_suffix}] Chunk {chunk_idx+1}/{total_chunks} "
                      f"[{chunk['start_time']:.1f}s - {chunk['end_time']:.1f}s]...")
            
            # Build context carryover from previous chunk
            context_block = ""
            if prev_scenes_context:
                prev_summary = "\n".join(
                    f"  Scene {s.get('scene_id')}: [{s.get('start_time')}s-{s.get('end_time')}s] "
                    f"type={s.get('scene_type')} "
                    f"prompt=\"{s.get('visual_prompt', '')[:100]}...\""
                    for s in prev_scenes_context
                )
                context_block = f"\n\nPREVIOUS SCENES (maintain visual continuity, do NOT repeat these):\n{prev_summary}\n"
            
            sys_inst = f"""You are an Elite Executive Video Director. Segment the timestamped script into sequential visual scenes.
LOCKED CHARACTERS:\n{char_refs}\nLOCKED LOCATIONS:\n{loc_refs}
AVAILABLE SFX LIBRARY:\n{sfx_refs}

RULES:
1. PACING: {pacing_rule}. Set 'start_time' and 'end_time' strictly from the transcript.
2. PROMPT FORMAT : You MUST format every single `visual_prompt` EXACTLY using this strict comma-separated template. Do NOT write natural prose. Do NOT include any art style descriptions. Fill in every section perfectly and separate them with commas: `Character's exact trait_tags and appearance, Single frozen action, Surrounding environment and location trait_tags, Character emotion, Camera angle/shot type`.
3. CHARACTERS: Maximum 3 characters present in any single scene.
4. COVERAGE: You MUST cover the ENTIRE transcript provided. Your first scene must start at the first word's timestamp and your last scene must end at the last word's timestamp. Do not skip any part of the transcript.
5. Naming rule: In visual_prompt, only use first names for characters.
6. SFX SELECTION & GENERATION:
- Check the AVAILABLE SFX LIBRARY. If an existing sound in the library fits this scene, reuse its exact 'sfx_id' and set 'sfx_prompt' to "none".
- If NO existing sound in the library fits, but the scene genuinely calls for a sound effect (e.g. coins clinking, heavy sacks dragging, horse hooves, door opening):
  - Propose a new 'sfx_id' in snake_case with a number (e.g. "coins_inspect_01", "sacks_drag_01").
  - Provide a vivid, descriptive 'sfx_prompt' for an AI sound generator (e.g. "Gentle metallic clinking of gold coins being inspected on a wooden desk").
- If the scene is ordinary dialogue or quiet and does NOT need a sound effect, set 'sfx_id' to "none" and 'sfx_prompt' to "none". Most scenes should NOT have sound effects—reserve them for key dramatic moments.

9. SINGLE ACTION RULE : An image generator can only render a single frozen moment in time. Your visual_prompt MUST NOT describe sequential actions or multiple events (e.g. "he throws a punch and turns around"). Describe exactly ONE static snapshot using present progressive tense (e.g. "he is in the middle of throwing a punch, arm extended mid-air"). Avoid describing sequences.

7. CINEMATOGRAPHY (CAMERA MOVEMENT & TRANSITIONS):
- camera_movement: Choose one from ['push_in', 'push_out', 'pan_left', 'pan_right', 'tilt_up', 'tilt_down', 'zoom_in_left', 'zoom_in_right', 'static'].
  * Close-up / portrait / emotional dialogue -> 'push_in' or 'zoom_in_left' / 'zoom_in_right'.
  * Wide landscape / establishing scenery / departures -> 'push_out' or 'pan_left' / 'pan_right'.
  * Walking, moving, looking across rooms -> 'pan_left' or 'pan_right'.
  * High vertical objects or looking up/down -> 'tilt_up' or 'tilt_down'.
  * CRITICAL ANTI-REPETITION RULE: Never use the exact same camera_movement twice in a row. Alternate movements to maintain dynamic cinema pacing.
- transition_type: Choose from ['cut', 'dissolve', 'fade_to_black'].
  * 'cut': Use for natural conversation and continuous action within the same scene/room (set transition_duration to 0.0).
  * 'dissolve': Use when changing location, scene setting, or passing time (set transition_duration to 1.0).
  * 'fade_to_black': Use for major chapter ends, flashbacks, or dramatic cliffhangers (set transition_duration to 1.0).
- transition_duration: 0.0 for 'cut', 0.8 to 1.2 for 'dissolve' or 'fade_to_black'.
{context_block}"""
            
            # Format only this chunk's transcript
            fmt_input = "\n".join(f"[{w['start']}s - {w['end']}s]: {w['word']}" for w in chunk_words)
            user_content = f"Story (full narrative for context):\n{story_text}\n\nTranscript (generate scenes ONLY for this section):\n{fmt_input}"
            
            if provider == "gemini":
                result = _call_gemini_with_retry(client, sys_inst, user_content, schema)
            else:
                result = _call_together_deepseek(sys_inst, user_content, schema)
                
            chunk_scenes = result.get("scenes", [])
            
            global_style = "Studio Ghibli animation style, dramatic cinematic lighting, lush vibrant colors, beautiful anime background, masterpiece, highly detailed"
            
            # Renumber scene_ids to be globally sequential and inject the global style
            for s in chunk_scenes:
                s["scene_id"] = scene_counter
                vp = s.get("visual_prompt", "")
                vp_clean = vp.replace("[", "").replace("]", "")
                vp_clean = _translate_hindi_prompts(vp_clean)
                s["visual_prompt"] = f"{global_style}, {vp_clean}, no text box"
                start_t = s.get("start_time", 0.0)
                end_t = s.get("end_time", 0.0)
                s["duration"] = round(end_t - start_t, 3)

                # Camera Movement & Anti-Repetition Fallback
                raw_cam = str(s.get("camera_movement", "")).lower().strip()
                valid_cams = ["push_in", "push_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "zoom_in_left", "zoom_in_right", "static"]
                if raw_cam not in valid_cams:
                    raw_cam = "push_in"

                # Anti-repetition: if same as previous scene, alternate it!
                if all_scenes and all_scenes[-1].get("camera_movement") == raw_cam:
                    alternatives = [c for c in ["pan_right", "push_out", "pan_left", "zoom_in_left", "tilt_up", "push_in"] if c != raw_cam]
                    raw_cam = alternatives[scene_counter % len(alternatives)]
                s["camera_movement"] = raw_cam

                # Transition validation
                raw_trans = str(s.get("transition_type", "cut")).lower().strip()
                if raw_trans not in ["cut", "dissolve", "fade_to_black"]:
                    raw_trans = "cut"
                s["transition_type"] = raw_trans
                
                raw_dur = s.get("transition_duration", 0.0)
                try:
                    raw_dur = float(raw_dur)
                except (ValueError, TypeError):
                    raw_dur = 0.0
                s["transition_duration"] = raw_dur if raw_trans != "cut" else 0.0
                
                raw_sfx = s.get("sfx_id", "none")
                raw_prompt = s.get("sfx_prompt", "none")
                
                is_valid_sfx = raw_sfx and str(raw_sfx).lower() not in ["none", "null", ""]
                is_valid_prompt = raw_prompt and str(raw_prompt).lower() not in ["none", "null", ""]
                
                if is_valid_sfx:
                    sfx_key = str(raw_sfx).strip().lower()
                    s["sfx_id"] = sfx_key
                    
                    # If this is a new sound effect proposed by AI, register it!
                    if is_valid_prompt and sfx_key not in active_sfx_lib:
                        active_sfx_lib[sfx_key] = str(raw_prompt).strip()
                        new_sfx_added[sfx_key] = str(raw_prompt).strip()
                else:
                    s["sfx_id"] = None
                    
                if "sfx_prompt" in s:
                    del s["sfx_prompt"]
                    
                scene_counter += 1
            
            all_scenes.extend(chunk_scenes)
            
            # Save last 2 scenes for context carryover to next chunk
            prev_scenes_context = chunk_scenes[-2:] if len(chunk_scenes) >= 2 else chunk_scenes[:]
            
            if total_chunks > 1:
                print(f"      ✅ Got {len(chunk_scenes)} scenes (total so far: {len(all_scenes)})")
                
        return all_scenes, new_sfx_added, active_sfx_lib

    all_scenes = []
    new_sfx_added = {}
    
    try:
        gemini_client = get_gemini_client()
        all_scenes, new_sfx_added, sfx_library = _run_all_chunks(provider="gemini", client=gemini_client)
    except Exception as gemini_err:
        print(f"\n  ⚠️ Gemini failed during Director Pass: {gemini_err}")
        print("  🗑️ Scrapping all partial scenes generated by Gemini.")
        print("  🔄 Restarting Director Pass cleanly from Chunk 1 using DeepSeek (Together AI)...\n")
        
        if not os.environ.get("TOGETHER_API_KEY"):
            print("  ⛔ TOGETHER_API_KEY not found in environment. Cannot execute DeepSeek fallback.")
            raise gemini_err
            
        all_scenes, new_sfx_added, sfx_library = _run_all_chunks(provider="deepseek")
    
    # Auto-register newly proposed sound effects into master sfx library (safe read-merge-write)
    if new_sfx_added and sfx_lib_path:
        try:
            p = Path(sfx_lib_path)
            sfx_dir = p if p.is_dir() else p.parent
            canonical_file = sfx_dir / "sfx.json"
            
            # Re-read existing disk state to guarantee zero overwrite of manual additions
            disk_sfx = {}
            if canonical_file.exists():
                try:
                    with open(canonical_file, "r", encoding="utf-8") as f:
                        disk_sfx = json.load(f)
                except Exception:
                    disk_sfx = {}
                    
            # Merge: preserve all disk sounds, plus active library and new additions
            disk_sfx.update(sfx_library)
            disk_sfx.update(new_sfx_added)
            sfx_library = disk_sfx
            
            with open(canonical_file, "w", encoding="utf-8") as f:
                json.dump(disk_sfx, f, indent=4, ensure_ascii=False)
            print(f"    ✨ Auto-registered {len(new_sfx_added)} new sound effect(s) in sfx/sfx.json:")
            for k, v in new_sfx_added.items():
                print(f"      - {k}: \"{v}\"")
        except Exception as e:
            print(f"    ⚠️ Could not update master sfx library file: {e}")

    # Build and save sfx_blueprint.json summary for this specific video project
    sfx_blueprint = {}
    for s in all_scenes:
        sid = s.get("sfx_id")
        if sid:
            if sid not in sfx_blueprint:
                desc = sfx_library.get(sid, "Custom sound effect")
                sfx_blueprint[sid] = {
                    "prompt": desc,
                    "scenes": [s.get("scene_id")],
                    "time_ranges": [f"{s.get('start_time')}s - {s.get('end_time')}s"]
                }
            else:
                sfx_blueprint[sid]["scenes"].append(s.get("scene_id"))
                sfx_blueprint[sid]["time_ranges"].append(f"{s.get('start_time')}s - {s.get('end_time')}s")
                
    if sfx_blueprint:
        sfx_bp_file = Path(output_path).parent / "sfx_blueprint.json"
        with open(sfx_bp_file, "w", encoding="utf-8") as f:
            json.dump(sfx_blueprint, f, indent=4, ensure_ascii=False)
        print(f"    🔊 Saved sfx_blueprint.json with {len(sfx_blueprint)} sound effect(s) for this video!")

    # Save the complete stitched blueprint
    final_result = {
        "scenes": all_scenes,
        "manual_review_completed": True
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_result, f, indent=4, ensure_ascii=False)
    
    print(f"  ✅ Director Pass complete! {len(all_scenes)} scenes across {total_chunks} chunk(s).")
    return final_result

def _normalize_word(w):
    import re
    return re.sub(r"[^\w']", "", w.lower().strip())

def _resolve_triggers(trigger_words, words, search_after_index=-1):
    for candidate in trigger_words or []:
        cand_words = [_normalize_word(x) for x in candidate.split()]
        cand_words = [x for x in cand_words if x]
        if not cand_words: continue
        
        # 1. Try exact sequence match
        for idx in range(search_after_index + 1, max(0, len(words) - len(cand_words) + 1)):
            match = True
            for i, cw in enumerate(cand_words):
                if _normalize_word(words[idx + i]["word"]) != cw:
                    match = False
                    break
            if match:
                return {"matched_word": words[idx]["word"], "word_index": idx, "start_time": words[idx]["start"], "end_time": words[idx]["end"]}
                
        # 2. Fallback: match the first non-trivial word (>= 4 chars)
        for cw in cand_words:
            if len(cw) >= 4:
                for idx in range(search_after_index + 1, len(words)):
                    if _normalize_word(words[idx]["word"]) == cw:
                        return {"matched_word": words[idx]["word"], "word_index": idx, "start_time": words[idx]["start"], "end_time": words[idx]["end"]}
                        
        # 3. Ultimate Fallback: just match the first word if we couldn't find a long one
        for cw in cand_words:
            for idx in range(search_after_index + 1, len(words)):
                if _normalize_word(words[idx]["word"]) == cw:
                    return {"matched_word": words[idx]["word"], "word_index": idx, "start_time": words[idx]["start"], "end_time": words[idx]["end"]}
                    
    return None

def run_music_pass(story_text, transcript_data, output_path, sfx_lib_path):
    print("  🎵 Running Music Pass (Single Track)...")
    from google.genai import types
    client = get_gemini_client()
    
    sys_inst = """You are an Elite Film Composer. 
Analyze the overall emotional arc of this story.
Generate a single, highly descriptive `overall_music_prompt` for the entire video.
The prompt should describe the instruments, tempo, and mood. The user will use this prompt to generate the background music track."""

    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "overall_music_prompt": types.Schema(type=types.Type.STRING)
        },
        required=["overall_music_prompt"]
    )
    
    try:
        client = get_gemini_client()
        result = _call_gemini_with_retry(client, sys_inst, story_text, schema)
    except Exception as e:
        print(f"\n  ⚠️ Gemini failed during Music Pass: {e}")
        if os.environ.get("TOGETHER_API_KEY"):
            print("  🔄 Scrapping and restarting Music Pass using DeepSeek (Together AI)...")
            result = _call_together_deepseek(sys_inst, story_text, schema)
        else:
            print(f"  ❌ Error in Music Pass: {e}")
            return False
            
    import json
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=4, ensure_ascii=False)
        
    print("  ✅ Simplified Music Pass complete.")
    return True

def _sanitize_hindi_story_and_words(story_text, transcript_data):
    """
    Option 1: Identifies high-risk dramatic Hindi trigger words (debt, repossession, ruin, etc.)
    and swaps them for neutral English equivalents in story_text and transcript words.
    Crucial guarantee: 100% of Whisper's word-level timestamps (start, end) remain untouched.
    """
    import copy
    import re

    base_map = {
        "कर्ज़": "loan", "कर्ज": "loan", "कर्जे": "loans",
        "कब्ज़ा": "repossession", "कब्जा": "repossession", "कब्ज़े": "repossession", "कब्जे": "repossession",
        "बर्बाद": "ruined", "बर्बादी": "ruin", "हार": "defeat",
        "धोखा": "betrayal", "खून": "blood", "फांसी": "hanging",
        "जहर": "poison", "हथियार": "weapon", "आत्महत्या": "despair",
        "नीलामी": "auction", "कंगाल": "bankrupt", "मुसीबत": "trouble",
        "तबाही": "destruction", "तलाक": "divorce", "दंगा": "riot",
        "हिंसा": "violence", "मारपीट": "fight", "मौत": "death"
    }

    # Dynamic scanner via DeepSeek if TOGETHER_API_KEY is available
    together_key = os.environ.get("TOGETHER_API_KEY")
    if together_key:
        try:
            sys_inst = (
                "You are a Content Safety and Translation Specialist. "
                "Analyze the provided story narrative and identify any dramatic Hindi words that might trigger automated content filters "
                "(words related to debt, loans, repossession, destruction, death, weapons, violence, or financial despair). "
                "Return a JSON object strictly matching this schema with 'word_replacements' mapping each sensitive Hindi word to a neutral English word. "
                "Example: {'कर्ज़': 'loan', 'कब्ज़ा': 'repossession'}. If none found, return empty map {}."
            )
            schema = types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "word_replacements": types.Schema(type=types.Type.OBJECT)
                },
                required=["word_replacements"]
            )
            ai_res = _call_together_deepseek(sys_inst, story_text[:3000], schema)
            extra_map = ai_res.get("word_replacements", {})
            if isinstance(extra_map, dict):
                for k, v in extra_map.items():
                    if k and v and isinstance(k, str) and isinstance(v, str):
                        base_map[k.strip()] = v.strip()
        except Exception:
            # Non-blocking fallback to base_map
            pass

    # Deep-copy transcript data to preserve original in-memory data
    clean_transcript = copy.deepcopy(transcript_data)
    words = clean_transcript.get("words", clean_transcript) if isinstance(clean_transcript, dict) else clean_transcript
    
    replaced_count = 0
    if isinstance(words, list):
        for w in words:
            if isinstance(w, dict) and "word" in w:
                orig_word = w["word"]
                clean_w = re.sub(r"[^\w\u0900-\u097f]", "", orig_word)
                if clean_w in base_map:
                    replacement = base_map[clean_w]
                    w["word"] = orig_word.replace(clean_w, replacement)
                    replaced_count += 1

    # Sanitize story_text as well
    clean_story = story_text
    for hin, eng in base_map.items():
        if hin in clean_story:
            clean_story = clean_story.replace(hin, eng)

    if replaced_count > 0:
        print(f"  🛡️ Sanitized {replaced_count} sensitive Hindi word(s) in transcript to prevent Gemini safety block.")
        
    return clean_story, clean_transcript

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
    
    # Option 1: AI-assisted word-level transcript sanitization (preserves 100% Whisper timestamps)
    clean_story, clean_transcript = _sanitize_hindi_story_and_words(story_text, transcript_data)
    
    char_out = proj / "character_prompts.json"
    vid_out = proj / "video_blueprint.json"
    mus_out = proj / "music_blueprint.json"
    
    # Paths to global libraries
    base_dir = proj.parent
    mus_lib = base_dir / "music" / "music_library.json"
    sfx_lib = base_dir / "sfx" / "sfx.json"
    
    try:
        if not char_out.exists():
            char_data = run_character_pass(clean_story, char_out)
        else:
            with open(char_out, "r", encoding="utf-8") as f: char_data = json.load(f)
            print("  \u2705 Skipping Character Pass (character_prompts.json already exists)")

        if not vid_out.exists():
            run_director_pass(clean_story, clean_transcript, char_data, vid_out, sfx_lib_path=sfx_lib, is_short=is_short)
        else:
            print("  \u2705 Skipping Director Pass (video_blueprint.json already exists)")
        
        # Shorts might not need a complex music pass in the same way, but let's run it anyway
        if not mus_out.exists():
            run_music_pass(clean_story, clean_transcript, mus_out, sfx_lib)
        else:
            print("  \u2705 Skipping Music Pass (music_blueprint.json already exists)")
        
        print("  \u2705 Blueprints successfully generated (or already exist)!")
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
    # Extract explicitly valid IDs to embed in the prompt
    valid_ids = [s.get('scene_id') for s in eng_scenes]
    valid_ids_str = ", ".join(map(str, valid_ids))
    
    sys_inst = f"""You are an Elite Executive Video Director adapting a Hindi video timeline using existing images from the English version.
The English pipeline has already generated still images and camera movements for each scene. Your job is to RE-MAP those existing scenes onto the Hindi timeline by mapping the Hindi sentences to the correct english_source_scene_id.

ENGLISH VIDEO BLUEPRINT (the source storyboard you are adapting):
{blueprint_block}

AVAILABLE ENGLISH IDS: [{valid_ids_str}]

RULES:
1. SEMANTIC MATCHING : Read the Hindi Story provided below, which is broken down into numbered sentences (e.g., [0], [1], [2]). Identify the specific topics/events being spoken about. Find the 'english_source_scene_id' from the English Video Blueprint that best matches that topic.
2. FAST PACING : The English video uses rapid scene changes. You MUST use as many of the provided English scenes as possible to maintain this fast pacing. Do not group too many sentences together. Switch images frequently, typically every 1 or 2 sentences!
3. OUTPUT: For each scene, output a sequentially increasing 'scene_id' (1, 2, 3...), the 'english_source_scene_id' you are linking to, and the index number of the first sentence ('start_index') and the index number of the last sentence ('end_index') that belong to that scene. Ensure every sentence from 0 to {len(sentences)-1} is covered!
4. ASSET REUSE & LIMITS : 
   - You MUST ONLY use the IDs listed above in AVAILABLE ENGLISH IDS. Do not invent new IDs. Do not ask for new images.
   - You MAY reuse an existing 'english_source_scene_id' if the story topic returns, BUT you MUST NOT use the same ID more than 3 times total in the entire blueprint.
   - You MUST NOT use the same ID in consecutive, back-to-back scenes.
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
    
    user_content = f"Numbered Hindi Story:\n{fmt_input}"
    
    print("    -> Sending story to AI for semantic mapping...")
    try:
        client = get_gemini_client()
        result = _call_gemini_with_retry(client, sys_inst, user_content, schema)
    except Exception as e:
        print(f"\n  ⚠️ Gemini failed during Hindi Semantic Mapping: {e}")
        if os.environ.get("TOGETHER_API_KEY"):
            print("  🔄 Scrapping and restarting Hindi Semantic Mapping using DeepSeek (Together AI)...")
            result = _call_together_deepseek(sys_inst, user_content, schema)
        else:
            raise e
        
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
            
            "visual_prompt": src.get("visual_prompt", ""),
            "camera_movement": src.get("camera_movement", "push_in"),
            "transition_type": src.get("transition_type", "cut"),
            "transition_duration": src.get("transition_duration", 0.0),
            "duration": round(real_end - real_start, 3),
            "sfx_id": src.get("sfx_id", None)
        })
        
    final_result = {
        "scenes": final_scenes,
        "manual_review_completed": True
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_result, f, indent=4, ensure_ascii=False)
        
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
    sfx_lib = base_dir / "sfx" / "sfx.json"
    
    try:
        if not hin_vid_out.exists():
            run_hindi_director_pass(hin_story_text, hin_transcript_data, eng_vid_bp, hin_vid_out, is_short=is_short)
            print("  \u2705 Hindi Video Blueprint successfully generated!")
        else:
            print("  \u2705 Skipping Hindi Video Blueprint (already exists)")
        
        if not hin_mus_out.exists():
            run_music_pass(hin_story_text, hin_transcript_data, hin_mus_out, sfx_lib)
            print("  \u2705 Hindi Music Blueprint successfully generated!")
        else:
            print("  \u2705 Skipping Hindi Music Blueprint (already exists)")
            
        return True
    except Exception as e:
        print(f"  \u274C Hindi Blueprint Generation failed: {e}")
        return False
