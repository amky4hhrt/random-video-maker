import os
import json
import subprocess
import shutil
import time

from py_files.captions import generate_subtitle_file, generate_subtitle_images


CAPTION_FONTS = {
    "en": "Montserrat Black",
    "hi": "NotoSansDevanagari-Black",
}

def _escape_ffmpeg_path(path: str) -> str:
    escaped = path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
    return f"'{escaped}'"

CPU_THREADS = 2
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")

def get_media_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTENSIONS: return "video"
    return "image"

def get_video_duration(path):
    try:
        res = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path], stderr=subprocess.STDOUT)
        return float(res.decode().strip())
    except:
        return 0.0

def _detect_nvenc() -> bool:
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        has_enc = "h264_nvenc" in res.stdout
    except: has_enc = False
    try:
        gpu = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        has_gpu = gpu.returncode == 0
    except: has_gpu = False
    return has_enc and has_gpu

USE_NVENC = _detect_nvenc()

def _video_encode_args(preset_speed: str = "fast") -> list:
    if USE_NVENC:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "18", "-b:v", "0", "-pix_fmt", "yuv420p"]
    else:
        return ["-c:v", "libx264", "-preset", preset_speed, "-crf", "18", "-pix_fmt", "yuv420p"]

# ─── Easing Profiles ───────────────────────────────────────────────
PROFILE_GENTLE = {"name": "gentle", "amplitude": 0.05, "ease_power": 1.0}
PROFILE_DRAMATIC = {"name": "dramatic", "amplitude": 0.09, "ease_power": 1.4}
PROFILE_REVEAL = {"name": "reveal", "amplitude": 0.08, "ease_power": 0.8}
PROFILE_CONTEMPLATIVE = {"name": "contemplative", "amplitude": 0.06, "ease_power": 1.8}

SCENE_TYPE_PROFILE = {
    "emotional": PROFILE_CONTEMPLATIVE,
    "intro": PROFILE_DRAMATIC,
    "discovery": PROFILE_REVEAL,
    "action": PROFILE_DRAMATIC,
    "dialogue": PROFILE_GENTLE,
    "description": PROFILE_GENTLE,
}
DEFAULT_PROFILE = PROFILE_GENTLE

def get_easing_profile(scene: dict) -> dict:
    scene_type = str(scene.get("scene_type", "")).lower()
    return SCENE_TYPE_PROFILE.get(scene_type, DEFAULT_PROFILE)

# ─── Camera Anti-Whiplash ─────────────────────────────────────────
OPPOSITES = {
    "push_in": "push_out", "push_out": "push_in",
    "pan_left": "pan_right", "pan_right": "pan_left",
    "tilt_up": "tilt_down", "tilt_down": "tilt_up",
}

def _smooth_camera_rhythm(scenes: list):
    """Prevents jarring A->B->A camera movement reversals."""
    n = len(scenes)
    for i in range(1, n - 1):
        prev_m = scenes[i-1].get("camera_movement", "static").lower()
        curr_m = scenes[i].get("camera_movement", "static").lower()
        next_m = scenes[i+1].get("camera_movement", "static").lower()
        
        if curr_m in OPPOSITES:
            if OPPOSITES[curr_m] == prev_m or OPPOSITES[curr_m] == next_m:
                scenes[i]["camera_movement"] = "push_in"

