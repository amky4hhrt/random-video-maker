import re

with open("/home/ankit-poddar/Documents/Video_Projects/py_files/renderer_long.py", "r") as f:
    content = f.read()

# Replace generate_audio_mix
start_str = "def generate_audio_mix("
end_str = "def stitch_with_crossfades("

start_idx = content.find(start_str)
end_idx = content.find(end_str)

new_func = """def generate_audio_mix(audio_dir, vo_path, music_bp, music_dir, sfx_dir, output_audio, total_duration):
    temp_dir = os.path.dirname(output_audio)
    
    # ── Cinematic Reverb ──
    vo_reverb_path = os.path.join(temp_dir, "vo_reverb.mp3")
    if apply_cinematic_reverb(vo_path, vo_reverb_path):
        vo_path = vo_reverb_path
    else:
        print("  ⚠️ Warning: Reverb application failed, falling back to dry voiceover.")
    
    # Find Custom Music
    import glob
    AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.flac')
    custom_music_path = None
    for ext in AUDIO_EXTENSIONS:
        for f in glob.glob(os.path.join(audio_dir, f"*{ext}")):
            name = os.path.basename(f).lower()
            if "voiceover" not in name and "temp" not in name and "final" not in name:
                custom_music_path = f
                break
        if custom_music_path: break
        
    cmd = ["ffmpeg", "-y", "-threads", str(CPU_THREADS)]
    
    if not custom_music_path:
        print("  ⚠️ No custom background music found. Rendering voiceover only.")
        cmd.extend(["-i", vo_path, "-filter_complex", "[0:a]aresample=44100,loudnorm=I=-14:LRA=11:TP=-1.5,alimiter=limit=0.95[a_out]", "-map", "[a_out]", "-c:a", "libmp3lame", "-b:a", "192k", output_audio])
        run_ffmpeg(cmd, "Final Audio Mix (VO Only)")
        return
        
    print(f"  🎵 Mixing with Custom Background Music: {os.path.basename(custom_music_path)}")
    cmd.extend(["-i", vo_path, "-stream_loop", "-1", "-i", custom_music_path])
    
    # True Sidechain Compression
    # [1:a] - music, [0:a] - voiceover
    # Sidechain dynamically ducks the music when voiceover triggers it
    filter_complex = (
        "[0:a]aresample=44100,loudnorm=I=-14:LRA=11:TP=-1.5,asplit[vo_norm][vo_sc]; "
        "[1:a]aresample=44100,volume=0.7[bgm_resampled]; "
        "[bgm_resampled][vo_sc]sidechaincompress=threshold=-15dB:ratio=4:attack=50:release=300:makeup=2[bgm_ducked]; "
        "[bgm_ducked][vo_norm]amix=inputs=2:duration=first:weights=1 1,alimiter=limit=0.95[a_out]"
    )
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[a_out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_audio
    ])
    
    run_ffmpeg(cmd, "Final Audio Mix (Sidechain Ducking)")

"""

new_content = content[:start_idx] + new_func + content[end_idx:]

with open("/home/ankit-poddar/Documents/Video_Projects/py_files/renderer_long.py", "w") as f:
    f.write(new_content)
