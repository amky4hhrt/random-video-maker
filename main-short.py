import os
import sys
import glob
import json
from pathlib import Path
from py_files.transcriber import generate_transcript
from py_files.ai_generator import generate_blueprints, generate_hindi_blueprints
from py_files.renderer_short import render_short_video
from py_files.manual_review import run_manual_review

BASE_DIR = Path(__file__).resolve().parent
ENG_ASSETS = BASE_DIR / "english_short_assets"
HIN_ASSETS = BASE_DIR / "hindi_short_assets"
ENG_OUT = BASE_DIR / "output" / "english_short"
HIN_OUT = BASE_DIR / "output" / "hindi_short"
MUSIC_DIR = BASE_DIR / "music"
SFX_DIR = BASE_DIR / "sfx"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov")

def has_assets(dir_path):
    for ext in IMAGE_EXTS + VIDEO_EXTS:
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
    
    vid_bp = ENG_ASSETS / "video_blueprint.json"
    mus_bp = ENG_ASSETS / "music_blueprint.json"
    char_json = ENG_ASSETS / "character_prompts.json"
    trans = ENG_ASSETS / "transcript.json"
    
    needs_blueprints = not (vid_bp.exists() and mus_bp.exists() and char_json.exists() and trans.exists())
    
    needs_manual_review = True
    if vid_bp.exists():
        with open(vid_bp, "r") as f:
            try:
                bp_data = json.load(f)
                if bp_data.get("manual_review_completed"):
                    needs_manual_review = False
            except json.JSONDecodeError:
                pass
                
    images_present = has_assets(str(ENG_ASSETS))
    
    if needs_blueprints:
        print("\nSTAGE 1: Generating Blueprints")
        print("-" * 50)
        
        print("\n📝 Generating English Transcript...")
        audio_file = get_audio_file(str(ENG_ASSETS))
        story_file = ENG_ASSETS / "story.txt"
        
        if not story_file.exists() or not audio_file:
            print("❌ Cannot proceed: story.txt and voiceover.* are required in english_short_assets/")
            sys.exit(1)
            
        if trans.exists():
            print("  ✅ Transcript already exists.")
        else:
            if not generate_transcript(audio_file, str(story_file), str(trans), language="en"):
                print("❌ English transcription failed.")
                sys.exit(1)
                
        print("\n🧠 Generating English Blueprints...")
        if not generate_blueprints(str(ENG_ASSETS), is_short=True):
            print("❌ Blueprint generation failed.")
            sys.exit(1)
            
        print("\n✅ STAGE 1 COMPLETE!")
        print("NEXT STEPS:")
        print("1. Review character_prompts.json and video_blueprint.json")
        print("2. Generate assets and save them in english_short_assets/ (e.g. 1.jpg)")
        print("3. Generate the unique music tracks from music_blueprint.json")
        print("4. Run this script again to proceed to Stage 2 (Manual Review)!")
        
    elif images_present and needs_manual_review:
        print("\nSTAGE 2: Manual Review")
        print("-" * 50)
        if not run_manual_review(str(vid_bp)):
            print("❌ Manual Review failed or was aborted.")
            sys.exit(1)
            
        print("\n✅ STAGE 2 COMPLETE! Proceeding to Stage 3 immediately...")
        needs_manual_review = False

    if images_present and not needs_manual_review:
        print("\nSTAGE 3: Render (Images and Manual Review Confirmed)")
        print("-" * 50)
        
        en_final = ENG_OUT / "final_en_short.mp4"
        en_ok = True
        if not en_final.exists():
            en_ok = render_short_video(str(ENG_ASSETS), str(ENG_ASSETS), str(ENG_OUT), str(MUSIC_DIR), str(SFX_DIR), language="en")
            if not en_ok:
                print("  ❌ English render FAILED — see errors above.")
            elif not en_final.exists():
                print(f"  ❌ English render reported success but {en_final} is missing!")
                en_ok = False
            else:
                print(f"  ✅ English render confirmed on disk: {en_final}")
        else:
            print(f"  ✅ English video already rendered ({en_final.name}). Skipping.")
        
        hin_story = HIN_ASSETS / "story.txt"
        hin_vo = glob.glob(str(HIN_ASSETS / "voiceover.*"))
        
        if hin_story.exists() and hin_vo:
            print("\n🇮🇳 Hindi assets detected! Preparing Hindi render...")
            
            hin_final = HIN_OUT / "final_hi_short.mp4"
            hin_ok = True
            if hin_final.exists():
                print(f"  ✅ Hindi video already rendered ({hin_final.name}). Skipping.")
            else:
                hin_transcript = HIN_ASSETS / "transcript.json"
                if not hin_transcript.exists():
                    print("\n📝 Generating Hindi Transcript...")
                    hin_audio = get_audio_file(str(HIN_ASSETS))
                    if not hin_audio or not generate_transcript(hin_audio, str(hin_story), str(hin_transcript), language="hi"):
                        print("❌ Hindi transcription failed.")
                        hin_ok = False
                
                hin_vid_bp = HIN_ASSETS / "video_blueprint.json"
                if hin_transcript.exists() and not hin_vid_bp.exists():
                    print("\n🧠 Generating Hindi Video Blueprint (Adapting from English)...")
                    generate_hindi_blueprints(str(ENG_ASSETS), str(HIN_ASSETS), is_short=True)
                    
                if hin_transcript.exists() and hin_vid_bp.exists():
                    hin_ok = render_short_video(str(HIN_ASSETS), str(HIN_ASSETS), str(HIN_OUT), str(MUSIC_DIR), str(SFX_DIR), language="hi", image_dir=str(ENG_ASSETS))
                    if not hin_ok:
                        print("  ❌ Hindi render FAILED — see errors above.")
                    elif not hin_final.exists():
                        print(f"  ❌ Hindi render reported success but {hin_final} is missing!")
                        hin_ok = False
                    else:
                        print(f"  ✅ Hindi render confirmed on disk: {hin_final}")
                elif not (hin_transcript.exists() and hin_vid_bp.exists()):
                    print("  ❌ Hindi render skipped — transcript or blueprint missing.")
                    hin_ok = False
        else:
            hin_ok = True
            print("\nℹ️ No Hindi story.txt or voiceover found. Skipping Hindi render.")

        if en_ok and hin_ok:
            print("\n✅ All Renders Complete!")
        else:
            print("\n❌ One or more renders FAILED — scroll up for the specific error.")
            sys.exit(1)

    elif not images_present and not needs_blueprints:
        print("\n⏸️ WAITING FOR ASSETS")
        print("-" * 50)
        print("Blueprints are generated, but no images/videos were found.")
        print("Please generate your images/videos and place them in the folder, then run this script again.")

if __name__ == "__main__":
    main()
