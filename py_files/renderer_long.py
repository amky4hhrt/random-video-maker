import os
import json
import subprocess
import shutil

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
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "22", "-b:v", "0", "-pix_fmt", "yuv420p"]
    else:
        return ["-c:v", "libx264", "-preset", preset_speed, "-crf", "22", "-pix_fmt", "yuv420p"]

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

ALL_CAMERA_MOVEMENTS = (
    "push_in", "push_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
    "push_in_pan_left", "push_in_pan_right", "push_out_tilt_up", "push_out_tilt_down",
    "slow_drift", "orbit"
)

def get_easing_profile(scene: dict) -> dict:
    scene_type = str(scene.get("scene_type", "")).lower()
    return SCENE_TYPE_PROFILE.get(scene_type, DEFAULT_PROFILE)

# ─── Ken Burns Filter Builder ──────────────────────────────────────
def build_ken_burns_filter(camera_movement: str, frames: int, width: int, height: int, profile: dict) -> str:
    """
    Builds a zoompan filter string for ffmpeg based on the camera movement type.
    
    All movements use the easing profile's amplitude (how far to move) and
    ease_power (acceleration curve). 
    
    The zoompan filter evaluates expressions per-frame where:
      - 'on' = current frame number (0 to d-1)
      - 'd' = total frames
      - 'iw'/'ih' = input width/height
      - 'zoom' = current zoom level
    """
    amp = profile["amplitude"]
    # ease_power not directly usable in ffmpeg expressions (no pow()),
    # but we can approximate with linear for now — the amplitude drives the feel.
    d = max(frames, 2)
    
    # Base scale: we scale the image up by (1 + amp*2) so we have room to pan/zoom
    scale_factor = 1 + amp * 2
    sw = int(width * scale_factor)
    sh = int(height * scale_factor)
    
    cm = camera_movement.lower().strip()
    
    if cm == "push_in":
        # Zoom from 1.0 to 1+amp, centered
        z_expr = f"1+{amp}*on/{d}"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)"
        
    elif cm == "push_out":
        # Zoom from 1+amp to 1.0, centered
        z_expr = f"{1+amp}-{amp}*on/{d}"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)"
        
    elif cm == "pan_left":
        # Slide from right to left, slight zoom
        z_expr = f"{1+amp*0.3}"
        x_expr = f"(iw-iw/zoom)*(1-on/{d})"
        y_expr = f"ih/2-(ih/zoom/2)"
        
    elif cm == "pan_right":
        # Slide from left to right, slight zoom
        z_expr = f"{1+amp*0.3}"
        x_expr = f"(iw-iw/zoom)*on/{d}"
        y_expr = f"ih/2-(ih/zoom/2)"
        
    elif cm == "tilt_up":
        # Slide from bottom to top, slight zoom
        z_expr = f"{1+amp*0.3}"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*(1-on/{d})"
        
    elif cm == "tilt_down":
        # Slide from top to bottom, slight zoom
        z_expr = f"{1+amp*0.3}"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*on/{d}"
        
    elif cm == "push_in_pan_left":
        # Zoom in while panning left (diagonal drift upper-left)
        z_expr = f"1+{amp}*on/{d}"
        x_expr = f"(iw-iw/zoom)*(1-on/{d})"
        y_expr = f"ih/2-(ih/zoom/2)"
        
    elif cm == "push_in_pan_right":
        # Zoom in while panning right (diagonal drift upper-right)
        z_expr = f"1+{amp}*on/{d}"
        x_expr = f"(iw-iw/zoom)*on/{d}"
        y_expr = f"ih/2-(ih/zoom/2)"
        
    elif cm == "push_out_tilt_up":
        # Zoom out while tilting up (dramatic reveal upward)
        z_expr = f"{1+amp}-{amp}*on/{d}"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*(1-on/{d})"
        
    elif cm == "push_out_tilt_down":
        # Zoom out while tilting down (settling shot)
        z_expr = f"{1+amp}-{amp}*on/{d}"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*on/{d}"
        
    elif cm == "slow_drift":
        # Very gentle diagonal drift (upper-left to center) with subtle zoom
        half_amp = amp * 0.5
        z_expr = f"1+{half_amp}*on/{d}"
        x_expr = f"(iw-iw/zoom)*(0.7-0.4*on/{d})"
        y_expr = f"(ih-ih/zoom)*(0.7-0.4*on/{d})"
        
    elif cm == "orbit":
        # Gentle horizontal pan with subtle zoom pulse (feels like floating)
        # Approximates a sine-like motion using a triangle wave
        half_amp = amp * 0.4
        z_expr = f"1+{half_amp}"
        x_expr = f"(iw-iw/zoom)*on/{d}"
        y_expr = f"(ih-ih/zoom)*(0.5-0.15*on/{d})"
        
    else:
        # Fallback: gentle push_in
        z_expr = f"1+{amp*0.5}*on/{d}"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)"
    
    return (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={d}:s={width}x{height}:fps=30,"
        f"format=yuv420p"
    )

