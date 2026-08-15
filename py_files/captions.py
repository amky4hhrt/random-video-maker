import os
import json
import re

MIN_WORDS_PER_GROUP_SHORT = 2
MAX_WORDS_PER_GROUP_SHORT = 2
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

def _fix_devanagari_shaping(text):
    """
    Manually swaps the logical order of Devanagari short 'i' matra (U+093F)
    with its preceding consonant so that libass draws it in the correct visual order.
    """
    return re.sub(r'([\u0915-\u0939]\u093C?)\u093F', r'\u093F\1', text)

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
        raw_word = _fix_devanagari_shaping(w["word"].strip())
        if not use_karaoke:
            runs.append(raw_word)
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
            run = "{" + open_tags + "}" + raw_word
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

    # NOTE: captions are chunked from the FULL, continuous word list — not
    # bucketed per video scene. Scene cuts are a visual concern; sentences and
    # phrases don't stop for them. Pre-splitting words by scene boundary (the
    # old approach) meant a caption group could never span a scene cut, so a
    # phrase that happened to straddle one got torn in half — the second
    # caption card would start on whatever word landed just after the cut,
    # even if that word was a mid-sentence fragment (e.g. a lone Hindi
    # postposition like "के"/"में"/"से" with its noun stranded on the
    # previous card). Chunking continuously avoids that entirely.
    transcript_words = transcript_data.get("words", transcript_data) if isinstance(transcript_data, dict) else transcript_data
    all_words = [w for w in transcript_words if isinstance(w, dict) and "word" in w]
    all_words.sort(key=_word_start)

    flat_groups = _chunk_words_for_captions(all_words, max_chars, is_short) if all_words else []

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