# ─── Ken Burns Filter Builder ──────────────────────────────────────
def build_ken_burns_filter(camera_movement: str, frames: int, width: int, height: int, profile: dict) -> str:
    amp = profile["amplitude"]
    d = max(frames, 2)
    
    if width > height:
        pre_w, pre_h = 7680, 4320
    else:
        pre_w, pre_h = 4320, 7680
    pre_scale = f"scale={pre_w}:{pre_h}:flags=bicubic"
    res = f"{width}x{height}"
    
    ease = f"pow(sin((on/{d})*(PI/2)),{profile['ease_power']})"
    cm = camera_movement.lower().strip()
    
    if cm == "push_in":
        zp = f"{pre_scale},zoompan=z='1.0+({amp}*{ease})':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={res}:fps=30"
    elif cm == "push_out":
        zp = f"{pre_scale},zoompan=z='(1.0+{amp})-({amp}*{ease})':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={res}:fps=30"
    elif cm == "pan_left":
        zp = f"{pre_scale},zoompan=z=1.1:x='(iw-(iw/1.1))*(1.0-{ease})':y='ih/2-(ih/zoom/2)':d={d}:s={res}:fps=30"
    elif cm == "pan_right":
        zp = f"{pre_scale},zoompan=z=1.1:x='(iw-(iw/1.1))*({ease})':y='ih/2-(ih/zoom/2)':d={d}:s={res}:fps=30"
    elif cm == "tilt_up":
        zp = f"{pre_scale},zoompan=z=1.1:x='iw/2-(iw/zoom/2)':y='(ih-(ih/1.1))*(1.0-{ease})':d={d}:s={res}:fps=30"
    elif cm == "tilt_down":
        zp = f"{pre_scale},zoompan=z=1.1:x='iw/2-(iw/zoom/2)':y='(ih-(ih/1.1))*({ease})':d={d}:s={res}:fps=30"
    elif cm == "zoom_in_left":
        zp = f"{pre_scale},zoompan=z='1.0+({amp}*{ease})':d={d}:x='0':y='ih/2-(ih/zoom/2)':s={res}:fps=30"
    elif cm == "zoom_in_right":
        zp = f"{pre_scale},zoompan=z='1.0+({amp}*{ease})':d={d}:x='iw-(iw/zoom)':y='ih/2-(ih/zoom/2)':s={res}:fps=30"
    elif cm == "zoom_out_left":
        zp = f"{pre_scale},zoompan=z='(1.0+{amp})-({amp}*{ease})':d={d}:x='0':y='ih/2-(ih/zoom/2)':s={res}:fps=30"
    elif cm == "zoom_out_right":
        zp = f"{pre_scale},zoompan=z='(1.0+{amp})-({amp}*{ease})':d={d}:x='iw-(iw/zoom)':y='ih/2-(ih/zoom/2)':s={res}:fps=30"
    elif cm == "static":
        zp = f"{pre_scale},zoompan=z='1.0':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={res}:fps=30"
    else:
        # Fallback to simple push_in
        zp = f"{pre_scale},zoompan=z='1.0+({amp}*{ease})':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={res}:fps=30"
        
    return f"{zp},format=yuv420p,setsar=1/1,fps=30"

def run_ffmpeg(cmd: list, label: str) -> bool:
    print(f"  \U000025B6 Running: {label}...")

    # Silence ffmpeg's banner/build-config spam and verbose per-frame logging.
    # Only inject these for actual ffmpeg calls (not ffprobe or other tools),
    # and only if the caller hasn't already set its own -loglevel.
    if cmd and os.path.basename(cmd[0]) == "ffmpeg":
        quiet_flags = []
        if "-hide_banner" not in cmd:
            quiet_flags.append("-hide_banner")
        if "-loglevel" not in cmd:
            quiet_flags.extend(["-loglevel", "error"])
        if quiet_flags:
            cmd = [cmd[0]] + quiet_flags + cmd[1:]

    start_t = time.time()
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    elapsed = time.time() - start_t

    if result.returncode != 0:
        print(f"  \u274C {label} FAILED (exit code {result.returncode}, after {elapsed:.1f}s)")
        if result.stdout:
            print("  ── ffmpeg output ──")
            print(result.stdout.strip())
            print("  ───────────────────")
        return False

    # Confirm the step actually produced usable output, not just a clean exit code.
    # ffmpeg can return 0 while writing a 0-byte or missing file in some failure modes.
    out_path = cmd[-1] if cmd else None
    if out_path and isinstance(out_path, str) and not out_path.startswith("-"):
        if os.path.exists(out_path):
            size = os.path.getsize(out_path)
            if size == 0:
                print(f"  \u274C {label} FAILED (exit 0 but output file is empty: {out_path})")
                return False
            print(f"  \u2705 Completed: {label} ({elapsed:.1f}s, {size/1_048_576:.1f} MB)")
        else:
            print(f"  \u2705 Completed: {label} ({elapsed:.1f}s)")
    else:
        print(f"  \u2705 Completed: {label} ({elapsed:.1f}s)")
    return True

def find_asset(input_dir, base_name, extensions):
    import glob
    for ext in extensions:
        exact = os.path.join(input_dir, f"{base_name}{ext}")
        if os.path.exists(exact): return exact
        matches = glob.glob(os.path.join(input_dir, f"{base_name}[_ \\-\\.]*{ext}"))
        if matches: return matches[0]
    return None


