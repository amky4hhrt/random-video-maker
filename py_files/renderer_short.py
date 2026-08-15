import os
import json
import subprocess
import shutil
from py_files.renderer_long import (
    generate_audio_mix, find_asset, AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, 
    run_ffmpeg, _video_encode_args, build_ken_burns_filter, get_easing_profile,
    stitch_with_crossfades, CPU_THREADS, _smooth_camera_rhythm
)
from py_files.captions import generate_subtitle_file

# Per-language font used for burned-in captions. Hindi needs a Devanagari-capable
# font (Montserrat has no Devanagari glyphs); English uses the brand font.
CAPTION_FONTS = {
    "en": "Montserrat Black",
    "hi": "Noto Sans Devanagari",
}

def _escape_ass_path_for_filter(path: str) -> str:
    # Paths inside an ffmpeg filtergraph need ':' and '\' escaped, and the
    # whole thing wrapped in single quotes so commas/spaces don't break
    # the filtergraph parser.
    escaped = path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
    return f"'{escaped}'"

def render_short_video(visual_dir, audio_dir, output_dir, music_dir, sfx_dir, language="en", image_dir=None, burn_captions=True):
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

    ass_path = None
    if burn_captions:
        try:
            font_name = CAPTION_FONTS.get(language, "Montserrat Black")
            ass_path = os.path.join(temp_dir, f"captions_{language}.ass")
            generate_subtitle_file(transcript, vid_bp, ass_path, is_short=True, font_name=font_name, use_karaoke=False)
        except Exception as e:
            print(f"  \u26A0\uFE0F Caption generation failed, continuing without captions: {e}")
            ass_path = None
    
    # 1. Audio Mix
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", vo_path]
    dur = float(subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True).stdout.strip())
    
    final_audio = os.path.join(temp_dir, "final_audio.mp3")
    generate_audio_mix(audio_dir, vo_path, mus_bp, music_dir, sfx_dir, final_audio, dur)
    
    # 2. Video Clips with Ken Burns (9:16 Vertical)
    WIDTH, HEIGHT = 1080, 1920
    clip_paths = []
    clip_info = []
    
    scenes = vid_bp.get("video_blueprint", []) or vid_bp.get("scenes", [])
    total_scenes = len(scenes)
    
    _smooth_camera_rhythm(scenes)
    
    for i, scene in enumerate(scenes):
        src_id = scene.get("english_source_scene_id")
        sid = src_id if src_id else scene.get("scene_id")
        
        img = find_asset(image_dir or visual_dir, str(sid), IMAGE_EXTENSIONS)
        if not img:
            print(f"  \u26A0\uFE0F Missing image for scene {sid}, skipping.")
            continue
            
        base_dur = scene.get("end_time", 0.0) - scene.get("start_time", 0.0)
        if base_dur <= 0: continue
        
        is_final = (i == total_scenes - 1)
        trans_type = str(scene.get("transition_type", "cut")).lower()
        
        if trans_type == "cut" or is_final:
            trans_dur = 0.0
            safety_pad = 0.0
        else:
            trans_dur = float(scene.get("transition_duration", 0.5))
            if trans_dur <= 0.0: trans_dur = 0.5
            safety_pad = 0.3
            
        render_dur = base_dur + trans_dur + safety_pad
        frames = max(int(render_dur * 30), 2)
        
        camera_movement = str(scene.get("camera_movement", "push_in")).lower()
        profile = get_easing_profile(scene)
        
        # Use the same Ken Burns engine but targeting 1080x1920 (vertical)
        vf = build_ken_burns_filter(camera_movement, frames, WIDTH, HEIGHT, profile)
            
        out_clip = os.path.join(temp_dir, f"clip_{i}_{sid}.mp4")
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", img, "-t", str(render_dur), "-vf", vf, "-r", "30"] + _video_encode_args("fast") + [out_clip]
        
        if run_ffmpeg(cmd, f"Scene {i+1}/{total_scenes} (ID:{sid})"):
            clip_paths.append(out_clip)
            clip_info.append({
                "base_duration": base_dur,
                "trans_type": trans_type,
                "trans_duration": trans_dur
            })
            
    if not clip_paths:
        print("  \u274C No clips rendered.")
        return False

    # Sanity check: does the rendered scene coverage actually reach the audio's
    # length? total_scene_dur is the mathematically exact final video duration
    # (crossfade offsets are computed so overlaps net out to this sum), so any
    # gap here means a scene was skipped (missing image / bad timestamp) or the
    # blueprint doesn't cover the full voiceover.
    total_scene_dur = sum(ci["base_duration"] for ci in clip_info)
    skipped = total_scenes - len(clip_paths)
    gap = dur - total_scene_dur
    print(f"  \u2139\uFE0F Scene coverage: {total_scene_dur:.2f}s rendered vs {dur:.2f}s audio "
          f"(gap: {gap:.2f}s, {skipped} scene(s) skipped)")

    # 3. Stitch with crossfade transitions
    print("  \U0001F3AC Stitching with crossfade transitions...")
    stitched = os.path.join(temp_dir, "stitched.mp4")
    
    if not stitch_with_crossfades(clip_paths, clip_info, stitched, temp_dir):
        print("  \u274C Stitching failed!")
        return False

    if not os.path.exists(stitched) or os.path.getsize(stitched) == 0:
        print(f"  \u274C Stitching reported success but {stitched} is missing/empty!")
        return False

    # If the video came in short of the audio (skipped scene / blueprint gap),
    # freeze the last frame to cover the difference instead of silently letting
    # -shortest below truncate the voiceover (and captions) early.
    if gap > 0.05:
        print(f"  \u26A0\uFE0F Video is {gap:.2f}s shorter than audio \u2014 extending final frame to compensate.")
        padded = os.path.join(temp_dir, "stitched_padded.mp4")
        if run_ffmpeg(
            ["ffmpeg", "-y", "-i", stitched, "-vf", f"tpad=stop_mode=clone:stop_duration={gap}"]
            + _video_encode_args("fast") + [padded],
            "Pad video to match audio length"
        ):
            stitched = padded
        else:
            print("  \u26A0\uFE0F Padding failed, continuing with the shorter (un-padded) video.")

    # 4. Final mux with audio (+ burn in captions, if generated)
    final_out = os.path.join(output_dir, f"final_{language}_short.mp4")
    if ass_path and os.path.exists(ass_path):
        subs_filter = f"subtitles={_escape_ass_path_for_filter(ass_path)}"
        cmd_final = (
            ["ffmpeg", "-y", "-i", stitched, "-i", final_audio, "-vf", subs_filter]
            + _video_encode_args("fast")
            + ["-c:a", "aac", "-shortest", final_out]
        )
        label = "Final Mux + Caption Burn-in"
    else:
        cmd_final = ["ffmpeg", "-y", "-i", stitched, "-i", final_audio, "-c:v", "copy", "-c:a", "aac", "-shortest", final_out]
        label = "Final Mux"
    mux_ok = run_ffmpeg(cmd_final, label)

    if not mux_ok or not os.path.exists(final_out) or os.path.getsize(final_out) == 0:
        print(f"  \u274C Short Render FAILED \u2014 {final_out} was not produced.")
        return False

    shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"  \u2705 Short Render complete! Saved to {final_out}")
    return True
