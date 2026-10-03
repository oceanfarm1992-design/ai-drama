"""Episode-script generator for "Bini's Real Ocean" — Bini narrates 3-4
distinct BEHAVIORS of one real sea creature (swim, glow, eat, defend...),
each segment paired with its own stock-footage search query so the episode
cuts between different real clips instead of reusing one static scene.

Same "general LLM + house bible in the system prompt" approach as
seabini_script.py, reusing its curated creature list and OpenAI key
handling. ~$0.001/episode.
"""
import os, sys, json, urllib.request, pathlib

import seabini_script as ss  # reuse _openai_key() and the verified creature facts

# Each has 10+ free stock clips whose Pexels/Pixabay metadata actually names it
# (checked 2026-09-24); gentle animals only, no sharks-that-bite/orcas/eels.
CREATURES = [
    "jellyfish", "crab", "starfish", "octopus", "seahorse", "clownfish",
    "sea turtle", "dolphin", "coral", "sea anemone", "penguin", "seal",
    "sea lion", "stingray", "manta ray", "whale shark", "whale", "pelican",
    "shrimp", "sea urchin", "squid", "cuttlefish",
]
_MIN_NARRATION_WORDS = 45  # ~20s of speech; below this episodes felt too thin (one was 15s)

# Personality profiles reused from seabini_script.py's CHARACTERS bible, so
# each narrator sounds like themselves rather than a generic reskin of Bini.
CHARACTER_PROFILES = {
    "bini": "Bini, a friendly turquoise seahorse — curious, brave, and playful; notices things and investigates with wonder.",
    "tula": "Tula, a calm green sea turtle — patient, thoughtful, and wise; explains things gently and never rushes.",
    "ollo": "Ollo, a clever purple octopus — inventive, funny, and playful; gets excited about clever details and puzzles.",
    "dodo": "Dodo, an energetic blue dolphin — sporty, confident, and upbeat; reacts with lots of enthusiasm and movement.",
    "pipi": "Pipi, a sweet little orange fish — a bit shy and curious; asks simple questions the way a young child would.",
}


def _build_system(character: str, include_song: bool) -> str:
    profile = CHARACTER_PROFILES[character]
    song_section = ""
    song_schema = ""
    if include_song:
        song_section = f"""

SONG (every episode ends with a {character.title()} sing-and-dance number sung in {character.title()}'s own voice):
- MUST be about the SAME creature as this episode. Never generic or reused across episodes.
- Structure: one verse (4 short lines), then the chorus (4 short lines) sung TWICE. Combined lyrics text, including every line, must be between 300 and 400 characters total. Keep every line under 30 characters.
- Gentle, unhurried, lullaby-adjacent tempo — NOT fast or hyper. Put a blank line between every line of lyrics (a real pause in the sung output, not just formatting).
- Simple words, concrete objects, repetition, positive emotions, actions kids can imitate.
- No complicated metaphors, no dense facts crammed into the lyrics."""
        song_schema = ',\n  "song": {"title": "short song title", "lyrics": "[Verse]\\nline\\n\\nline\\n\\nline\\n\\nline\\n\\n[Chorus]\\nline\\n\\nline\\n\\nline\\n\\nline\\n\\n[Chorus]\\nline\\n\\nline\\n\\nline\\n\\nline"}'

    return f"""You are the writer for "Bini's Real Ocean" — a preschool (ages 2-6) educational
short where {profile} shows kids REAL nature footage of one sea creature and
talks about what it's doing on screen, in their own distinct personality.

Pick ONE sea creature. Write 3-4 short segments, each about a DIFFERENT
everyday BEHAVIOR of that creature (e.g. how it swims/moves, how it looks
close-up, how it eats, how it protects/defends itself, a fun body-part fact).

HARD RULES (preschool safety & footage-search accuracy):
- NEVER a "predator eating it" / "being hunted" / "being attacked" segment —
  this is REAL footage, not cartoon, so anything violent or scary is
  completely off-limits. If defense comes up, frame it gently (e.g. "it
  curls up safe inside its shell", "it can change color to hide") — no
  fighting, biting, or blood.
  All 3-4 behaviors must be visually peaceful/observable in typical nature
  footage: swimming, floating, glowing, eating (plants/plankton/small
  harmless food), moving, hiding, changing color, its body parts, etc.
  - Warm, positive, simple words a 2-6 year old understands. Short sentences.
- Each segment's "search_query" must be a short (2-4 word) phrase that would
  realistically find real stock video footage of exactly that behavior —
  concrete and visual (e.g. "jellyfish swimming underwater", "crab walking
  sideways", "octopus changing color"), not abstract.
- Each segment's "narration" is what the narrator says while that footage
  plays, IN THEIR OWN VOICE/PERSONALITY per the profile above: 1-2 short
  sentences, said as if watching it live and reacting to it. Total across
  all segments should read aloud in about 25-40 seconds combined (roughly
  60-100 words total).
- Facts must be true and simple. Prefer well-known, easily-verified facts.{song_section}

SEARCH SEO (how real people type into TikTok/YouTube search — this drives
whether the algorithm surfaces the video to people actively searching):
- Write "search_hook": the episode's single most interesting fact, phrased
  EXACTLY the way a curious parent or kid would type it into a search bar.
  Prefer a genuine question starting with "why", "how", or "what" (e.g.
  "why do jellyfish glow", "how does an octopus change color", "why do
  starfish have five arms") over a statement. Lowercase, no punctuation,
  6-10 words, no creature-name-only queries (too generic/competitive) —
  it must name the SPECIFIC behavior/fact from this episode.

Return ONLY JSON in this exact shape:
{{
  "creature": "the creature name, lowercase, simple (e.g. \\"jellyfish\\")",
  "title": "short episode title, e.g. \\"Jellyfish: A Closer Look\\"",
  "search_hook": "the search-style question, e.g. \\"why do jellyfish glow in the dark\\"",
  "segments": [
    {{"behavior": "short behavior label, e.g. swimming",
     "search_query": "2-4 word visual search phrase for stock footage",
     "narration": "what the narrator says over this footage"}}
  ]{song_schema}
}}
The "segments" array must have exactly 3 or 4 entries."""


