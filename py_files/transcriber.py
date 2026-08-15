import os
import subprocess
import json
import time
import math
import difflib
import re
from faster_whisper import WhisperModel
from pathlib import Path

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Chunks overlap so no word ever sits at a hard, context-free edge in every
# chunk that covers it. A word landing at t=30.0s used to be the very last
# fragment of chunk 0 (no trailing audio) AND simply didn't exist in chunk 1
# (started at 30.0s exactly) — easy for Whisper's VAD to drop as noise on
# both sides. With OVERLAP_SECONDS, that same word sits in the middle of one
# of the two chunks that contain it, where Whisper has full context on both
# sides. We then keep only the copy of each word drawn from whichever chunk
# is farthest from ITS edges (see _authoritative_zone below), so overlapping
# regions never produce duplicates.
CHUNK_SECONDS = 30.0
OVERLAP_SECONDS = 4.0
STEP_SECONDS = CHUNK_SECONDS - OVERLAP_SECONDS


def _probe_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout.strip()
    return float(out)


def chunk_audio(input_file, chunk_dir):
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for f in chunk_dir.glob("*.wav"):
        f.unlink()

    total_dur = _probe_duration(input_file)
    print(f"  \u2139\uFE0F Splitting {input_file} into {CHUNK_SECONDS:.0f}s chunks "
          f"with {OVERLAP_SECONDS:.0f}s overlap ({total_dur:.1f}s total)...")

    n_chunks = max(1, math.ceil(max(0.0, total_dur - OVERLAP_SECONDS) / STEP_SECONDS))
    chunk_paths = []
    for i in range(n_chunks):
        start = i * STEP_SECONDS
        if start >= total_dur:
            break
        out_path = chunk_dir / f"chunk_{i:03d}.wav"
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-i", str(input_file),
            "-t", str(CHUNK_SECONDS), "-c:a", "pcm_s16le", str(out_path)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if out_path.exists():
            chunk_paths.append(str(out_path))

    return chunk_paths


def _authoritative_zone(i, n_chunks):
    """Time range (in the full-length timeline) for which chunk i's words are
    kept. Chunks overlap by OVERLAP_SECONDS with their neighbors; each
    overlapping word is credited to whichever chunk has it closer to that
    chunk's center rather than its edge, so we split each overlap at its
    midpoint."""
    zone_start = 0.0 if i == 0 else (i * STEP_SECONDS) + (OVERLAP_SECONDS / 2)
    zone_end = float("inf") if i == n_chunks - 1 else ((i + 1) * STEP_SECONDS) + (OVERLAP_SECONDS / 2)
    return zone_start, zone_end


def transcribe_chunks_words(chunks, language="en"):
    print(f"  \U0001F399\uFE0F Loading Whisper large-v3 ({language}) on GPU...")
    model = WhisperModel("large-v3", device="cuda", compute_type="float16")

    all_words = []
    n_chunks = len(chunks)

    for i, chunk in enumerate(chunks):
        base_time = i * STEP_SECONDS
        zone_start, zone_end = _authoritative_zone(i, n_chunks)

        segments, info = model.transcribe(
            chunk,
            language=language,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=200, speech_pad_ms=100),
            word_timestamps=True
        )

        for segment in segments:
            print(f"    [{round(base_time + segment.start, 1)}s -> {round(base_time + segment.end, 1)}s] {segment.text}")
            for word in segment.words:
                w_start = base_time + word.start
                # Skip words that fall in this chunk's overlap edge — they'll
                # be (or already were) picked up from the neighboring chunk
                # where they sit further from that chunk's own edge.
                if not (zone_start <= w_start < zone_end):
                    continue
                all_words.append({
                    "start": w_start,
                    "end": base_time + word.end,
                    "word": word.word.strip()
                })

    all_words.sort(key=lambda w: w["start"])
    return all_words

def tokenize(text):
    words = []
    for m in re.finditer(r'\S+', text):
        words.append(m.group(0))
    return words

def align_and_correct(rough_words_data, story_text):
    story_words = tokenize(story_text)
    rough_words = [w["word"] for w in rough_words_data]
    
    sm = difflib.SequenceMatcher(None, story_words, rough_words)
    
    final_output = []
    last_story_idx = 0
    
    for block in sm.get_matching_blocks():
        story_start = block.a
        rough_start = block.b
        size = block.size
        
        if size == 0:
            continue
            
        if story_start > last_story_idx:
            missing_count = story_start - last_story_idx
            if len(final_output) > 0:
                prev_time = final_output[-1].get("end_time", final_output[-1].get("end"))
            else:
                prev_time = max(0, rough_words_data[rough_start]["start"] - 0.5)
                
            next_time = rough_words_data[rough_start]["start"]
            time_per_word = max(0.1, (next_time - prev_time) / (missing_count + 1))
            
            for i in range(missing_count):
                interp_start = prev_time + (i * time_per_word)
                interp_end = prev_time + ((i + 1) * time_per_word)
                final_output.append({
                    "word": story_words[last_story_idx + i],
                    "start": round(interp_start, 3),
                    "end": round(interp_end, 3)
                })
                
        for i in range(size):
            r_data = rough_words_data[rough_start + i]
            final_output.append({
                "word": story_words[story_start + i],
                "start": round(r_data["start"], 3),
                "end": round(r_data["end"], 3)
            })
            
        last_story_idx = story_start + size
        
    if last_story_idx < len(story_words):
        missing_count = len(story_words) - last_story_idx
        prev_time = final_output[-1].get("end_time", final_output[-1].get("end")) if final_output else 0.0
        for i in range(missing_count):
            interp_start = prev_time + (i * 0.3)
            interp_end = prev_time + ((i + 1) * 0.3)
            final_output.append({
                "word": story_words[last_story_idx + i],
                "start": round(interp_start, 3),
                "end": round(interp_end, 3)
            })
            
    return final_output

def generate_transcript(audio_file_path, story_file_path, output_json_path, language="en"):
    """
    Main entry point for supervisor to generate transcript.json.
    """
    print(f"\n\U0001F4DD Starting transcription for {language.upper()}...")
    audio_path = Path(audio_file_path)
    story_path = Path(story_file_path)
    out_path = Path(output_json_path)
    
    if not audio_path.exists():
        print(f"  \u274C Missing audio file: {audio_path}")
        return False
        
    chunk_dir = audio_path.parent / "temp_chunks"
    
    try:
        with open(story_path, "r", encoding="utf-8") as f:
            story_text = f.read()
            
        chunks = chunk_audio(audio_path, chunk_dir)
        rough_words = transcribe_chunks_words(chunks, language=language)
        
        print("  \u2699\uFE0F Aligning exact story text with AI timestamps...")
        corrected_words = align_and_correct(rough_words, story_text)
        
        output_data = {
            "full_transcript": story_text,
            "words": corrected_words
        }
        
        with open(out_path, "w", encoding="utf-8") as json_file:
            json.dump(output_data, json_file, ensure_ascii=False, indent=4)
            
        print(f"  \u2705 Transcript compiled successfully -> {out_path}")
        return True
        
    finally:
        for f in chunk_dir.glob("*.wav"):
            f.unlink()
        try:
            chunk_dir.rmdir()
        except OSError:
            pass
