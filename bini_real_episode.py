"""One-command "Bini's Real Ocean" episode factory: generate a behavior-based
script -> fetch real footage per behavior -> render Bini narrating over each
-> stitch title card + segments with background music into one Short.

Run: python bini_real_episode.py ["optional theme"] out.mp4
Mirrors seabini_render.build_episode()'s stitching pattern so both series
produce consistent output (same W/H/FPS, same audio-mixing approach).
"""
import json, random, sys, urllib.request, wave
import numpy as np
from moviepy.editor import ImageSequenceClip

import seabini_render as sr
import bini_real_footage as brf
import bini_real_render as brr
from bini_real_script import CREATURES, generate_real_episode

SERIES_TITLE = "Bini's Real Ocean"
_MANIFEST_URL = "https://raw.githubusercontent.com/oceanfarm1992-design/ai-drama-showcase/master/manifest.json"


def _recent_creature():
    """Best-effort: the most recent Bini's Real Ocean creature, so back-to-
    back runs (2x/day) don't repeat it. Public repo, no auth needed; any
    failure here just means no exclusion, not a hard error."""
    try:
        data = json.loads(urllib.request.urlopen(_MANIFEST_URL, context=sr._SSL, timeout=10).read())
        for ep in data.get("episodes", []):
            if ep.get("series_title") == SERIES_TITLE:
                title = ep.get("title", "").lower()
                for c in CREATURES:
                    if c in title:
                        return c
                break
    except Exception:
        pass
    return None


def _pick_theme(theme):
    """The LLM is unreliable at picking a genuinely random creature when
    just asked to "pick any" — it's primed by its own prompt describing
    Bini as a seahorse and gravitates there. Pick uniformly at random
    ourselves instead, excluding whatever the last episode covered."""
    if theme:
        return theme
    pool = [c for c in CREATURES if c != _recent_creature()]
    return random.choice(pool or CREATURES)


def build_real_episode(theme, out_path, character="bini"):
    theme = _pick_theme(theme)
    episode = generate_real_episode(theme, character)
    creature = episode["creature"]
    title = episode.get("title", f"{character.title()}'s Real Ocean: {creature}")
    print(f"EPISODE: {title} ({creature}) — narrated by {character}")

    segments = [sr._card(f"{character.title()}'s Real Ocean", f"A closer look: {creature}")]
    segment_videos = []
    for i, seg in enumerate(episode["segments"]):
        query = seg.get("search_query", creature)
        narration = seg["narration"]
        print(f"  [{seg.get('behavior', '?')}] fetching '{query}' ...")
        video_path = brf.fetch_footage(query, f"seg{i}", sr.WORK, sr.FF)
        if not video_path:
            print(f"    no footage found, skipping this segment")
            continue
        frames, wav = brr.render_real_scene(narration, video_path, f"seg{i}", character)
        segments.append((frames, wav))
        segment_videos.append(video_path)
        print(f"    rendered {len(frames)} frames")

    if len(segments) <= 1:
        raise SystemExit("No footage could be found for any segment — aborting.")

    all_frames, chunks = [], []
    for frames, wav in segments:
        all_frames.extend(frames)
        n = int(round(len(frames) / sr.FPS * sr.SR))
        chunks.append(np.zeros((n, 2), dtype=np.int16) if wav is None else sr._wav_samples(wav, n))
    narration_audio = np.vstack(chunks).astype(np.float32)

    music = str(sr.ASSET / "music" / "theme.wav")
    import pathlib, subprocess
    if pathlib.Path(music).exists():
        mw = str(sr.WORK / "real_music.wav")
        subprocess.run([sr.FF, "-y", "-i", music, "-ac", "2", "-ar", str(sr.SR), mw], check=True, capture_output=True)
        w = wave.open(mw, "rb")
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)
        w.close()
        if len(a) >= len(narration_audio):
            m = a[:len(narration_audio)]
        else:
            reps = int(np.ceil(len(narration_audio) / len(a)))
            m = np.tile(a, (reps, 1))[:len(narration_audio)]
        narration_audio = np.clip(narration_audio + m.astype(np.float32) * 0.15, -32768, 32767)

    song = episode.get("song")
    if song and song.get("lyrics"):
        print("generating song...")
        song_mp3 = sr.get_song(song["lyrics"], "realsong0")
        song_wav = sr._song_to_wav(song_mp3, "realsong0")
        if segment_videos:
            song_frames, _ = brr.render_real_dance_scene(song_wav, segment_videos[0], "realsong0", character)
        else:
            song_frames, _ = sr._dance_scene(song_wav, "realsong0")
        n = int(round(len(song_frames) / sr.FPS * sr.SR))
        song_audio = sr._wav_samples(song_wav, n).astype(np.float32)
        all_frames = all_frames + song_frames
        narration_audio = np.vstack([narration_audio, song_audio])
        print("added song:", song.get("title"), round(len(song_frames) / sr.FPS, 1), "s")

    final = narration_audio.astype(np.int16)
    wav_path = str(sr.WORK / "real_episode_audio.wav")
    ww = wave.open(wav_path, "wb")
    ww.setnchannels(2); ww.setsampwidth(2); ww.setframerate(sr.SR); ww.writeframes(final.tobytes())
    ww.close()

    silent = str(sr.WORK / "real_episode_silent.mp4")
    ImageSequenceClip(all_frames, fps=sr.FPS).write_videofile(
        silent, fps=sr.FPS, codec="libx264", audio=False, preset="medium", logger=None)
    subprocess.run([sr.FF, "-y", "-i", silent, "-i", wav_path,
                     "-c:v", "copy", "-c:a", "aac", "-shortest", out_path], check=True, capture_output=True)
    dur = round(len(all_frames) / sr.FPS, 1)
    print(f"wrote {out_path} ({dur}s)")

    meta = {"title": title, "creature": creature, "series_title": SERIES_TITLE,
             "character": character, "narrator": character.title(),
             "learning_objective": episode["segments"][0]["narration"] if episode["segments"] else ""}
    with open("episode_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return out_path


if __name__ == "__main__":
    theme = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "episode.mp4"
    character = sys.argv[3] if len(sys.argv) > 3 else "bini"
    build_real_episode(theme, out, character)
