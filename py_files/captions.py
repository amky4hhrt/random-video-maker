import os
import json

MIN_WORDS_PER_GROUP_SHORT = 2
MAX_WORDS_PER_GROUP_SHORT = 3
MIN_WORDS_PER_GROUP_LONG = 3
MAX_WORDS_PER_GROUP_LONG = 4
MAX_CHARS_PER_GROUP_SHORT = 18       # portrait (1080x1920)
MAX_CHARS_PER_GROUP_LANDSCAPE = 40   # landscape (1920x1080)
MIN_WORD_HIGHLIGHT_SECONDS = 0.35

COLOR_BASE = "&H007B99F0&"
COLOR_HIGHLIGHT = "&H007B99F0&"
TEXT_OUTLINE_COLOUR = "&H00000000&"
TEXT_SHADOW_COLOUR = "&H00000000&"
OUTLINE_WIDTH_FACTOR = 0.065
SHADOW_DEPTH_FACTOR = 0.02

HIGHLIGHT_POP_ENABLED = False
HIGHLIGHT_POP_SCALE = 108
HIGHLIGHT_POP_MS = 90

def _word_start(w):
    return w.get("start", w.get("start_time", 0.0))

def _word_end(w):
    return w.get("end", w.get("end_time", 0.0))

def _chunk_words_for_captions(words, max_chars, is_short):
    min_words = MIN_WORDS_PER_GROUP_SHORT if is_short else MIN_WORDS_PER_GROUP_LONG
    max_words = MAX_WORDS_PER_GROUP_SHORT if is_short else MAX_WORDS_PER_GROUP_LONG
    
    groups = []
    i, n = 0, len(words)
    while i < n:
        current = [words[i]]
        current_len = len(words[i]["word"].strip())
        j = i + 1
        while j < n and len(current) < max_words:
            candidate = words[j]["word"].strip()
            projected_len = current_len + 1 + len(candidate)
            if projected_len > max_chars and len(current) >= min_words:
                break
            current.append(words[j])
            current_len = projected_len
            j += 1
            if projected_len > max_chars:
                break
        groups.append(current)
        i = j
    return groups

def _ms(seconds):
    return max(0, round(seconds * 1000))

def _render_group_dialogue(group, group_start, highlight_windows, use_karaoke):
    runs = []
    n = len(group)
    for idx, w in enumerate(group):
        if not use_karaoke:
            runs.append(w["word"].strip())
        else:
            hl_start, hl_end = highlight_windows[idx]
            rel_start_ms = _ms(hl_start - group_start)
            rel_end_ms = _ms(hl_end - group_start)
            is_last = idx == n - 1
            
            # When karaoke is on, base is Grey, highlight is Amber
            c_base = "&H00E6E6E6&"
            c_highlight = "&H007B99F0&"

            open_tags = "\\c" + c_base
            open_tags += f"\\t({rel_start_ms},{rel_start_ms + 1},\\c{c_highlight})"
            if HIGHLIGHT_POP_ENABLED:
                pop_peak_ms = rel_start_ms + HIGHLIGHT_POP_MS
                open_tags += (
                    f"\\t({rel_start_ms},{pop_peak_ms},\\fscx{HIGHLIGHT_POP_SCALE}\\fscy{HIGHLIGHT_POP_SCALE})"
                    f"\\t({pop_peak_ms},{pop_peak_ms + HIGHLIGHT_POP_MS},\\fscx100\\fscy100)"
                )
            run = "{" + open_tags + "}" + w["word"].strip()
            if not is_last:
                run += "{" + f"\\t({rel_end_ms},{rel_end_ms + 1},\\c{c_base})" + "}"
            runs.append(run)
    return " ".join(runs)

def generate_subtitle_file(transcript_data, blueprint_data, output_path, is_short, font_name="Montserrat Black", use_karaoke=False):
    print("\n\U0001F7E2 Generating Captions...")
    res_x, res_y = (1080, 1920) if is_short else (1920, 1080)
    font_size = 150 if is_short else 114
    max_chars = MAX_CHARS_PER_GROUP_SHORT if is_short else MAX_CHARS_PER_GROUP_LANDSCAPE
    margin_l, margin_r = 80, 80
    margin_v = 160 if is_short else 90
    outline_width = round(font_size * OUTLINE_WIDTH_FACTOR, 1)
    shadow_depth = round(font_size * SHADOW_DEPTH_FACTOR, 1)
    
    # Base color in Style header: if not karaoke, make everything Amber by default!
    header_color = "&H00E6E6E6&" if use_karaoke else COLOR_BASE

    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: BottomCaptionStyle,{font_name},{font_size},{header_color},{header_color},{TEXT_OUTLINE_COLOUR},{TEXT_SHADOW_COLOUR},-1,0,0,0,100,100,0,0,1,{outline_width},{shadow_depth},2,{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"""

    def format_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h:01d}:{m:02d}:{s:05.2f}"

    blueprint_scenes = blueprint_data.get("video_blueprint", []) or blueprint_data.get("scenes", [])
    
    scenes_groups = []
    for i, scene in enumerate(blueprint_scenes):
        scene_start = scene.get("start_time", 0.0)
        if i < len(blueprint_scenes) - 1:
            boundary_end = blueprint_scenes[i + 1].get("start_time", 0.0)
        else:
            boundary_end = float('inf')

        transcript_words = transcript_data.get("words", transcript_data) if isinstance(transcript_data, dict) else transcript_data
        scene_words = []
        for word_entry in transcript_words:
            if isinstance(word_entry, dict) and "word" in word_entry:
                w_start = _word_start(word_entry)
                if scene_start <= w_start < boundary_end:
                    scene_words.append(word_entry)

        if not scene_words:
            continue
        groups = _chunk_words_for_captions(scene_words, max_chars, is_short)
        scenes_groups.append(groups)

    flat_groups = []
    for groups in scenes_groups:
        flat_groups.extend(groups)

    for gi, group in enumerate(flat_groups):
        group_start = _word_start(group[0])
        n = len(group)
        next_group_start = _word_start(flat_groups[gi + 1][0]) if gi + 1 < len(flat_groups) else None

        highlight_windows = []
        for i, w in enumerate(group):
            start = _word_start(w)
            desired_end = max(_word_end(w), start + MIN_WORD_HIGHLIGHT_SECONDS)
            if i + 1 < n:
                next_start = _word_start(group[i + 1])
            else:
                next_start = next_group_start

            if next_start is not None:
                end = min(desired_end, next_start)
                end = max(end, start + 0.01)
            else:
                end = desired_end
            highlight_windows.append((start, end))

        event_start = group_start
        if next_group_start is not None:
            event_end = min(highlight_windows[-1][1] + 1.5, next_group_start - 0.05)
        else:
            event_end = highlight_windows[-1][1] + 1.5

        line_text = _render_group_dialogue(group, group_start, highlight_windows, use_karaoke)
        ass_content += f"Dialogue: 0,{format_time(event_start)},{format_time(event_end)},BottomCaptionStyle,,0,0,0,,{line_text}\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    print(f"  \u2705 Captions written to {output_path}")
    return output_path
