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

### 2. The Gemini Fallback Loop (`askGeminiForVariations`)
When a policy error is detected, the extension calls the `gemini-3.5-flash` model via `generativelanguage.googleapis.com/v1beta`.
*   It feeds the AI the exact blocked prompt and asks for 5 sanitized variations formatted as a JSON array.
*   It then loops through each variation (typing the new text and re-typing any `characters_present` `@mentions`).
*   It monitors each variation for 45 seconds. If an error is caught, it moves to the next. If it passes, it breaks the loop and resumes standard execution.

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

## Maintenance Notes
*   **Debugger Detachment:** `background.js` manages a `chrome.debugger` instance to send raw `windowsVirtualKeyCode: 13`. It attaches, fires the event, and immediately detaches. This is necessary to bypass trusted-event listeners without permanently holding the debugger and blocking user DevTools.
*   **Character Mentions:** The wait times in `pasteText` and `pressKey` (1500ms to 2000ms) are critical. The Google Flow `@mention` dropdown is slow to hydrate; reducing these delays will cause the Enter key to fire before the character is selected.

