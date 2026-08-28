import os
import json
import glob
from pathlib import Path
from google import genai
from google.genai import types
from PIL import Image
import subprocess

def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set.")
    return genai.Client(api_key=api_key)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")

def find_asset(input_dir, base_name):
    for ext in IMAGE_EXTS + VIDEO_EXTS:
        exact = os.path.join(input_dir, f"{base_name}{ext}")
        if os.path.exists(exact): return exact
        matches = glob.glob(os.path.join(input_dir, f"{base_name}[_ \\-\\.]*{ext}"))
        if matches: return matches[0]
    return None

def run_vision_pass(project_dir):
    print("\n\U0001F441\U0001F4F9 Running Vision Editing Pass...")
    proj = Path(project_dir)
    vid_bp_path = proj / "video_blueprint.json"
    
    if not vid_bp_path.exists():
        print("  \u274C No video_blueprint.json found!")
        return False
        
    with open(vid_bp_path, "r", encoding="utf-8") as f:
        vid_bp = json.load(f)
        
    scenes = vid_bp.get("video_blueprint", []) or vid_bp.get("scenes", [])
    if not scenes:
        print("  \u274C No scenes found in blueprint!")
        return False
        
    # Check if we've already run the vision pass by looking at the first scene
    if "camera_movement" in scenes[0]:
        print("  \u2705 Vision Pass already completed (camera movements detected). Skipping.")
        return True

    # Gather images and build the prompt contents
    contents = []
    
    sys_inst = """You are an Elite Video Editor. Your job is to assign cinematic camera movements and transitions to a sequence of images.
Look at the sequence of images carefully. Understand the visual composition of each one.
- If the subject is on the left, you might pan_left or push_in.
- If it's a tight close-up portrait, do not zoom too fast.
- If there is a drastic change in lighting or location between two scenes, use a 'dissolve' or 'fade_to_black' transition instead of a 'cut'.
- If the images flow naturally, use a 'cut'.

CRITICAL RULE FOR VIDEO FILES:
Some scenes might be provided as TWO images labeled "(Start of Video)" and "(End of Video)".
Even if there are two images for a scene, you MUST output EXACTLY ONE camera_movement and ONE transition_type for that Scene ID.
Use the 'Start' image to decide how the previous scene transitions into this one. Use the 'End' image to decide how this scene transitions into the next one, and use the 'End' image to decide the camera movement in case the video freezes.

Assign EXACTLY ONE camera movement and transition per scene. The schema uses strict ENUM values. You must select from them.
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
                        "camera_movement": types.Schema(type=types.Type.STRING, enum=["push_in", "push_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "zoom_in_left", "zoom_in_right", "zoom_out_left", "zoom_out_right", "static"]),
                        "transition_type": types.Schema(type=types.Type.STRING, enum=["cut", "dissolve", "flashback_fade", "fade_to_black"]),
                        "transition_duration": types.Schema(type=types.Type.NUMBER, description="Duration in seconds. Use 0.0 for cut. Use 0.8 to 1.5 for dissolves/fades."),
                    },
                    required=["scene_id", "camera_movement", "transition_type", "transition_duration"]
                )
            )
        },
        required=["scenes"]
    )
    
    print("  \U0001F4E4 Loading images and sending to Gemini Vision...")
    
    # We will upload the text script context and the PIL images in one big request
    prompt_text = "Here is the blueprint data mapping scene IDs to their story context:\n"
    
    for s in scenes:
        sid = s.get("scene_id")
        img_path = find_asset(str(proj), str(sid))
        
        prompt_text += f"\nScene ID {sid}: {s.get('visual_prompt', '')}"
        
        if img_path:
            try:
                img = Image.open(img_path)
                contents.append(f"Image for Scene ID: {sid}")
                contents.append(img)
            except Exception as e:
                print(f"  \u26A0\uFE0F Failed to load image {img_path}: {e}")
        else:
            print(f"  \u26A0\uFE0F Missing image for Scene ID: {sid}")
            
    contents.insert(0, prompt_text)
    
    client = get_gemini_client()
    
    try:
        response = client.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=sys_inst,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.4
            )
        )
        
        result = json.loads(response.text)
        vision_scenes = result.get("scenes", [])
        
        # Cleanup temp images
        for f in glob.glob(os.path.join(str(proj), "temp_*_*.jpg")):
            try: os.remove(f)
            except: pass

        # Merge results back into original blueprint
        vision_map = {s["scene_id"]: s for s in vision_scenes}
        
        for s in scenes:
            sid = s.get("scene_id")
            if sid in vision_map:
                v_data = vision_map[sid]
                s["camera_movement"] = v_data.get("camera_movement", "push_in")
                s["transition_type"] = v_data.get("transition_type", "cut")
                s["transition_duration"] = v_data.get("transition_duration", 0.0)
            else:
                # Fallbacks just in case
                s["camera_movement"] = "push_in"
                s["transition_type"] = "cut"
                s["transition_duration"] = 0.0
            
            if "duration" not in s:
                s["duration"] = round(s.get("end_time", 0.0) - s.get("start_time", 0.0), 3)
                
        # Re-save
        if "video_blueprint" in vid_bp:
            vid_bp["video_blueprint"] = scenes
        else:
            vid_bp["scenes"] = scenes
            
        with open(vid_bp_path, "w", encoding="utf-8") as f:
            json.dump(vid_bp, f, indent=4)
            
        print("  \u2705 Vision Editing Pass complete! video_blueprint.json updated.")
        return True
        
    except Exception as e:
        print(f"  \u274C Vision Pass failed: {e}")
        return False
