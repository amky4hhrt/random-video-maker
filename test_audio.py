#!/usr/bin/env python3
"""
Test Audio Mix Generator
Quickly tests and previews the full audio mix (Voiceover + Emotional Background Music + Sidechain Ducking + SFX)
in 3-5 seconds without requiring visual images or video rendering.

Saves the mixed audio file to:
1. output/test_audio.mp3
2. test_audio.mp3 (in project root on Google Drive)
"""

import os
import sys
import json
import shutil
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from py_files.renderer_long import generate_audio_mix, get_video_duration

def run_test_audio_mix(asset_dir=None):
    print("=" * 60)
    print("🎧 TESTING AUDIO MIX (VO + Emotional Music + Ducking + SFX)")
    print("=" * 60)

    # 1. Auto-detect asset directory if not provided
    candidates = [
        BASE_DIR / "hindi_long_assets",
        BASE_DIR / "hindi_short_assets",
        BASE_DIR / "english_long_assets",
        BASE_DIR / "english_short_assets",
    ]
    
    selected_dir = None
    if asset_dir:
        cand = Path(asset_dir)
        if cand.exists():
            selected_dir = cand
    else:
        for c in candidates:
            if (c / "audio.wav").exists() or (c / "audio.mp3").exists():
                selected_dir = c
                break

    if not selected_dir or not selected_dir.exists():
        print("❌ Could not find an asset folder with audio.wav or audio.mp3!")
        print(f"Checked: {[str(c.name) for c in candidates]}")
        return False

    print(f"📁 Selected Asset Folder: {selected_dir.name}")

    # 2. Locate Voiceover
    vo_path = selected_dir / "audio.wav"
    if not vo_path.exists():
        vo_path = selected_dir / "audio.mp3"
    
    if not vo_path.exists():
        print(f"❌ Voiceover not found in {selected_dir}")
        return False

    vo_dur = get_video_duration(str(vo_path))
    print(f"🎙️ Voiceover: {vo_path.name} ({vo_dur:.2f}s)")

    # 3. Locate Blueprints
    music_bp_file = selected_dir / "music_blueprint.json"
    vid_bp_file   = selected_dir / "video_blueprint.json"
    
    music_bp = {}
    if music_bp_file.exists():
        try:
            with open(music_bp_file, "r", encoding="utf-8") as f:
                music_bp = json.load(f)
            print(f"🎵 Music Blueprint Loaded ({len(music_bp.get('music_timeline', []))} segments)")
        except Exception as e:
            print(f"⚠️ Could not read music_blueprint.json: {e}")

    vid_bp = {}
    if vid_bp_file.exists():
        try:
            with open(vid_bp_file, "r", encoding="utf-8") as f:
                vid_bp = json.load(f)
            print(f"🎬 Video Blueprint Loaded (for SFX cues)")
        except Exception as e:
            print(f"⚠️ Could not read video_blueprint.json: {e}")

    music_dir = BASE_DIR / "music"
    sfx_dir   = BASE_DIR / "sfx"
    output_dir = BASE_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file_output = output_dir / "test_audio.mp3"
    output_file_root   = BASE_DIR / "test_audio.mp3"

    print("\n▶ Running Audio Mix Pipeline (Multi-Track Crossfade + Sidechain Ducking)...")
    generate_audio_mix(
        audio_dir=str(selected_dir),
        vo_path=str(vo_path),
        music_bp=music_bp,
        music_dir=str(music_dir),
        sfx_dir=str(sfx_dir),
        output_audio=str(output_file_output),
        total_duration=vo_dur,
        vid_bp=vid_bp
    )

    if output_file_output.exists():
        # Copy to root on Google Drive for immediate access
        try:
            shutil.copy2(output_file_output, output_file_root)
        except Exception:
            pass

        print("\n" + "=" * 60)
        print("✅ AUDIO MIX GENERATED SUCCESSFULLY!")
        print(f"📁 Saved to: {output_file_output}")
        print(f"📁 Saved to: {output_file_root} (Root Drive Folder)")
        print("=" * 60)
        return str(output_file_output)
    else:
        print("\n❌ Audio mix generation failed.")
        return False

if __name__ == "__main__":
    arg_dir = sys.argv[1] if len(sys.argv) > 1 else None
    res = run_test_audio_mix(arg_dir)
    if not res:
        sys.exit(1)
