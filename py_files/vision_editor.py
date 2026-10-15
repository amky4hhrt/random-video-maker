import os
import re
import json
import glob
from pathlib import Path
from PIL import Image
import subprocess
import torch

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")

_MODEL = None
_PROCESSOR = None

def get_qwen_model_and_processor():
    """Lazy-loads and caches Qwen2-VL-2B-Instruct on the available GPU (or CPU)."""
    global _MODEL, _PROCESSOR
    if _MODEL is not None and _PROCESSOR is not None:
        return _MODEL, _PROCESSOR

    from transformers import Qwen2VLForConditionalGeneration, AutoProcessor

    model_id = "Qwen/Qwen2-VL-2B-Instruct"
    print(f"  🧠 Loading local vision model '{model_id}'...")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16 if torch.cuda.is_available() else torch.float32

    _PROCESSOR = AutoProcessor.from_pretrained(model_id)
    _MODEL = Qwen2VLForConditionalGeneration.from_pretrained(
        model_id,
        torch_dtype=dtype,
        device_map="auto" if device == "cuda" else None
    )
    if device == "cpu":
        _MODEL.to("cpu")

    _MODEL.eval()
    print("  ✅ Local Qwen2-VL model loaded successfully!")
    return _MODEL, _PROCESSOR

def find_asset(input_dir, base_name):
    for ext in IMAGE_EXTS + VIDEO_EXTS:
        exact = os.path.join(input_dir, f"{base_name}{ext}")
        if os.path.exists(exact): return exact
        matches = glob.glob(os.path.join(input_dir, f"{base_name}[_ \\-\\.]*{ext}"))
        if matches: return matches[0]
    return None

