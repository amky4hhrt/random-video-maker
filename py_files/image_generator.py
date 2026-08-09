import json
import os
import time
import requests
import base64

def generate_missing_images(assets_dir):
    blueprint_path = os.path.join(assets_dir, "video_blueprint.json")
    
    if not os.path.exists(blueprint_path):
        print("❌ video_blueprint.json not found!")
        return False

    with open(blueprint_path, 'r') as f:
        try:
            blueprint = json.load(f)
        except json.JSONDecodeError:
            print("❌ Failed to parse video_blueprint.json!")
            return False

    missing_images = []
    scenes = blueprint.get('scenes', [])
    for scene in scenes:
        scene_id = scene.get('scene_id')
        img_path = os.path.join(assets_dir, f"{scene_id}.jpg")
        if not os.path.exists(img_path):
            missing_images.append(scene)
            
    if not missing_images:
        print("✅ All images already exist. Skipping generation.")
        return True
        
    api_key = os.environ.get("TOGETHER_API_KEY")
    if not api_key:
        print("❌ TOGETHER_API_KEY not found in environment variables!")
        print("💡 In Colab, run: os.environ['TOGETHER_API_KEY'] = 'your_api_key_here'")
        return False
        
    print(f"🔎 Found {len(missing_images)} missing images. Starting Together AI (FLUX) generation...\n")
    
    success = True
    url = "https://api.together.xyz/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    for scene in missing_images:
        scene_id = scene['scene_id']
        base_prompt = scene.get('visual_prompt', '')
        
        # Append a strong style instruction to force FLUX to output high-quality graphic novel art
        style_suffix = "High-quality graphic novel illustration, detailed comic book art style, dramatic cinematic lighting, gritty realism, masterpiece, highly detailed."
        prompt = f"{base_prompt}. {style_suffix}"
        
        print(f"Generating Scene {scene_id}...")
        
        payload = {
            "model": "black-forest-labs/FLUX.1-schnell",
            "prompt": prompt,
            "width": 1024,
            "height": 768,
            "steps": 4,
            "n": 1,
            "response_format": "b64_json"
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload)
            
            if response.status_code == 200:
                data = response.json()
                image_b64 = data["data"][0]["b64_json"]
                out_path = os.path.join(assets_dir, f"{scene_id}.jpg")
                with open(out_path, "wb") as f:
                    f.write(base64.b64decode(image_b64))
                print(f"  ✅ Saved {scene_id}.jpg")
            else:
                print(f"  ❌ Error {response.status_code}: {response.text}")
                success = False
                
        except Exception as e:
            print(f"  ❌ Exception generating Scene {scene_id}: {e}")
            success = False
            
        time.sleep(1) # Simple rate limit protection
            
    return success
