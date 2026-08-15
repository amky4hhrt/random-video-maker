import os
import json
import subprocess
import shutil
import time

CPU_THREADS = 2
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")

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

# ─── Audio Ducking & Bed ──────────────────────────────────────────
BGM_BASE_MULTIPLIER  = 1.0
BGM_DUCK_MULTIPLIER  = 0.40
VOLUME_RAMP_SECONDS  = 1.2

def build_volume_breakpoints(music_data: dict, active_end: float) -> list:
    cues = sorted(music_data.get("duck_cues", []), key=lambda c: c.get("start_time", 0.0))
    points = {0.0: BGM_BASE_MULTIPLIER}
    for cue in cues:
        t = cue.get("start_time")
        if t is None or t >= active_end: continue
        direction = cue.get("volume_direction")
        if direction == "decrease": points[round(t, 3)] = BGM_DUCK_MULTIPLIER
        elif direction == "increase": points[round(t, 3)] = BGM_BASE_MULTIPLIER
    return sorted(points.items())

def build_volume_expr(breakpoints: list, ramp_seconds: float, total_duration: float) -> str:
    if not breakpoints: return f"{BGM_BASE_MULTIPLIER}"
    if len(breakpoints) == 1: return f"{breakpoints[0][1]}"

    t0, v0 = breakpoints[0]
    intervals = []
    prev_ramp_end, prev_level = t0, v0

    for t, v in breakpoints[1:]:
        if t > prev_ramp_end:
            intervals.append((t, f"{prev_level}"))
        ramp_end = min(t + ramp_seconds, total_duration)
        if ramp_end > t:
            span = max(ramp_end - t, 0.001)
            intervals.append((ramp_end, f"({prev_level}+({v}-{prev_level})*(t-{t})/{span})"))
        prev_ramp_end, prev_level = ramp_end, v

    intervals.append((None, f"{prev_level}"))
    
    expr = intervals[-1][1]
    for upper_bound, this_expr in reversed(intervals[:-1]):
        expr = f"if(lt(t,{upper_bound}),{this_expr},{expr})"
    return expr

def build_music_bed(music_placements, total_duration, output_path):
    if not music_placements: return False
    cmd = ["ffmpeg", "-y", "-threads", str(CPU_THREADS)]
    for path, _, _ in music_placements: cmd.extend(["-i", path])
    n = len(music_placements)
    filter_parts = []
    for i, (_, start_time, end_time) in enumerate(music_placements):
        delay_ms = max(0, int(start_time * 1000))
        play_dur = max(0.1, end_time - start_time)
        fade_out = max(0.0, play_dur - 2.0)
        filters = f"[{i}:a]atrim=0:{play_dur}"
        if start_time > 0.5: filters += f",afade=t=in:st=0:d=2"
        filters += f",afade=t=out:st={fade_out}:d=2,adelay={delay_ms}|{delay_ms}[m{i}]"
        filter_parts.append(filters)
    if n == 1: filter_parts.append("[m0]anull[music_out]")
    else:
        mix = "".join(f"[m{i}]" for i in range(n))
        filter_parts.append(f"{mix}amix=inputs={n}:duration=longest:normalize=0[music_out]")
    
    cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", "[music_out]", "-t", str(total_duration), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", output_path])
    return run_ffmpeg(cmd, "Music bed assembly")

def build_sfx_bed(sfx_placements, total_duration, output_path):
    if not sfx_placements: return False
    cmd = ["ffmpeg", "-y", "-threads", str(CPU_THREADS)]
    for path, _ in sfx_placements: cmd.extend(["-i", path])
    n = len(sfx_placements)
    filter_parts = []
    for i, (_, start_time) in enumerate(sfx_placements):
        delay_ms = max(0, int(start_time * 1000))
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[s{i}]")
    if n == 1: filter_parts.append("[s0]volume=0.55[sfx_out]")
    else:
        mix = "".join(f"[s{i}]" for i in range(n))
        filter_parts.append(f"{mix}amix=inputs={n}:duration=longest:normalize=0,volume=0.55[sfx_out]")
    
    cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", "[sfx_out]", "-t", str(total_duration), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", output_path])
    return run_ffmpeg(cmd, "SFX bed assembly")