# ─── Final Audio Mix ──────────────────────────────────────────────
def generate_audio_mix(audio_dir, vo_path, music_bp, music_dir, sfx_dir, output_audio, total_duration):
    temp_dir = os.path.dirname(output_audio)
    
    # (Cinematic Reverb Removed)
    
    # Find Custom Music
    import glob
    AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.flac')
    
    def search_for_music(search_dir):
        for ext in AUDIO_EXTENSIONS:
            for f in glob.glob(os.path.join(search_dir, f"*{ext}")):
                name = os.path.basename(f).lower()
                if "voiceover" not in name and "temp" not in name and "final" not in name:
                    return f
        return None

    custom_music_path = search_for_music(audio_dir)
    
    # Hindi Fallback Logic
    if not custom_music_path and "hindi_" in audio_dir:
        fallback_dir = audio_dir.replace("hindi_", "english_")
        if os.path.exists(fallback_dir):
            custom_music_path = search_for_music(fallback_dir)
            if custom_music_path:
                print(f"  🎵 Using fallback English music: {os.path.basename(custom_music_path)}")
        
    cmd = ["ffmpeg", "-y", "-threads", str(CPU_THREADS)]
    
    if not custom_music_path:
        print("  ⚠️ No custom background music found. Rendering voiceover only.")
        cmd.extend(["-i", vo_path, "-filter_complex", "[0:a]aresample=44100,loudnorm=I=-14:LRA=11:TP=-1.5,alimiter=limit=0.95[a_out]", "-map", "[a_out]", "-t", str(total_duration), "-c:a", "libmp3lame", "-b:a", "192k", output_audio])
        run_ffmpeg(cmd, "Final Audio Mix (VO Only)")
        return
        
    print(f"  🎵 Mixing with Custom Background Music: {os.path.basename(custom_music_path)}")
    cmd.extend(["-i", vo_path, "-stream_loop", "-1", "-i", custom_music_path])
    
    # True Sidechain Compression
    # [1:a] - music, [0:a] - voiceover
    # Sidechain dynamically ducks the music when voiceover triggers it
    filter_complex = (
        "[0:a]aresample=44100,loudnorm=I=-14:LRA=11:TP=-1.5,asplit[vo_norm][vo_sc]; "
        "[1:a]aresample=44100,volume=0.63[bgm_resampled]; "
        "[bgm_resampled][vo_sc]sidechaincompress=threshold=-15dB:ratio=4:attack=50:release=300:makeup=2[bgm_ducked]; "
        "[bgm_ducked][vo_norm]amix=inputs=2:duration=first:weights=1 1,alimiter=limit=0.95[a_out]"
    )
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[a_out]",
        "-t", str(total_duration),
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_audio
    ])
    
    run_ffmpeg(cmd, "Final Audio Mix (Sidechain Ducking)")

def stitch_with_crossfades(clip_paths, clip_info, output_path, temp_dir):
    """
    Two-phase stitcher:
    1. Segments clips by crossfade boundaries. Concat non-xfade clips.
    2. xfade between segments.
    """
    TRANSITION_FILTER_MAP = {
        "dissolve": "fade",
        "flashback_fade": "fadewhite",
        "fade_to_black": "fadeblack",
        "fade": "fade" # Standard fallback
    }

    if not clip_paths: return False
    if len(clip_paths) == 1:
        shutil.copy2(clip_paths[0], output_path)
        return True

    # Find xfade boundaries
    xfade_at = []
    for i in range(len(clip_info) - 1):
        if clip_info[i]["trans_type"] in TRANSITION_FILTER_MAP and clip_info[i]["trans_duration"] > 0:
            xfade_at.append(i)

    if not xfade_at:
        # Fast path: pure concat
        list_file = os.path.join(temp_dir, "list.txt")
        with open(list_file, "w") as f:
            for p in clip_paths: f.write(f"file '{os.path.abspath(p)}'\n")
        return run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c:v", "copy", output_path], "Concat clips")

    # Step 2: Group clips into segments
    seg_starts = [0] + [x + 1 for x in xfade_at]
    seg_ends = [x + 1 for x in xfade_at] + [len(clip_paths)]
    seg_transitions = [clip_info[xfade_at[k]] for k in range(len(xfade_at))]

    seg_files = []
    seg_durations = []

    for k, (s, e) in enumerate(zip(seg_starts, seg_ends)):
        seg_clips = clip_paths[s:e]
        seg_dur = sum(clip_info[j]["base_duration"] for j in range(s, e))
        seg_durations.append(seg_dur)

        if len(seg_clips) == 1:
            seg_files.append(seg_clips[0])
        else:
            seg_path = os.path.join(temp_dir, f"segment_{k}.mp4")
            list_file = os.path.join(temp_dir, f"list_{k}.txt")
            with open(list_file, "w") as f:
                for p in seg_clips: f.write(f"file '{os.path.abspath(p)}'\n")
            run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c:v", "copy", seg_path], f"Concat segment {k}")
            seg_files.append(seg_path)

    # Step 3: Chain xfade across segments
    cmd_xfade = ["ffmpeg", "-y", "-threads", str(CPU_THREADS)]
    for p in seg_files: cmd_xfade.extend(["-i", p])

    filter_parts = []
    for i in range(len(seg_files)):
        filter_parts.append(f"[{i}:v]fps=30[vfps{i}]")
        
    current_v = "[vfps0]"
    current_duration = seg_durations[0]

    for j in range(1, len(seg_files)):
        trans = seg_transitions[j - 1]
        trans_dur = trans["trans_duration"]
        trans_filter = TRANSITION_FILTER_MAP.get(trans["trans_type"], "fade")
        
        offset = current_duration
        out_label = f"[v{j}]"
        filter_parts.append(f"{current_v}[vfps{j}]xfade=transition={trans_filter}:duration={trans_dur}:offset={offset}{out_label}")
        current_duration += seg_durations[j]
        current_v = out_label

    filter_complex = ";".join(filter_parts)
    cmd_xfade.extend(["-filter_complex", filter_complex, "-map", current_v] + _video_encode_args("fast") + [output_path])
    
    return run_ffmpeg(cmd_xfade, "Final crossfade stitch")


