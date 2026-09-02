import json
import os
import re

CAMERA_MOVEMENTS = {
    1: "push_in",
    2: "push_out",
    3: "pan_left",
    4: "pan_right",
    5: "tilt_up",
    6: "tilt_down",
    7: "zoom_in_left",
    8: "zoom_in_right",
    9: "zoom_out_left",
    10: "zoom_out_right",
    11: "static"
}

TRANSITIONS = {
    "A": "cut",
    "B": "dissolve",
    "C": "flashback_fade",
    "D": "fade_to_black"
}

def print_menu():
    print("\n" + "="*50)
    print("🎥 MANUAL REVIEW: EFFECTS & TRANSITIONS")
    print("="*50)
    print("Effects (Camera Movements):")
    print(" 1 = push_in       2 = push_out       3 = pan_left      4 = pan_right")
    print(" 5 = tilt_up       6 = tilt_down      7 = zoom_in_left  8 = zoom_in_right")
    print(" 9 = zoom_out_left 10= zoom_out_right 11= static")
    print("\nTransitions:")
    print(" A = cut           B = dissolve")
    print(" C = flashback     D = fade_to_black")
    print("\nFormat: <EffectNum><TransitionLetter><Duration>")
    print("Example: '2A0' or '2 A 0' (push_out, cut, 0s)")
    print("Press ENTER to use default (1 A 0.0)")
    print("="*50 + "\n")

def parse_input(user_in):
    user_in = user_in.strip().upper()
    if not user_in:
        return 1, "A", 0.0
    
    # Remove all spaces to unify parsing: "10 B 0.5" -> "10B0.5"
    user_in = user_in.replace(" ", "")
    
    # Regex: (digits)(letter)(digits or decimal)
    match = re.match(r'^(\d+)([A-D])([\d\.]+)$', user_in)
    if not match:
        return None
        
    eff_num = int(match.group(1))
    trans_letter = match.group(2)
    
    try:
        duration = float(match.group(3))
    except ValueError:
        return None
        
    if eff_num not in CAMERA_MOVEMENTS:
        return None
        
    return eff_num, trans_letter, duration

def run_manual_review(blueprint_path):
    if not os.path.exists(blueprint_path):
        print(f"  ❌ Blueprint not found at {blueprint_path}")
        return False
        
    with open(blueprint_path, "r") as f:
        bp_data = json.load(f)
        
    # Check if already reviewed
    if bp_data.get("manual_review_completed"):
        ans = input("  ➤ You have already completed the manual review for this video. Do you want to redo it? (y/N): ").strip().lower()
        if ans != "y":
            print("  ✅ Skipping manual review (using saved choices).")
            return True
            
    scenes = bp_data.get("video_blueprint", []) or bp_data.get("scenes", [])
    if not scenes:
        return True
        
    print_menu()
    
    for s in scenes:
        sid = s.get("scene_id")
        prompt = s.get("visual_prompt", "")
        # truncate prompt for display
        if len(prompt) > 80: prompt = prompt[:77] + "..."
        
        while True:
            print(f"\n🎬 Scene ID {sid}: {prompt}")
            user_in = input("➤ Choice (Effect, Transition, Duration): ")
            
            parsed = parse_input(user_in)
            if parsed is None:
                print("  ❌ Invalid input. Please try again (e.g. 1A0 or 3 B 1.5).")
            else:
                eff_num, trans_letter, duration = parsed
                s["camera_movement"] = CAMERA_MOVEMENTS[eff_num]
                s["transition_type"] = TRANSITIONS[trans_letter]
                s["transition_duration"] = duration
                print(f"  ✅ Saved: {s['camera_movement']} | {s['transition_type']} | {duration}s")
                break
                
    bp_data["manual_review_completed"] = True
    
    with open(blueprint_path, "w") as f:
        json.dump(bp_data, f, indent=4)
        
    print("\n✅ All manual choices saved successfully!\n")
    return True
