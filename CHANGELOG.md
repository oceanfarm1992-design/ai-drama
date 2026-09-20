# Changelog

## 2026-09-16 — 2026-09-20: Bini's Real Ocean launch

This session's work is already live in production — everything below was
committed and pushed directly to `master` throughout (this repo's
established workflow for the pipeline code), and the corresponding
`ai-drama-showcase` changes shipped via three merged PRs (#1, #2, #3) in
that repo. This changelog is a session summary for reviewers, not a
staged/unmerged change set.

### Character rig
- Added natural eye-blink animation for Bini (mouth-viseme-style erase +
  redraw technique, ~every 2.5–4.5s). Gated behind a per-character
  `blink_ready` flag — Tula/Ollo/Dodo/Pipi have detected eye coordinates
  but their more elongated eye shapes need further per-character patch
  tuning before their blinks are enabled.
- Fixed character vertical positioning so the AI-generated background
  creature is no longer hidden behind the talking character.

### New series: "Bini's Real Ocean"
Real stock-footage nature clips (Pexels, Pixabay fallback) with a
character narrating over them, instead of the main SEABINI series'
AI-generated cartoon backgrounds.

- `bini_real_footage.py` — searches/downloads/normalizes real video clips
  per behavior topic.
- `bini_real_render.py` — composites the rigged character as a small
  corner "narrator bubble" over the real footage (never centered, since
  real footage isn't directed and the subject creature's position is
  unknown).
- `bini_real_script.py` — generates a 3–4 segment, behavior-based episode
  script per creature (swim/eat/glow/defend), with hard safety rules
  against any predator/violence content since this is real footage, not
  cartoon.
- `bini_real_episode.py` — orchestrates script → footage → render →
  stitch into one episode.

### Song generation
- Switched the underlying model from `minimax/music-01` to
  `minimax/music-2.5` for better vocal quality (A/B tested with the user
  on real lyrics). Lost per-voice cloning in the process; voice
  consistency now comes from a fixed style-description prompt instead.
- Added a sung closing number to Bini's Real Ocean episodes, using the
  same song pipeline as the main SEABINI series.
- The song's dance scene now renders over real footage (reusing one of
  the episode's own clips) instead of switching to a static cartoon
  background, keeping the whole episode visually consistent.

### Multi-character expansion
- All 5 SEABINI characters (Bini, Tula, Ollo, Dodo, Pipi) now each
  narrate their own daily Real Ocean episode, in their own voice and
  personality. Only Bini's episode includes the song.
- 5 episodes/day total, scheduled at irregular off-hour times (02:15,
  06:40, 10:55, 15:25, 20:10 UTC) to avoid GitHub Actions' documented
  on-the-hour scheduling congestion and spread load/audience reach
  across the day.
- Publish captions now name the actual narrator instead of always
  crediting Bini.

### Reliability fixes
- Root-caused and fixed the daily workflow never actually firing on
  schedule (GitHub Actions delays/drops crons pinned to `:00`); moved to
  off-hour times.
- Fixed episodes always defaulting to "seahorse" — the script generator's
  own prompt describes Bini as a seahorse, which biased the LLM's "pick
  any creature" choice. Creature selection is now done by the
  orchestrator via true random selection, excluding whatever the most
  recent episode covered.
- Fixed a missing `REPLICATE_API_TOKEN` in the Real Ocean workflow once
  song generation started needing it.
- Hardened `_replicate_key()` to fail with a clear message instead of an
  unhandled `FileNotFoundError` when a secret is missing.

### Retired
- The original SEABINI cartoon-story daily workflow has been disabled
  (not deleted) — Bini's Real Ocean is now the primary series per user
  direction.