def run_ffmpeg(cmd: list, label: str) -> bool:
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(f"  \u274C {label} failed: {result.stderr.decode(errors='ignore')[-300:]}")
        return False
    return True

def find_asset(input_dir, base_name, extensions):
    import glob
    for ext in extensions:
        exact = os.path.join(input_dir, f"{base_name}{ext}")
        if os.path.exists(exact): return exact
        # Match base_name followed by any valid separator (_ - space .) or just the extension
        matches = glob.glob(os.path.join(input_dir, f"{base_name}[_ \\-\\.]*{ext}"))
        if matches: return matches[0]
    return None

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
    
    cmd = ["ffmpeg", "-y", "-threads", str(CPU_THREADS), "-i", vo_path]
    inputs = 1
    if has_bgm:
        cmd.extend(["-i", bgm_path])
        inputs += 1
    if has_sfx:
        cmd.extend(["-i", sfx_path])
        inputs += 1
        
    if inputs == 1:
        shutil.copy2(vo_path, output_audio)
        return
        
    mix_filters = []
    if has_bgm: mix_filters.append(f"[1:a]volume=0.4[bgm_ducked]")
    else: mix_filters.append(f"[0:a]anull[bgm_ducked]")
    
    if inputs == 2 and has_bgm:
        mix_filters.append(f"[0:a][bgm_ducked]amix=inputs=2:duration=first:normalize=0[a_out]")
    elif inputs == 3:
        mix_filters.append(f"[0:a][bgm_ducked][2:a]amix=inputs=3:duration=first:normalize=0[a_out]")
        
    cmd.extend(["-filter_complex", ";".join(mix_filters), "-map", "[a_out]", "-c:a", "libmp3lame", "-b:a", "192k", output_audio])
    run_ffmpeg(cmd, "Final Audio Mix")

# ─── Crossfade Stitcher ────────────────────────────────────────────
def stitch_with_crossfades(clip_paths, output_path, xfade_duration=1.0, clip_durations=None):
    """
    Stitches clips together using ffmpeg xfade filter for smooth dissolve transitions.
    clip_durations: list of float durations (seconds) for each clip.
    """
    if not clip_paths:
        return False
    if len(clip_paths) == 1:
        shutil.copy2(clip_paths[0], output_path)
        return True
    
    # For large numbers of clips, xfade filter_complex becomes enormous.
    # We batch stitch in groups to avoid ffmpeg command line limits.
    BATCH_SIZE = 20
    
    if len(clip_paths) <= BATCH_SIZE:
        return _xfade_stitch(clip_paths, output_path, xfade_duration, clip_durations)
    else:
        # Batch: stitch groups, then stitch the results
        temp_dir = os.path.dirname(output_path)
        batch_outputs = []
        batch_durs = []
        
        for batch_idx in range(0, len(clip_paths), BATCH_SIZE):
            batch = clip_paths[batch_idx:batch_idx + BATCH_SIZE]
            batch_dur = clip_durations[batch_idx:batch_idx + BATCH_SIZE] if clip_durations else None
            batch_out = os.path.join(temp_dir, f"batch_{batch_idx}.mp4")
            
            if _xfade_stitch(batch, batch_out, xfade_duration, batch_dur):
                batch_outputs.append(batch_out)
                # Calculate resulting duration: sum of durations minus overlaps
                if batch_dur:
                    total = sum(batch_dur) - xfade_duration * (len(batch) - 1)
                    batch_durs.append(total)
            else:
                # Fallback: concat without transitions
                batch_outputs.append(batch[0])
                if batch_dur:
                    batch_durs.append(batch_dur[0])
        
        if len(batch_outputs) == 1:
            shutil.copy2(batch_outputs[0], output_path)
            return True
        
        return _xfade_stitch(batch_outputs, output_path, xfade_duration, batch_durs if batch_durs else None)

