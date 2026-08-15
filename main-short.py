import os
import sys
import glob
from pathlib import Path
from py_files.transcriber import generate_transcript
from py_files.ai_generator import generate_blueprints, generate_hindi_blueprints
from py_files.renderer_short import render_short_video

BASE_DIR = Path(__file__).resolve().parent
ENG_ASSETS = BASE_DIR / "english_short_assets"
HIN_ASSETS = BASE_DIR / "hindi_short_assets"
ENG_OUT = BASE_DIR / "output" / "english_short"
HIN_OUT = BASE_DIR / "output" / "hindi_short"
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
    print("📱 UNIFIED SHORT-FORM VIDEO MAKER")
    print("=" * 50)
    
    if not has_images(ENG_ASSETS):
        print("\nPhase 1: Generation (No images found in english_short_assets)")
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
        if not generate_blueprints(str(ENG_ASSETS), is_short=True):
            print("❌ Blueprint generation failed.")
            sys.exit(1)
            
        print("\n✅ Phase 1 Complete!")
        print("NEXT STEPS:")
        print("1. Review english_short_assets/character_prompts.json and video_blueprint.json")
        print("2. Generate 9:16 images and save them in english_short_assets/ with scene IDs (e.g. 1.jpg)")
        print("3. Optional: Add required music/SFX files to music/ and sfx/ folders")
        print("4. Optional: Place Hindi story.txt and voiceover.* in hindi_short_assets/")
        print("5. Run this script again to Render!")
    else:
        print("\nPhase 2: Render (Images found in english_short_assets)")
        print("-" * 50)
        
        # Render English
        en_final = ENG_OUT / "final_en_short.mp4"
        en_ok = True
        if not en_final.exists():
            en_ok = render_short_video(str(ENG_ASSETS), str(ENG_ASSETS), str(ENG_OUT), str(MUSIC_DIR), str(SFX_DIR), language="en")
            if not en_ok:
                print("  \u274C English render FAILED \u2014 see errors above.")
            elif not en_final.exists():
                print(f"  \u274C English render reported success but {en_final} is missing!")
                en_ok = False
            else:
                print(f"  \u2705 English render confirmed on disk: {en_final}")
        else:
            print(f"  \u2705 English video already rendered ({en_final.name}). Skipping.")
        
        # Check if Hindi is ready
        hin_story = HIN_ASSETS / "story.txt"
        hin_vo = glob.glob(str(HIN_ASSETS / "voiceover.*"))
        
        if hin_story.exists() and hin_vo:
            print("\n\U0001F1EE\U0001F1F3 Hindi assets detected! Preparing Hindi render...")
            
            hin_final = HIN_OUT / "final_hi_short.mp4"
            hin_ok = True
            if hin_final.exists():
                print(f"  \u2705 Hindi video already rendered ({hin_final.name}). Skipping.")
            else:
                hin_transcript = HIN_ASSETS / "transcript.json"
                if not hin_transcript.exists():
                    print("\n\U0001F4DD Generating Hindi Transcript...")
                    hin_audio = get_audio_file(str(HIN_ASSETS))
                    if not hin_audio or not generate_transcript(hin_audio, str(hin_story), str(hin_transcript), language="hi"):
                        print("\u274C Hindi transcription failed.")
                        hin_ok = False
                
                hin_vid_bp = HIN_ASSETS / "video_blueprint.json"
                if hin_transcript.exists() and not hin_vid_bp.exists():
                    print("\n\U0001F9E0 Generating Hindi Video Blueprint (Adapting from English)...")
                    generate_hindi_blueprints(str(ENG_ASSETS), str(HIN_ASSETS), is_short=True)
                    
                if hin_transcript.exists() and hin_vid_bp.exists():
                    hin_ok = render_short_video(str(HIN_ASSETS), str(HIN_ASSETS), str(HIN_OUT), str(MUSIC_DIR), str(SFX_DIR), language="hi", image_dir=str(ENG_ASSETS))
                    if not hin_ok:
                        print("  \u274C Hindi render FAILED \u2014 see errors above.")
                    elif not hin_final.exists():
                        print(f"  \u274C Hindi render reported success but {hin_final} is missing!")
                        hin_ok = False
                    else:
                        print(f"  \u2705 Hindi render confirmed on disk: {hin_final}")
                elif not (hin_transcript.exists() and hin_vid_bp.exists()):
                    print("  \u274C Hindi render skipped \u2014 transcript or video_blueprint.json was never produced.")
                    hin_ok = False
        else:
            hin_ok = True
            print("\nℹ️ No Hindi story.txt or voiceover found in hindi_short_assets. Skipping Hindi render.")

        if en_ok and hin_ok:
            print("\n✅ All Renders Complete!")
        else:
            print("\n\u274C One or more renders FAILED \u2014 scroll up for the specific error.")
            sys.exit(1)

if __name__ == "__main__":
    main()