def _problems(ep: dict, include_song: bool) -> list:
    """Why an LLM episode is unusable ([] if it's fine). Checked before any
    rendering so a malformed script fails fast instead of mid-render."""
    probs = []
    for key in ("creature", "title", "search_hook"):
        if not isinstance(ep.get(key), str) or not ep[key].strip():
            probs.append(f"missing {key}")
    segs = ep.get("segments")
    if not isinstance(segs, list) or not 3 <= len(segs) <= 4:
        return probs + ["segments must be a list of 3-4"]
    for i, s in enumerate(segs):
        for key in ("behavior", "search_query", "narration"):
            if not isinstance(s, dict) or not isinstance(s.get(key), str) or not s[key].strip():
                probs.append(f"segment {i} missing {key}")
    words = sum(len(s.get("narration", "").split()) for s in segs if isinstance(s, dict))
    if words < _MIN_NARRATION_WORDS:
        probs.append(f"narration only {words} words (need {_MIN_NARRATION_WORDS}+)")
    if include_song and len(((ep.get("song") or {}).get("lyrics") or "")) < 150:
        probs.append("song lyrics missing or too short")
    return probs


def generate_real_episode(theme: str = "", character: str = "bini", include_song: bool = None) -> dict:
    if include_song is None:
        include_song = (character == "bini")
    creature_hint = ", ".join(CREATURES)
    user = (f"Write today's Bini's Real Ocean episode. Theme/creature seed: {theme}.\n"
            if theme else
            f"Write today's Bini's Real Ocean episode about any one of these (or a similar) sea creature: {creature_hint}.\n")
    body = {
        "model": "gpt-4o-mini",
        "response_format": {"type": "json_object"},
        "temperature": 0.9,
        "messages": [{"role": "system", "content": _build_system(character, include_song)},
                     {"role": "user", "content": user}],
    }
    probs = []
    for attempt in range(3):
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {ss._openai_key()}", "Content-Type": "application/json"},
        )
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=60))
            ep = json.loads(resp["choices"][0]["message"]["content"])
        except Exception as exc:
            probs = [f"request/parse failed: {exc}"]
        else:
            probs = _problems(ep, include_song)
            if not probs:
                return ep
        print(f"[script] attempt {attempt + 1} unusable: {'; '.join(probs)}")
    raise SystemExit(f"Script generation failed after 3 attempts: {'; '.join(probs)}")


if __name__ == "__main__":
    theme = sys.argv[1] if len(sys.argv) > 1 else ""
    character = sys.argv[2] if len(sys.argv) > 2 else "bini"
    ep = generate_real_episode(theme, character)
    print(json.dumps(ep, indent=2, ensure_ascii=False))