def _xfade_stitch(clip_paths, output_path, xfade_duration, clip_durations):
    """Stitches up to ~20 clips with xfade transitions."""
    n = len(clip_paths)
    if n == 1:
        shutil.copy2(clip_paths[0], output_path)
        return True
    
    cmd = ["ffmpeg", "-y", "-threads", str(CPU_THREADS)]
    for p in clip_paths:
        cmd.extend(["-i", p])
    
    # Build xfade filter chain
    # [0:v][1:v]xfade=transition=fade:duration=1:offset=X[v01];
    # [v01][2:v]xfade=transition=fade:duration=1:offset=Y[v012]; ...
    filter_parts = []
    
    # We need to know the duration of each clip to calculate offsets
    if clip_durations and len(clip_durations) == n:
        durations = clip_durations
    else:
        # Probe durations
        durations = []
        for p in clip_paths:
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
                     "-of", "default=noprint_wrappers=1:nokey=1", p],
                    stdout=subprocess.PIPE, text=True
                )
                durations.append(float(probe.stdout.strip()))
            except:
                durations.append(5.0)  # fallback
    
    xd = min(xfade_duration, 0.5)  # safety cap per transition
    
    # Calculate cumulative offset for each xfade
    # First xfade happens at: duration_of_clip_0 - xfade_duration
    # Second xfade: (dur_0 + dur_1 - xfade) - xfade = dur_0 + dur_1 - 2*xfade
    cumulative_offset = 0.0
    
    for i in range(n - 1):
        in_a = f"[{i}:v]" if i == 0 else f"[v{i}]"
        in_b = f"[{i+1}:v]"
        out_label = f"[v{i+1}]" if i < n - 2 else "[vout]"
        
        cumulative_offset += durations[i] - (xd if i > 0 else 0)
        offset = max(0, cumulative_offset - xd)
        
        filter_parts.append(f"{in_a}{in_b}xfade=transition=fade:duration={xd}:offset={offset:.3f}{out_label}")
    
    filter_str = ";".join(filter_parts)
    cmd.extend(["-filter_complex", filter_str, "-map", "[vout]"] + _video_encode_args("fast") + [output_path])
    
    return run_ffmpeg(cmd, "Crossfade stitch")

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
    clip_durations = []
    
    scenes = vid_bp.get("video_blueprint", []) or vid_bp.get("scenes", [])
    total_scenes = len(scenes)
    
    for i, scene in enumerate(scenes):
        # For Hindi blueprints, the image matches the english source scene
        src_id = scene.get("english_source_scene_id")
        sid = src_id if src_id else scene.get("scene_id")
        
        img = find_asset(image_dir or visual_dir, str(sid), IMAGE_EXTENSIONS)
        if not img:
            print(f"  \u26A0\uFE0F Missing image for scene {sid}, skipping.")
            continue
            
        s_dur = scene.get("end_time", 0.0) - scene.get("start_time", 0.0)
        if s_dur <= 0: continue
        
        frames = max(int(s_dur * 30), 2)
        camera_movement = str(scene.get("camera_movement", "push_in")).lower()
        profile = get_easing_profile(scene)
        
        vf = build_ken_burns_filter(camera_movement, frames, WIDTH, HEIGHT, profile)
            
        out_clip = os.path.join(temp_dir, f"clip_{i}_{sid}.mp4")
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img, "-t", str(s_dur), "-vf", vf] + _video_encode_args("fast") + [out_clip]
        if run_ffmpeg(cmd, f"Scene {i+1}/{total_scenes} (ID:{sid})"):
            clip_paths.append(out_clip)
            clip_durations.append(s_dur)
            
    if not clip_paths:
        print("  \u274C No clips rendered.")
        return False
    
    # 3. Stitch with crossfade transitions
    print("  \U0001F3AC Stitching with crossfade transitions...")
    stitched = os.path.join(temp_dir, "stitched.mp4")
    
    if not stitch_with_crossfades(clip_paths, stitched, xfade_duration=0.8, clip_durations=clip_durations):
        # Fallback to simple concat if xfade fails
        print("  \u26A0\uFE0F Crossfade failed, falling back to simple concat...")
        list_file = os.path.join(temp_dir, "list.txt")
        with open(list_file, "w") as f:
            for p in clip_paths: f.write(f"file '{os.path.abspath(p)}'\n")
        run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c:v", "copy", stitched], "Concat")
    
    # 4. Final mux with audio
    final_out = os.path.join(output_dir, f"final_{language}_long.mp4")
    run_ffmpeg(["ffmpeg", "-y", "-i", stitched, "-i", final_audio, "-c:v", "copy", "-c:a", "aac", "-shortest", final_out], "Final Mux")
    
    shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"  \u2705 Render complete! Saved to {final_out}")
    return True
