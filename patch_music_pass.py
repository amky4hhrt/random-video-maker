import re

with open("/home/ankit-poddar/Documents/Video_Projects/py_files/ai_generator.py", "r") as f:
    content = f.read()

# Replace run_music_pass
start_str = "def run_music_pass("
end_str = "def generate_blueprints("

start_idx = content.find(start_str)
end_idx = content.find(end_str)

new_func = """def run_music_pass(story_text, transcript_data, output_path, sfx_lib_path):
    print("  🎵 Running Music Pass (Single Track)...")
    from google.genai import types
    client = get_gemini_client() if AI_PROVIDER == "gemini" else None
    
    sys_inst = \"\"\"You are an Elite Film Composer. 
Analyze the overall emotional arc of this story.
Generate a single, highly descriptive `overall_music_prompt` for the entire video.
The prompt should describe the instruments, tempo, and mood. The user will use this prompt to generate the background music track.\"\"\"

    schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "overall_music_prompt": types.Schema(type=types.Type.STRING)
        },
        required=["overall_music_prompt"]
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=story_text,
            config=types.GenerateContentConfig(
                system_instruction=sys_inst,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.7
            )
        )
        
        import json
        result = json.loads(response.text)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=4)
            
        print("  ✅ Simplified Music Pass complete.")
        return True
    except Exception as e:
        print(f"  ❌ Error in Music Pass: {e}")
        return False

"""

new_content = content[:start_idx] + new_func + content[end_idx:]

with open("/home/ankit-poddar/Documents/Video_Projects/py_files/ai_generator.py", "w") as f:
    f.write(new_content)
