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

def find_voiceover_file(search_dir):
    p = Path(search_dir)
    if not p.exists():
        return None
    # 1. Prioritize voiceover.* then audio.*
    for prefix in ["voiceover", "audio"]:
        for ext in [".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"]:
            cand = p / f"{prefix}{ext}"
            if cand.exists():
                return cand
    # 2. Any audio file in the folder (excluding background music if in music/)
    for ext in [".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"]:
        matches = [f for f in p.glob(f"*{ext}") if not f.name.startswith("test_audio")]
        if matches:
            return matches[0]
    return None

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
        BASE_DIR,
    ]
    
    selected_dir = None
    vo_path = None
    if asset_dir:
        cand = Path(asset_dir)
        if cand.exists():
            selected_dir = cand
            vo_path = find_voiceover_file(selected_dir)
    else:
        for c in candidates:
            cand_vo = find_voiceover_file(c)
            if cand_vo:
                selected_dir = c
                vo_path = cand_vo
                break

    if not selected_dir or not vo_path:
        print("❌ Could not find voiceover.mp3, audio.wav, or any audio file!")
        print(f"Checked folders: {[str(c.name) for c in candidates]}")
        return False

    print(f"📁 Selected Asset Folder: {selected_dir.name if selected_dir != BASE_DIR else 'Project Root'}")
    vo_dur = get_video_duration(str(vo_path))
    print(f"🎙️ Voiceover: {vo_path.name} ({vo_dur:.2f}s)")

    # 3. Locate Blueprints (check selected_dir first, then any asset dir)
    music_bp_file = selected_dir / "music_blueprint.json"
    if not music_bp_file.exists():
        for c in candidates:
            if (c / "music_blueprint.json").exists():
                music_bp_file = c / "music_blueprint.json"
                break

    vid_bp_file = selected_dir / "video_blueprint.json"
    if not vid_bp_file.exists():
        for c in candidates:
            if (c / "video_blueprint.json").exists():
                vid_bp_file = c / "video_blueprint.json"
                break
    
    music_bp = {}
    if music_bp_file and music_bp_file.exists():
        try:
            with open(music_bp_file, "r", encoding="utf-8") as f:
                music_bp = json.load(f)
            print(f"🎵 Music Blueprint Loaded from {music_bp_file.parent.name} ({len(music_bp.get('music_timeline', []))} segments)")
        except Exception as e:
            print(f"⚠️ Could not read music_blueprint.json: {e}")

    vid_bp = {}
    if vid_bp_file and vid_bp_file.exists():
        try:
            with open(vid_bp_file, "r", encoding="utf-8") as f:
                vid_bp = json.load(f)
            print(f"🎬 Video Blueprint Loaded from {vid_bp_file.parent.name} (for SFX cues)")
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