def generate_audio_mix(audio_dir, vo_path, music_bp, music_dir, sfx_dir, output_audio, total_duration):
    m_placements = []
    for mt in music_bp.get("music_tracks", []):
        m_id = mt.get("music_id", "")
        m_path = find_asset(music_dir, m_id, AUDIO_EXTENSIONS)
        if m_path: m_placements.append((m_path, mt.get("start_time", 0.0), mt.get("end_time", 999999.0)))
        
    s_placements = []
    for st in music_bp.get("sfx_cues", []):
        s_id = st.get("sfx_id", "")
        s_path = find_asset(sfx_dir, s_id, AUDIO_EXTENSIONS)
        if s_path: s_placements.append((s_path, st.get("start_time", 0.0)))
        
    temp_dir = os.path.dirname(output_audio)
    bgm_path = os.path.join(temp_dir, "bgm_bed.wav")
    sfx_path = os.path.join(temp_dir, "sfx_bed.wav")
    
    has_bgm = build_music_bed(m_placements, total_duration, bgm_path)
    has_sfx = build_sfx_bed(s_placements, total_duration, sfx_path)
    
    breakpoints = build_volume_breakpoints(music_bp, total_duration)
    vol_expr = build_volume_expr(breakpoints, VOLUME_RAMP_SECONDS, total_duration)
    
    cmd = ["ffmpeg", "-y", "-threads", str(CPU_THREADS), "-i", vo_path]
    inputs = 1
    if has_bgm:
        cmd.extend(["-i", bgm_path])
        inputs += 1
    if has_sfx:
        cmd.extend(["-i", sfx_path])
        inputs += 1
        
    if inputs == 1:
        cmd.extend(["-filter_complex", "[0:a]aresample=44100,loudnorm=I=-14:LRA=11:TP=-1.5,alimiter=limit=0.95[a_out]", "-map", "[a_out]", "-c:a", "libmp3lame", "-b:a", "192k", output_audio])
        run_ffmpeg(cmd, "Final Audio Mix")
        return

    filter_complex = ""
    if has_bgm and has_sfx:
        filter_complex = (
            f"[1:a]loudnorm=I=-22:LRA=7:TP=-2,volume=eval=frame:volume='{vol_expr}'[bgm_final]; "
            "[2:a]aresample=44100[sfx_resampled]; "
            "[bgm_final][sfx_resampled]amix=inputs=2:duration=longest:normalize=0[music_bed]; "
            "[0:a]aresample=44100,loudnorm=I=-14:LRA=11:TP=-1.5[vo_norm]; "
            "[music_bed][vo_norm]amix=inputs=2:duration=longest:weights=1 1,alimiter=limit=0.95[a_out]"
        )
    elif has_bgm:
        filter_complex = (
            f"[1:a]loudnorm=I=-22:LRA=7:TP=-2,volume=eval=frame:volume='{vol_expr}'[bgm_final]; "
            "[0:a]aresample=44100,loudnorm=I=-14:LRA=11:TP=-1.5[vo_norm]; "
            "[bgm_final][vo_norm]amix=inputs=2:duration=longest:weights=1 1,alimiter=limit=0.95[a_out]"
        )
    elif has_sfx:
        filter_complex = (
            "[1:a]aresample=44100[sfx_resampled]; "
            "[0:a]aresample=44100,loudnorm=I=-14:LRA=11:TP=-1.5[vo_norm]; "
            "[sfx_resampled][vo_norm]amix=inputs=2:duration=longest:weights=1 1,alimiter=limit=0.95[a_out]"
        )

    cmd.extend(["-filter_complex", filter_complex, "-map", "[a_out]", "-c:a", "libmp3lame", "-b:a", "192k", output_audio])
    run_ffmpeg(cmd, "Final Audio Mix")

# ─── Crossfade Stitcher ────────────────────────────────────────────
def stitch_with_crossfades(clip_paths, clip_info, output_path, temp_dir):
    """
    Two-phase stitcher:
    1. Segments clips by crossfade boundaries. Concat non-xfade clips.
    2. xfade between segments.
    """
    TRANSITION_FILTER_MAP = {
        "dissolve": "fadewhite",
        "flashback_fade": "fadewhite",
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
    current_v = "[0:v]"
    current_duration = seg_durations[0]

    for j in range(1, len(seg_files)):
        trans = seg_transitions[j - 1]
        trans_dur = trans["trans_duration"]
        trans_filter = TRANSITION_FILTER_MAP.get(trans["trans_type"], "fade")
        
        offset = current_duration
        out_label = f"[v{j}]"
        filter_parts.append(f"{current_v}[{j}:v]xfade=transition={trans_filter}:duration={trans_dur}:offset={offset}{out_label}")
        current_duration += seg_durations[j]
        current_v = out_label

    filter_complex = ";".join(filter_parts)
    cmd_xfade.extend(["-filter_complex", filter_complex, "-map", current_v] + _video_encode_args("fast") + [output_path])
    
    return run_ffmpeg(cmd_xfade, "Final crossfade stitch")


# ─── Main Render Function ─────────────────────────────────────────
def render_long_video(visual_dir, audio_dir, output_dir, music_dir, sfx_dir, language="en", image_dir=None):
    print(f"\n\U0001F4F9 Starting Long-Form Render for {language.upper()}")
    
    vo_path = find_asset(audio_dir, "voiceover", AUDIO_EXTENSIONS)
    if not vo_path:
        print("  \u274C Missing voiceover!")
        return False
        
    with open(os.path.join(audio_dir, "transcript.json")) as f: transcript = json.load(f)
    with open(os.path.join(visual_dir, "video_blueprint.json")) as f: vid_bp = json.load(f)
    
    music_bp_path = os.path.join(visual_dir, "music_blueprint.json")
    mus_bp = json.load(open(music_bp_path)) if os.path.exists(music_bp_path) else {}
    
    temp_dir = f"/tmp/temp_render_long_{language}"
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Audio Mix
    cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", vo_path]
    dur = float(subprocess.run(cmd_dur, stdout=subprocess.PIPE, text=True).stdout.strip())
    
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
            # For dissolve/fade, use 0.8s default if not specified
            trans_dur = float(scene.get("transition_duration", 0.8))
            if trans_dur <= 0.0: trans_dur = 0.8
            safety_pad = 0.3
            
        render_dur = base_dur + trans_dur + safety_pad
        frames = max(int(render_dur * 30), 2)
        
        camera_movement = str(scene.get("camera_movement", "push_in")).lower()
        profile = get_easing_profile(scene)
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

    # 4. Final mux with audio
    final_out = os.path.join(output_dir, f"final_{language}_long.mp4")
    mux_ok = run_ffmpeg(["ffmpeg", "-y", "-i", stitched, "-i", final_audio, "-c:v", "copy", "-c:a", "aac", "-shortest", final_out], "Final Mux")

    if not mux_ok or not os.path.exists(final_out) or os.path.getsize(final_out) == 0:
        print(f"  \u274C Final render FAILED \u2014 {final_out} was not produced.")
        return False

    shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"  \u2705 Render complete! Saved to {final_out}")
    return True