# ─── Main Render Function ─────────────────────────────────────────
def render_long_video(visual_dir, audio_dir, output_dir, music_dir, sfx_dir, language="en", image_dir=None, burn_captions=True):
    print(f"\n📹 Starting Long-Form Render for {language.upper()}")
    
    vo_path = find_asset(audio_dir, "voiceover", AUDIO_EXTENSIONS)
    if not vo_path:
        print("  ❌ Missing voiceover!")
        return False
        
    with open(os.path.join(audio_dir, "transcript.json")) as f: transcript = json.load(f)
    with open(os.path.join(visual_dir, "video_blueprint.json")) as f: vid_bp = json.load(f)
    
    music_bp_path = os.path.join(visual_dir, "music_blueprint.json")
    mus_bp = json.load(open(music_bp_path)) if os.path.exists(music_bp_path) else {}
    
    temp_dir = f"/tmp/temp_render_long_{language}"
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    ass_path = None
    if burn_captions:
        try:
            font_name = CAPTION_FONTS.get(language, "Montserrat Black")
            if language == "hi":
                ass_path = os.path.join(visual_dir, f"captions_{language}.txt")
                generate_subtitle_images(transcript, vid_bp, visual_dir, language, is_short=False, font_name="NotoSansDevanagari-Black", use_karaoke=True)
            else:
                ass_path = os.path.join(visual_dir, f"captions_{language}.ass")
                generate_subtitle_file(transcript, vid_bp, ass_path, is_short=False, font_name=font_name, use_karaoke=True)
        except Exception as e:
            print(f"  ⚠️ Caption generation failed, continuing without captions: {e}")
            ass_path = None
    
    # 1. Audio Mix
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", vo_path]
    dur = float(subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True).stdout.strip())
    dur += 2.0  # Add 2 seconds to accommodate cinematic reverb tail and music outro
    
    final_audio = os.path.join(temp_dir, "final_audio.mp3")
    generate_audio_mix(audio_dir, vo_path, mus_bp, music_dir, sfx_dir, final_audio, dur)
    
    # 2. Video Clips with Ken Burns
    WIDTH, HEIGHT = 1920, 1080
    clip_paths = []
    clip_info = []
    
    scenes = vid_bp.get("video_blueprint", []) or vid_bp.get("scenes", [])
    total_scenes = len(scenes)
    
    _smooth_camera_rhythm(scenes)
    
    for i, scene in enumerate(scenes):
        src_id = scene.get("english_source_scene_id")
        sid = src_id if src_id else scene.get("scene_id")
        
        asset_path = find_asset(image_dir or visual_dir, str(sid), IMAGE_EXTENSIONS + VIDEO_EXTENSIONS)
        if not asset_path:
            print(f"  ⚠️ Missing visual asset for scene {sid}, skipping.")
            continue
            
        start = 0.0 if i == 0 else scene.get("start_time", 0.0)
        end = dur if i == len(scenes) - 1 else scenes[i+1].get("start_time", 0.0)
        base_dur = end - start
        
        if base_dur <= 0: continue
        
        is_final = (i == total_scenes - 1)
        trans_type = str(scene.get("transition_type", "cut")).lower()
        
        if trans_type == "cut" or is_final:
            trans_dur = 0.0
            safety_pad = 0.0
        else:
            trans_dur = float(scene.get("transition_duration", 0.8))
            if trans_dur <= 0.0: trans_dur = 0.8
            safety_pad = 0.3
            
        render_dur = base_dur + trans_dur + safety_pad
        frames = max(int(render_dur * 30), 2)
        
        camera_movement = str(scene.get("camera_movement", "push_in")).lower()
        profile = get_easing_profile(scene)
        out_clip = os.path.join(temp_dir, f"clip_{i}_{sid}.mp4")
        
        mtype = get_media_type(asset_path)
        success = False
        
        if mtype == "image":
            vf = build_ken_burns_filter(camera_movement, frames, WIDTH, HEIGHT, profile)
            cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", asset_path, "-t", str(render_dur), "-vf", vf, "-r", "30"] + _video_encode_args("fast") + [out_clip]
            success = run_ffmpeg(cmd, f"Scene {i+1}/{total_scenes} (ID:{sid})")
        else:
            video_dur = get_video_duration(asset_path)
            scale_filter = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}"
            
            if video_dur >= render_dur:
                cmd = ["ffmpeg", "-y", "-i", asset_path, "-t", str(render_dur), "-vf", f"{scale_filter},fps=30"] + _video_encode_args("fast") + [out_clip]
                success = run_ffmpeg(cmd, f"Scene {i+1}/{total_scenes} (ID:{sid})")
            else:
                vid_part = os.path.join(temp_dir, f"clip_{i}_{sid}_vid.mp4")
                cmd_vid = ["ffmpeg", "-y", "-i", asset_path, "-vf", f"{scale_filter},fps=30"] + _video_encode_args("fast") + [vid_part]
                run_ffmpeg(cmd_vid, f"Scene {i+1}/{total_scenes} (ID:{sid} P1)")
                
                last_frame_img = os.path.join(temp_dir, f"clip_{i}_{sid}_last.jpg")
                cmd_frame = ["ffmpeg", "-y", "-sseof", "-0.1", "-i", asset_path, "-update", "1", "-q:v", "2", last_frame_img]
                run_ffmpeg(cmd_frame, f"Scene {i+1}/{total_scenes} (ID:{sid} Frame)")
                
                rem_dur = render_dur - video_dur
                rem_frames = max(int(rem_dur * 30), 2)
                vf = build_ken_burns_filter(camera_movement, rem_frames, WIDTH, HEIGHT, profile)
                img_part = os.path.join(temp_dir, f"clip_{i}_{sid}_img.mp4")
                cmd_img = ["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", last_frame_img, "-t", str(rem_dur), "-vf", vf, "-r", "30"] + _video_encode_args("fast") + [img_part]
                run_ffmpeg(cmd_img, f"Scene {i+1}/{total_scenes} (ID:{sid} P2)")
                
                list_file = os.path.join(temp_dir, f"list_{i}_{sid}.txt")
                with open(list_file, "w") as f:
                    f.write(f"file '{os.path.abspath(vid_part)}'\n")
                    f.write(f"file '{os.path.abspath(img_part)}'\n")
                
                cmd_concat = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c:v", "copy", out_clip]
                success = run_ffmpeg(cmd_concat, f"Scene {i+1}/{total_scenes} (ID:{sid})")

        if success:
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
    print("  \U0001F3AC Stitching clips...")
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
    final_out = os.path.join(output_dir, f"final_{language}_long.mp4")
    
    if ass_path and os.path.exists(ass_path):
        if ass_path.endswith(".txt"):
            cmd_final = (
                ["ffmpeg", "-y", "-i", stitched, "-i", final_audio, "-f", "concat", "-safe", "0", "-i", ass_path, "-filter_complex", "[0:v][2:v]overlay=0:0"]
                + _video_encode_args("fast") + ["-c:a", "aac", "-b:a", "192k", "-shortest", final_out]
            )
        else:
            ass_escaped = _escape_ffmpeg_path(ass_path)
            cmd_final = (
                ["ffmpeg", "-y", "-i", stitched, "-i", final_audio, "-vf", f"ass={ass_escaped}"]
                + _video_encode_args("fast") + ["-c:a", "aac", "-b:a", "192k", "-shortest", final_out]
            )
    else:
        cmd_final = ["ffmpeg", "-y", "-i", stitched, "-i", final_audio, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", final_out]
        
    mux_ok = run_ffmpeg(cmd_final, "Final Mux")

    if not mux_ok or not os.path.exists(final_out) or os.path.getsize(final_out) == 0:
        print(f"  ❌ Final render FAILED — {final_out} was not produced.")
        return False

    shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"  ✅ Render complete! Saved to {final_out}")
    return True
