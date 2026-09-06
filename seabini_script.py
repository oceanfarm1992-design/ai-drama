"""
SEABINI episode-script generator.

The "specialized SEABINI model" = a general LLM (OpenAI gpt-4o-mini) with the
SEABINI brand bible + curated, verified sea facts in the system prompt. No
training, ~$0.001/episode. Outputs a structured JSON episode the rig pipeline
turns into a Short.

Run:  python seabini_script.py ["optional theme"]
Reads the OpenAI key from .APIs.txt (openai-secret=...) or OPENAI_API_KEY.
"""

import os, sys, json, urllib.request, pathlib

# ---- Curated, verified simple sea facts the writer may use (kept accurate & preschool-safe) ----
SEA_FACTS = [
    "A sea star (starfish) usually has five arms, and it can slowly grow a new arm if it loses one.",
    "An octopus has eight arms and three hearts, and it is very clever at solving puzzles.",
    "A dolphin is not a fish; it is a mammal that comes up to breathe air through a blowhole on its head.",
    "A sea turtle can hold its breath for a long time underwater before coming up for air.",
    "A clownfish lives safely among the wiggly arms of a sea anemone.",
    "A seahorse daddy carries the babies until they are ready to swim.",
    "A crab walks sideways and wears a hard shell like a little suit of armor.",
    "A jellyfish has no brain and no bones, and its body is made mostly of water.",
    "Coral looks like a colorful rock, but it is actually made of tiny living animals.",
    "Fish breathe underwater using special parts called gills.",
    "Bubbles float up to the top because air is lighter than water.",
    "A humpback whale sings long songs that travel far across the ocean.",
]

CHARACTERS = """
- Bini (turquoise seahorse, THE EXPLORER): curious, brave, kind, playful; notices something and investigates. Catchphrase: "Let's dive in!"
- Tula (green turtle, THE WISE ONE): calm, patient, thoughtful; slows the group down and encourages good choices.
- Ollo (purple octopus, THE INVENTOR): clever, creative, funny; solves problems with his eight arms and clever ideas.
- Dodo (blue dolphin, THE ENERGETIC ONE): sporty, confident, playful; brings movement, excitement, fun challenges.
- Pipi (orange fish, THE LITTLE ONE): sweet, curious, a little shy; asks the simple questions younger kids also wonder.
""".strip()

LOCATIONS = "Rainbow Reef (home base), Mangrove Bay, Seagrass Garden, Shell Beach, Blue Lagoon, The Old Wreck, Moonlight Reef (calm/bedtime)."

SYSTEM = f"""You are the head writer for SEABINI, an original preschool (ages 2-6) ocean cartoon channel. Tagline: "Adventures Beneath the Blue!" Pillars: Songs, Stories, Discovery, Kindness. Entertainment comes first; any learning is simple, accurate, and woven naturally into the adventure.

CAST (keep each personality distinct; do NOT redesign them):
{CHARACTERS}

WORLD (reusable locations): {LOCATIONS}

SIGNATURE PHRASES (use naturally, not all at once): "What is that?", "Let's find out!", "Let's dive in!", and ALWAYS end the episode with "See you beneath the blue!".

EPISODE FORMULA (follow in order): HOOK (something interesting happens) -> QUESTION (a character gets curious and asks why) -> ADVENTURE (friends explore) -> DISCOVERY (they learn one simple thing) -> FUN ENDING -> SIGNATURE signoff.

HARD RULES (preschool safety & quality):
- Simple words and short sentences a 2-6 year old understands.
- Warm, positive, gentle tone. NO scary predators, danger, violence, injury, or sad/adult themes.
- Exactly ONE simple takeaway per episode. Do not overload with facts.
- Any sea fact MUST be accurate. Prefer facts from the provided list; if you use another, keep it true and simple. When unsure, keep it playful and skip the fact.
- Original content only. Never imitate CoComelon, Baby Shark, or any existing show/song/character.
- Each narration line is spoken by ONE named character. Keep lines short (good for a young narrator).

Return ONLY JSON in this exact shape:
{{
  "series": "SEABINI",
  "format": "Seabini Short",
  "title": "short episode title",
  "learning_objective": "the one simple takeaway",
  "main_location": "one location from the world list",
  "scenes": [
    {{"beat": "HOOK|QUESTION|ADVENTURE|DISCOVERY|ENDING",
      "location": "a location from the world list",
      "speaker": "Bini|Tula|Ollo|Dodo|Pipi",
      "line": "one short spoken line",
      "visual": "short description of what we see (character + action + setting)"}}
  ]
}}
Use 5 to 7 scenes. The final scene's line MUST include "See you beneath the blue!"."""


def _openai_key():
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k
    txt = pathlib.Path(__file__).with_name(".APIs.txt")
    if txt.exists():
        for line in txt.read_text().splitlines():
            if line.lower().startswith("openai"):
                return line.split("=", 1)[1].strip()
    raise SystemExit("No OpenAI key (set OPENAI_API_KEY or .APIs.txt openai-secret=...)")


def generate_episode(theme: str = "") -> dict:
    facts = "\n".join(f"- {f}" for f in SEA_FACTS)
    user = (f"Write today's Seabini Short. Theme/seed: {theme}.\n" if theme else "Write today's Seabini Short about any one sea creature or simple ocean idea.\n")
    user += f"You may use any of these verified facts (pick at most ONE as the takeaway):\n{facts}"
    body = {
        "model": "gpt-4o-mini",
        "response_format": {"type": "json_object"},
        "temperature": 0.9,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_openai_key()}", "Content-Type": "application/json"},
    )
    resp = json.load(urllib.request.urlopen(req))
    return json.loads(resp["choices"][0]["message"]["content"])


if __name__ == "__main__":
    theme = sys.argv[1] if len(sys.argv) > 1 else ""
    ep = generate_episode(theme)
    print(json.dumps(ep, indent=2, ensure_ascii=False))