def _extract_representative_image(asset_path, temp_prefix):
    """Returns a PIL Image object representing the asset (or start/end for video)."""
    ext = os.path.splitext(asset_path)[1].lower()
    if ext in VIDEO_EXTS:
        frame_jpg = f"{temp_prefix}.jpg"
        subprocess.run(["ffmpeg", "-y", "-i", asset_path, "-vframes", "1", "-q:v", "2", frame_jpg],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(frame_jpg):
            return Image.open(frame_jpg).convert("RGB")
        return None
    else:
        return Image.open(asset_path).convert("RGB")

def _parse_qwen_json(text):
    """Safely extracts JSON object from Qwen response text."""
    try:
        match = re.search(r"\{.*?\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except Exception:
        pass
    return None

VALID_MOVEMENTS = ["push_in", "push_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
                   "zoom_in_left", "zoom_in_right", "zoom_out_left", "zoom_out_right", "static"]
VALID_TRANSITIONS = ["cut", "dissolve", "flashback_fade", "fade_to_black"]

def run_vision_pass(project_dir):
    print("\n👁️📹 Running Local Qwen2-VL Vision Editing Pass (100% Free)...")
    proj = Path(project_dir)
    vid_bp_path = proj / "video_blueprint.json"

    if not vid_bp_path.exists():
        print("  ❌ No video_blueprint.json found!")
        return False

    with open(vid_bp_path, "r", encoding="utf-8") as f:
        vid_bp = json.load(f)

    scenes = vid_bp.get("video_blueprint", []) or vid_bp.get("scenes", [])
    if not scenes:
        print("  ❌ No scenes found in blueprint!")
        return False

    # Check if vision pass has already been run
    if "camera_movement" in scenes[0] and scenes[0].get("camera_movement") != "push_in":
        print("  ✅ Vision Pass already completed (camera movements detected). Skipping.")
        return True

    try:
        from qwen_vl_utils import process_vision_info
    except ImportError:
        print("  ⚠️ 'qwen-vl-utils' not installed. Please run: pip install qwen-vl-utils")

    try:
        model, processor = get_qwen_model_and_processor()
    except Exception as e:
        print(f"  ❌ Failed to load local Qwen2-VL model: {e}")
        print("  ⚠️ Ensure transformers>=4.45.0, accelerate, and torchvision are installed.")
        return False

    temp_cleanup_files = []

    try:
        print(f"  🔍 Analyzing {len(scenes)} scenes locally with Qwen2-VL...")

        for i, s in enumerate(scenes):
            sid = s.get("scene_id")
            asset_path = find_asset(str(proj), str(sid))
            vp = s.get("visual_prompt", "")

            # Check next scene for transition context
            next_sid = scenes[i + 1].get("scene_id") if i + 1 < len(scenes) else None
            next_asset_path = find_asset(str(proj), str(next_sid)) if next_sid else None

            curr_img = None
            next_img = None

            if asset_path:
                temp_prefix = os.path.join(str(proj), f"temp_qwen_{sid}")
                curr_img = _extract_representative_image(asset_path, temp_prefix)
                if os.path.exists(f"{temp_prefix}.jpg"):
                    temp_cleanup_files.append(f"{temp_prefix}.jpg")

            if next_asset_path:
                temp_prefix_next = os.path.join(str(proj), f"temp_qwen_{next_sid}")
                next_img = _extract_representative_image(next_asset_path, temp_prefix_next)
                if os.path.exists(f"{temp_prefix_next}.jpg"):
                    temp_cleanup_files.append(f"{temp_prefix_next}.jpg")

            # Fallback if image missing
            if not curr_img:
                s["camera_movement"] = "push_in"
                s["transition_type"] = "cut"
                s["transition_duration"] = 0.0
                if "duration" not in s:
                    s["duration"] = round(s.get("end_time", 0.0) - s.get("start_time", 0.0), 3)
                continue

            # Build messages for Qwen2-VL
            prompt_text = (
                "You are an expert film director and editor. Analyze this scene image.\n"
                f"Scene Narrative: {vp}\n\n"
                "Rules:\n"
                "1. camera_movement: Choose one from ['push_in', 'push_out', 'pan_left', 'pan_right', 'tilt_up', 'tilt_down', 'static'].\n"
                "   - If the main subject is positioned on the LEFT side of the frame, use 'pan_left' or 'push_in'.\n"
                "   - If the main subject is on the RIGHT side, use 'pan_right'.\n"
                "   - If it is a portrait close-up, use 'push_in'.\n"
                "   - If it is a wide scenery/establishing landscape, use 'push_out'.\n"
                "2. transition_type: Choose one from ['cut', 'dissolve', 'fade_to_black'].\n"
                "   - Use 'cut' for natural narrative flow.\n"
                "   - Use 'fade_to_black' or 'dissolve' only if there is a dramatic change in setting, time, or lighting.\n"
                "3. transition_duration: 0.0 for cut, 0.8 to 1.2 for dissolve/fade.\n\n"
                "Respond ONLY with a JSON object. Example:\n"
                '{"camera_movement": "push_in", "transition_type": "cut", "transition_duration": 0.0}'
            )

            user_content = [{"type": "text", "text": prompt_text}, {"type": "image", "image": curr_img}]
            if next_img and (i + 1 < len(scenes)):
                user_content.append({"type": "text", "text": "Next scene image (for evaluating transition):"})
                user_content.append({"type": "image", "image": next_img})

            messages = [{"role": "user", "content": user_content}]

            text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            from qwen_vl_utils import process_vision_info
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt"
            )
            inputs = inputs.to(model.device)

            with torch.no_grad():
                generated_ids = model.generate(**inputs, max_new_tokens=90, do_sample=False)
                generated_ids_trimmed = [
                    out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
                ]
                output_text = processor.batch_decode(
                    generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
                )[0]

            parsed = _parse_qwen_json(output_text)
            if parsed:
                mov = str(parsed.get("camera_movement", "push_in")).lower()
                trans = str(parsed.get("transition_type", "cut")).lower()
                dur = float(parsed.get("transition_duration", 0.0))

                s["camera_movement"] = mov if mov in VALID_MOVEMENTS else "push_in"
                s["transition_type"] = trans if trans in VALID_TRANSITIONS else "cut"
                s["transition_duration"] = dur if s["transition_type"] != "cut" else 0.0
            else:
                s["camera_movement"] = "push_in"
                s["transition_type"] = "cut"
                s["transition_duration"] = 0.0

            if "duration" not in s:
                s["duration"] = round(s.get("end_time", 0.0) - s.get("start_time", 0.0), 3)

            if (i + 1) % 10 == 0 or (i + 1) == len(scenes):
                print(f"    ✓ Processed {i + 1}/{len(scenes)} scenes...")

        # Re-save blueprint with updated camera & transitions
        if "video_blueprint" in vid_bp:
            vid_bp["video_blueprint"] = scenes
        else:
            vid_bp["scenes"] = scenes

        vid_bp["manual_review_completed"] = True

        with open(vid_bp_path, "w", encoding="utf-8") as f:
            json.dump(vid_bp, f, indent=4)

        print("  ✅ Local Qwen2-VL Vision Editing Pass complete! video_blueprint.json updated.")
        return True

    except Exception as e:
        print(f"  ❌ Vision Pass encountered an error: {e}")
        return False
    finally:
        for f in temp_cleanup_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
