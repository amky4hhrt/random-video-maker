import os
import subprocess
import json
import time
import difflib
import re
from faster_whisper import WhisperModel
from pathlib import Path

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

def chunk_audio(input_file, chunk_dir):
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for f in chunk_dir.glob("*.wav"):
        f.unlink()
        
    print(f"  \u2139\uFE0F Splitting {input_file} into 30-second chunks...")
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file), 
        "-f", "segment", 
        "-segment_time", "30", 
        "-c:a", "pcm_s16le", 
        str(chunk_dir / "chunk_%03d.wav")
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    chunks = sorted(chunk_dir.glob("chunk_*.wav"))
    return [str(c) for c in chunks]

def transcribe_chunks_words(chunks, language="en"):
    print(f"  \U0001F399\uFE0F Loading Whisper large-v3 ({language}) on GPU...")
    model = WhisperModel("large-v3", device="cuda", compute_type="float16")
    
    all_words = []
    
    for i, chunk in enumerate(chunks):
        base_time = i * 30.0
        
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
                all_words.append({
                    "start": base_time + word.start,
                    "end": base_time + word.end,
                    "word": word.word.strip()
                })
            
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
