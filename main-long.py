import os
import sys
import glob
from pathlib import Path
from py_files.transcriber import generate_transcript
from py_files.ai_generator import generate_blueprints, generate_hindi_blueprints
from py_files.renderer_long import render_long_video

BASE_DIR = Path(__file__).resolve().parent
ENG_ASSETS = BASE_DIR / "english_long_assets"
HIN_ASSETS = BASE_DIR / "hindi_long_assets"
ENG_OUT = BASE_DIR / "output" / "english_long"
HIN_OUT = BASE_DIR / "output" / "hindi_long"
MUSIC_DIR = BASE_DIR / "music"
SFX_DIR = BASE_DIR / "sfx"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

def has_images(dir_path):
    for ext in IMAGE_EXTS:
        if glob.glob(os.path.join(dir_path, f"*{ext}")):
            return True
    return False

def get_audio_file(dir_path):
    for ext in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"):
        files = glob.glob(os.path.join(dir_path, f"voiceover{ext}"))
        if files: return files[0]
    return None

def main():
    print("=" * 50)
    print("🎬 UNIFIED LONG-FORM VIDEO MAKER")
    print("=" * 50)
    
    bp_missing = not os.path.exists(os.path.join(str(ENG_ASSETS), "video_blueprint.json")) or not os.path.exists(os.path.join(str(ENG_ASSETS), "music_blueprint.json"))
    if not has_images(ENG_ASSETS) or bp_missing:
        print("\nPhase 1: Generation (Blueprints or Images missing)")
        print("-" * 50)
        
        # 1. English Transcript
        print("\n📝 Generating English Transcript...")
        audio_file = get_audio_file(str(ENG_ASSETS))
        story_file = os.path.join(str(ENG_ASSETS), "story.txt")
        out_json = os.path.join(str(ENG_ASSETS), "transcript.json")
        if os.path.exists(out_json):
            print("  \u2705 Transcript already exists. Skipping transcription.")
        else:
            if not audio_file or not generate_transcript(audio_file, story_file, out_json, language="en"):
                print("\u274C English transcription failed. Ensure story.txt and voiceover.* exist.")
                sys.exit(1)
        # 2. English Blueprints
        print("\n🧠 Generating English Blueprints...")
        if not generate_blueprints(str(ENG_ASSETS), is_short=False):
            print("❌ Blueprint generation failed.")
            sys.exit(1)
            
        print("\n✅ Phase 1 Complete!")
        print("NEXT STEPS:")
        print("1. Review english_long_assets/character_prompts.json and video_blueprint.json")
        print("2. Generate 16:9 images using the Google Flow Auto-Prompter extension")
        print("3. Save them in english_long_assets/ with scene IDs (e.g. 1.jpg, 2_image.jpg)")
        print("4. Generate the unique music tracks from music_blueprint.json and save them in english_long_assets/")
        print("5. Optional: Add required SFX files to the global sfx/ folder")
        print("6. Optional: Place Hindi story.txt and voiceover.* in hindi_long_assets/")
        print("7. Run this script again to Render!")
    else:
        print("\nPhase 2: Render (Images found in english_long_assets)")
        print("-" * 50)
        
        # from py_files.vision_editor import run_vision_pass
        # if not run_vision_pass(str(ENG_ASSETS)):
        #     print("❌ Vision Pass failed. Aborting Render.")
        #     sys.exit(1)
            
        from py_files.manual_review import run_manual_review
        bp_path = ENG_ASSETS / "video_blueprint.json"
        if not run_manual_review(str(bp_path)):
            print("❌ Manual Review failed. Aborting Render.")
            sys.exit(1)

        
        # Render English
        en_final = ENG_OUT / "final_en_long.mp4"
        if not en_final.exists():
            render_long_video(str(ENG_ASSETS), str(ENG_ASSETS), str(ENG_OUT), str(MUSIC_DIR), str(SFX_DIR), language="en")
        else:
            print(f"  \u2705 English video already rendered ({en_final.name}). Skipping.")
        
        # Check if Hindi is ready
        hin_story = HIN_ASSETS / "story.txt"
        hin_vo = glob.glob(str(HIN_ASSETS / "voiceover.*"))
        
        if hin_story.exists() and hin_vo:
            print("\n\U0001F1EE\U0001F1F3 Hindi assets detected! Preparing Hindi render...")
            
            hin_final = HIN_OUT / "final_hi_long.mp4"
            if hin_final.exists():
                print(f"  \u2705 Hindi video already rendered ({hin_final.name}). Skipping.")
            else:
                hin_transcript = HIN_ASSETS / "transcript.json"
                if not hin_transcript.exists():
                    print("\n\U0001F4DD Generating Hindi Transcript...")
                    hin_audio = get_audio_file(str(HIN_ASSETS))
                    if not hin_audio or not generate_transcript(hin_audio, str(hin_story), str(hin_transcript), language="hi"):
                        print("\u274C Hindi transcription failed.")
                
                hin_vid_bp = HIN_ASSETS / "video_blueprint.json"
                if hin_transcript.exists() and not hin_vid_bp.exists():
                    print("\n\U0001F9E0 Generating Hindi Video Blueprint (Adapting from English)...")
                    generate_hindi_blueprints(str(ENG_ASSETS), str(HIN_ASSETS), is_short=False)
                    
                if hin_transcript.exists() and hin_vid_bp.exists():
                    render_long_video(str(HIN_ASSETS), str(HIN_ASSETS), str(HIN_OUT), str(MUSIC_DIR), str(SFX_DIR), language="hi", image_dir=str(ENG_ASSETS))
        else:
            print("\nℹ️ No Hindi story.txt or voiceover found in hindi_long_assets. Skipping Hindi render.")
            
        print("\n✅ All Renders Complete!")

if __name__ == "__main__":
    main()
