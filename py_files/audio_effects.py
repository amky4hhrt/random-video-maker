import subprocess
import os

def apply_cinematic_reverb(input_audio, output_audio):
    """
    Applies a subtle cinematic room reverb (echo) to a voiceover track.
    Returns True if successful, False otherwise.
    """
    print(f"  🎬 Applying Cinematic Reverb to voiceover...")
    
    # aecho=0.8:0.88:20:0.1 -> A very fast, subtle early reflection
    # 0.8 in_gain, 0.88 out_gain, 20ms delay, 0.1 decay (very short decay so it doesn't sound like a cave)
    
    cmd = [
        "ffmpeg", "-y", "-i", input_audio,
        "-af", "aecho=0.8:0.88:20:0.1",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_audio
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0
