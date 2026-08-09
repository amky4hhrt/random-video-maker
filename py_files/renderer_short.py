import os
import json
import subprocess
import shutil
from py_files.renderer_long import (
    generate_audio_mix, find_asset, AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, 
    run_ffmpeg, _video_encode_args, build_ken_burns_filter, get_easing_profile,
    stitch_with_crossfades, CPU_THREADS
)


def render_short_video(visual_dir, audio_dir, output_dir, music_dir, sfx_dir, language="en", image_dir=None):
    print(f"\n\U0001F4F1 Starting Short-Form Render (9:16) for {language.upper()}")
    
    vo_path = find_asset(audio_dir, "voiceover", AUDIO_EXTENSIONS)
    if not vo_path:
        print("  \u274C Missing voiceover!")
        return False
        
    with open(os.path.join(audio_dir, "transcript.json")) as f: transcript = json.load(f)
    with open(os.path.join(visual_dir, "video_blueprint.json")) as f: vid_bp = json.load(f)
    
    music_bp_path = os.path.join(visual_dir, "music_blueprint.json")
    mus_bp = json.load(open(music_bp_path)) if os.path.exists(music_bp_path) else {}
    
    temp_dir = f"/tmp/temp_render_short_{language}"
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Audio Mix
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", vo_path]
    dur = float(subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True).stdout.strip())
    
    final_audio = os.path.join(temp_dir, "final_audio.mp3")
    generate_audio_mix(audio_dir, vo_path, mus_bp, music_dir, sfx_dir, final_audio, dur)
    
    # 2. Video Clips with Ken Burns (9:16 Vertical)
    WIDTH, HEIGHT = 1080, 1920
    clip_paths = []
    clip_durations = []
    
    scenes = vid_bp.get("video_blueprint", []) or vid_bp.get("scenes", [])
    total_scenes = len(scenes)
    
    for i, scene in enumerate(scenes):
        # For Hindi blueprints, the image matches the english source scene
        src_id = scene.get("english_source_scene_id")
        sid = src_id if src_id else scene.get("scene_id")
        
        img = find_asset(image_dir or visual_dir, str(sid), IMAGE_EXTENSIONS)
        if not img: continue
            
        s_dur = scene.get("end_time", 0.0) - scene.get("start_time", 0.0)
        if s_dur <= 0: continue
        
        frames = max(int(s_dur * 30), 2)
        camera_movement = str(scene.get("camera_movement", "push_in")).lower()
        profile = get_easing_profile(scene)
        
        # Use the same Ken Burns engine but targeting 1080x1920 (vertical)
        vf = build_ken_burns_filter(camera_movement, frames, WIDTH, HEIGHT, profile)
            
        out_clip = os.path.join(temp_dir, f"clip_{i}_{sid}.mp4")
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", img, "-t", str(s_dur), "-vf", vf] + _video_encode_args("fast") + [out_clip]
        if run_ffmpeg(cmd, f"Scene {i+1}/{total_scenes} (ID:{sid})"):
            clip_paths.append(out_clip)
            clip_durations.append(s_dur)
            
    # 3. Stitch with crossfade transitions
    if not clip_paths:
        print("  \u274C No clips rendered.")
        return False
    
    print("  \U0001F3AC Stitching with crossfade transitions...")
    stitched = os.path.join(temp_dir, "stitched.mp4")
    
    if not stitch_with_crossfades(clip_paths, stitched, xfade_duration=0.5, clip_durations=clip_durations):
        # Fallback to simple concat
        print("  \u26A0\uFE0F Crossfade failed, falling back to simple concat...")
        list_file = os.path.join(temp_dir, "list.txt")
        with open(list_file, "w") as f:
            for p in clip_paths: f.write(f"file '{os.path.abspath(p)}'\n")
        run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c:v", "copy", stitched], "Concat")
    
    # 4. Final mux with audio
    final_out = os.path.join(output_dir, f"final_{language}_short.mp4")
    run_ffmpeg(["ffmpeg", "-y", "-i", stitched, "-i", final_audio, "-c:v", "copy", "-c:a", "aac", "-shortest", final_out], "Final Mux")
    
    shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"  \u2705 Short Render complete! Saved to {final_out}")
    return True
