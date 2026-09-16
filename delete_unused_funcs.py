import re

with open("/home/ankit-poddar/Documents/Video_Projects/py_files/renderer_long.py", "r") as f:
    content = f.read()

# Delete build_volume_breakpoints, build_volume_expr, build_music_bed, build_sfx_bed
start_str = "# ─── Audio Ducking & Bed ──────────────────────────────────────────"
end_str = "def generate_audio_mix("

start_idx = content.find(start_str)
end_idx = content.find(end_str)

new_content = content[:start_idx] + "\n# ─── Final Audio Mix ──────────────────────────────────────────────\n" + content[end_idx:]

with open("/home/ankit-poddar/Documents/Video_Projects/py_files/renderer_long.py", "w") as f:
    f.write(new_content)
