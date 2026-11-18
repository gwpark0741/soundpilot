# Mockup brief — v2a_inspect demo UI (2026-11-18)

Build a **throwaway static HTML mockup** (not app code) of the demo screen described in
`docs/superpowers/specs/2026-11-18-demo-upgrade-design.md` §5.4, fixing the problems in
`docs/superpowers/specs/2026-11-18-ui-review.md`. The user will look at it and react before any
React code is written. Read both docs first.

## Output
- `docs/mockups/2026-11-18-demo-ui.html` — one self-contained file (inline CSS, minimal inline JS
  only for the demo-state switcher and toggles; no external libraries, no web fonts — use the
  system stack `ui-sans-serif, system-ui, …` and drop "Inter").
- `docs/mockups/assets/frame.jpg` — one frame of `samples/sample.mp4` at ~4.0 s
  (`ffmpeg -ss 4 -i samples/sample.mp4 -frames:v 1 -q:v 3 …`). Look at it and draw the object
  highlight masks (SVG polygons/ellipses over the image) roughly on the player character and the
  enemy creature you see.
- Screenshots next to it: `docs/mockups/shots/{desktop-1440x900,laptop-1280x720,narrow-320}-{audio,silent}.png`
  via headless Chromium/Playwright (a cached Playwright Chromium exists from an earlier review
  under the user's cache; install into a scratch venv under
  /tmp/claude-1016/-home-gwpark0741/5c9f0520-36e9-4a5f-979a-1d2da8f69b1d/scratchpad/ if needed).

## Data (use these real sample values)
Video 7.1 s, 213 frames, 30 fps. Tracks (event frame ranges):
- Player Damage Impact — sfx — events 75–77, 97–99, 121–123, 189–191 — source: Player Character
- Player Footsteps — sfx — event 0–213 — source: Player Character
- Background Wind Ambience — ambience — event 0–213 — source: Scene Global Environment (no mask)
- Player Hurt Grunt — sfx — events 75–77, 97–99, 121–123, 189–191 — source: Player Character
Use fake but plausible waveforms (SVG paths) for stems.

## Layout (1440×900 and 1280×720: no page scroll; everything for listen + regenerate visible)
```
┌ Top bar: "V2A Inspect" | [Open video] [Load bundle] (secondary) | status (role=status, stage text + progress bar) | [Generate audio] (the ONLY primary button) ┐
├ Video stage + SAM3 mask overlay (selected track's object highlighted, others dim/hidden) ┬ Track panel (own scroll if needed) ┤
│ transport: play/pause, frame slider, "1.20 s / 7.10 s"                                   │ one card per track + residual row   │
├ Timeline: SOUND lanes first (one per track, waveform + event bars), then a collapsed "Scenes & tracking" group ┤
```
Track card (selected card expanded, others compact):
- color swatch matching its mask + track name + type (SFX/Ambience/Speech)
- method badge with TEXT (never color alone): "Separated" / "SelVA w/ SelSync" / "TTS" / "ElevenLabs"
- seed (generated) or attempt (separated), e.g. "seed 1834" / "attempt 2"
- status with text + icon: Done / Running… (stage) / Failed — <error> + what to do next
- controls: Play (named "Play Player Footsteps"), Solo, Mute (aria-pressed), description field (editable textarea)
- actions: separated track → [Re-separate] [Switch to generation]; generated track → [Regenerate] with an optional seed input inside a "Seed" disclosure; a speech track would be [Re-separate]/[TTS] (not shown in sample)
- Residual row ("Residual (original minus separated stems)") with an on/off switch, only in the audio scenario.

## States to show (a small switcher OUTSIDE the app frame, top-right, clearly labelled "Mockup state")
1. Audio video: all tracks Separated, Player Footsteps selected, attempt 2; Player Damage Impact = SelVA w/ SelSync (switched), Done; residual off.
2. Silent video: all SelVA w/ SelSync seed 42 except selected Player Footsteps seed 48728754; no residual row.
3. Running: one track "Running… generating 2/4", others disabled with reason text.
4. Failed: Player Hurt Grunt failed — "Audyssey tools server unreachable. Check the tools server, then press Regenerate." previous stem kept ("Playing previous result").

## Visual direction
Keep the existing dark theme (`web/src/styles.css`: bg #101114, panels #17191f, borders #2a2d35, text #e7e9ee, muted #9aa3b2, gold accent) but make it look like a product, not a dev tool: one primary, consistent 13 px control text (12 px minimum), two radii (controls 6 px, panels 10 px), hover/press/focus-visible states, 24 px+ targets, AA contrast (4.5:1 text), no color-only meaning, `prefers-reduced-motion` respected. Load and follow the skills `frontend-design:frontend-design`, `better-layout`, `better-ui`, `better-typography`, `better-colors`, `better-accessibility`, `better-writing` (UI copy in English, sentence case).
Narrow (≤ 900 px): stack video → track panel → timeline, page may scroll, nothing clipped at 320 px.

## Rules
Do not modify any file outside `docs/mockups/`. Do not commit. No subagents. Report: file paths, what each state shows, any spec point you could not fit, under 15 lines.
