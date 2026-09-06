"""One-command SEABINI episode factory: generate a script, then render a full
multi-character Short.

Run:  python seabini_episode.py ["optional theme"] [output.mp4]
Needs the render deps (moviepy, pillow<10, numpy, imageio-ffmpeg) + Rhubarb + ffmpeg,
and keys in .APIs.txt (openai + elevenlabs). Optional background music:
place seabini_assets/music/theme.mp3 (used automatically if present).
"""
import sys, json, pathlib
from seabini_script import generate_episode
from seabini_render import build_episode, ASSET

theme = sys.argv[1] if len(sys.argv) > 1 else ""
out = sys.argv[2] if len(sys.argv) > 2 else "seabini_episode.mp4"

ep = generate_episode(theme)
print("EPISODE:", ep.get("title"), "|", ep.get("learning_objective"))
for sc in ep["scenes"]:
    print(f"  [{sc.get('beat','')}] {sc['speaker']} @ {sc.get('location','')}: {sc['line']}")

music = ASSET / "music" / "theme.mp3"
build_episode(ep, out, music=str(music) if music.exists() else None)
