---
name: flow-prototype-extension
description: >-
  Documentation and standard operating procedures for maintaining and extending 
  the Flow Prototype Chrome Extension (Google Flow AI Auto-Prompter).
---

# Flow Prototype Extension Guide

This skill provides comprehensive documentation on the inner workings of the `Flow_Prototype` Chrome Extension, specifically detailing the robust error-handling mechanisms built to bypass Google Flow's safety policies via the Gemini API.

## Core Features & Architecture

### 1. Dual-Mode Interface
The extension UI supports two distinct operational modes:
*   **Test Mode:** Allows manual submission of a single prompt and optional character ID(s) to verify the fallback logic without running a full JSON payload.
*   **Full Automation:** Processes a 40-scene `video_blueprint.json` end-to-end, parsing scene objects and automatically triggering the fallback loop on any failed scene.
*   **Dynamic 10s Polling & Early Exit (`pollGenerationStatus`):** Replaces rigid countdown timers with a 10-second polling cycle (up to 6 checks = 60s max). The moment newly generated images appear, they are automatically downloaded and the prompter immediately advances to the next scene. If no image or error appears after 6 checks (60s), it times out and triggers fallback or cleanly skips.

### 2. The Gemini Fallback Loop (`askGeminiForVariations`)
When a policy error or timeout is detected, the extension calls the `gemini-3.5-flash` model via `generativelanguage.googleapis.com/v1beta`.
*   It feeds the AI the exact blocked prompt and asks for 5 sanitized variations formatted as a JSON array.
*   It then loops through each variation (typing the new text and re-typing any `characters_present` `@mentions`).
*   It monitors each variation using the same dynamic 10s polling cycle (max 6 checks / 60s). If an image generates, it downloads it and advances. If an error is caught or it times out, it moves to the next variation. If all variations fail or time out, the scene is skipped cleanly without blocking.

### 3. The Shadow-Piercing Error Counter (`getErrorCount`)
Google Web Components often utilize deeply nested Shadow DOMs that cannot be searched via standard `MutationObserver` or `document.body.innerText`.
*   **Solution:** A recursive `TreeWalker` (`getVisibleText`) penetrates all open Shadow DOM boundaries to extract raw text nodes, stripping away HTML tags.
*   It uses Regex to count occurrences of the exact error string: `"This generation might violate our policies"`.

### 4. The Memory Wiper (Ghost Bug Fix)
Because Google Flow preserves old failed posts in the visual feed, the error counter would constantly trigger false positives (The Ghost Bug).
*   **Solution:** The `wipeOldErrors()` function executes exactly milliseconds before simulating the Hardware Enter key.
*   It recursively walks the DOM (piercing Shadow DOMs) and explicitly overwrites the `nodeValue` of any old error text nodes to `""`. 
*   This guarantees the error count is always exactly `0` when a prompt is submitted, so if the count becomes `> 0`, it is a mathematically guaranteed new failure.

### 5. React-Safe Text Wiping
Attempting to clear a React-controlled `contenteditable` component by setting `.textContent = ""` will destroy the internal virtual DOM state, causing subsequent keyboard events to fail.
*   **Solution:** We use a Native Browser Selection approach:
    ```javascript
    const range = document.createRange();
    range.selectNodeContents(editor);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('delete', false, null);
    ```

### 6. Collapsible Floating Panel (Minimize / Maximize)
To prevent the extension widget from obstructing the Google Flow generation canvas or text editor:
*   **One-Click Toggle:** Clicking the header bar or the `[ — ]` / `[ ＋ ]` button toggles between full view (300px width) and a compact bottom-right pill (`auto` width).
*   **Live Minimized Status:** A `MutationObserver` watches `#flow-status` and surfaces a compact progress indicator in the minimized header (e.g. `• Scene 3 rendering...`) so users stay informed without taking up screen space.
*   **State & Key Persistence:** The panel remembers its minimized/maximized state and auto-saves the entered Gemini API key in `localStorage` across page reloads.

### 7. Robust Generation Detection & Guaranteed JPEG Pipeline
Solves early false-positive image detection, unwanted thumbnail downloads, and WebM/missing-extension errors:
*   **Configurable "Images / Scene" Selector:** Users can choose how many images to save per prompt (defaults to `2`, range `1` to `4`), with the preference saved in `localStorage`.
*   **Immutable URL Set Snapshotting (`initialUrls = new Set()`):** Snapshots all existing image and video URLs before pressing Enter. Completely immune to React unmounting/remounting DOM nodes, preventing older scene images from ever being falsely detected as newly rendered images.
*   **Active Generation Awareness (`isGoogleFlowGenerating`):** Actively monitors Google Flow's progress spinners, circular loading rings, and stop/cancel buttons. The polling loop will **never** declare victory while Google Flow is actively rendering.
*   **Strict Native Resolution Filter:** Requires native pixel dimensions `naturalWidth >= 512` and `naturalHeight >= 512` (with `complete === true`), discarding loading skeleton shimmers and CSS layout placeholders (`clientWidth`).
*   **Character Mention Modal Auto-Dismissal (`handleCharacterMention`):** Automatically detects and clicks Google Flow's **"Add to prompt"** button strictly within active modal dialogs (`[role="dialog"]`), avoiding header icon buttons (such as the top-right `+` Add menu).
*   **Dual-Action Prompt Submitter (`submitPrompt`):** Re-focuses the prompt editor, dispatches hardware Enter via `chrome.debugger`, and automatically clicks the circular submit arrow button (`➔`) beside the prompt box to guarantee generation triggers every time.
*   **Guaranteed JPEG Canvas Pipeline:** Prior to downloading, the rendered media is drawn onto an offscreen HTML5 `<canvas>` and exported as `image/jpeg` Data URLs (`canvas.toDataURL("image/jpeg", 0.95)`). This guarantees Chrome never downloads video streams, strips extensions (fixing file `97`), or misidentifies MIME types.
*   **Blueprint `scene_id` Resolution:** Uses `scene.scene_id` directly from the blueprint JSON rather than the loop index `i + 1`. This ensures filtered ranges (e.g. running Scene 2) name files correctly.
*   **Single vs Multiple Naming:** If 1 image is saved per prompt, files are cleanly named `{scene_id}.jpg` (e.g. `1.jpg`, `2.jpg`). If multiple images are saved, files use the `_x` candidate numbering (e.g. `1_1.jpg`, `1_2.jpg`, `2_1.jpg`, `2_2.jpg`).
*   **Strict Slicing:** Only downloads the first $N$ primary candidate images and prevents downloading extra thumbnails.

## Maintenance Notes
*   **Debugger Detachment:** `background.js` manages a `chrome.debugger` instance to send raw `windowsVirtualKeyCode: 13`. It attaches, fires the event, and immediately detaches. This is necessary to bypass trusted-event listeners without permanently holding the debugger and blocking user DevTools.
*   **Character Mentions:** The wait times in `pasteText` and `pressKey` (1500ms to 2000ms) are critical. The Google Flow `@mention` dropdown is slow to hydrate; reducing these delays will cause the Enter key to fire before the character is selected.

