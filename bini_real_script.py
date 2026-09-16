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

CREATURES = [
    "jellyfish", "crab", "starfish", "octopus", "seahorse", "clownfish",
    "sea turtle", "dolphin", "coral", "sea anemone",
]

SYSTEM = """You are the writer for "Bini's Real Ocean" — a preschool (ages 2-6) educational
short where Bini (a friendly turquoise seahorse narrator) shows kids REAL nature
footage of one sea creature and talks about what it's doing on screen.

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
- Each segment's "narration" is what Bini says while that footage plays:
  1-2 short sentences, said as if Bini is watching it live and reacting
  with wonder. Total across all segments should read aloud in about 25-40
  seconds combined (roughly 60-100 words total).
- Facts must be true and simple. Prefer well-known, easily-verified facts.

Return ONLY JSON in this exact shape:
{
  "creature": "the creature name, lowercase, simple (e.g. \\"jellyfish\\")",
  "title": "short episode title, e.g. \\"Jellyfish: A Closer Look\\"",
  "segments": [
    {"behavior": "short behavior label, e.g. swimming",
     "search_query": "2-4 word visual search phrase for stock footage",
     "narration": "what Bini says over this footage"}
  ]
}
The "segments" array must have exactly 3 or 4 entries."""


def generate_real_episode(theme: str = "") -> dict:
    creature_hint = ", ".join(CREATURES)
    user = (f"Write today's Bini's Real Ocean episode. Theme/creature seed: {theme}.\n"
            if theme else
            f"Write today's Bini's Real Ocean episode about any one of these (or a similar) sea creature: {creature_hint}.\n")
    body = {
        "model": "gpt-4o-mini",
        "response_format": {"type": "json_object"},
        "temperature": 0.9,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {ss._openai_key()}", "Content-Type": "application/json"},
    )
    resp = json.load(urllib.request.urlopen(req))
    return json.loads(resp["choices"][0]["message"]["content"])


if __name__ == "__main__":
    theme = sys.argv[1] if len(sys.argv) > 1 else ""
    ep = generate_real_episode(theme)
    print(json.dumps(ep, indent=2, ensure_ascii=False))
