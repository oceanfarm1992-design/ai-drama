"""One-command "Bini's Real Ocean" episode factory: generate a behavior-based
script -> fetch real footage per behavior -> render the narrator over each
-> stitch segments (+ Bini's song) + an end card, with background music.

Run: python bini_real_episode.py ["optional theme"] out.mp4 [character]
Mirrors seabini_render.build_episode()'s stitching pattern so both series
produce consistent output (same W/H/FPS, same audio-mixing approach).
"""
import json, pathlib, random, subprocess, sys, urllib.request, wave
import numpy as np
from moviepy.editor import ImageSequenceClip

import seabini_render as sr
import bini_real_footage as brf
import bini_real_render as brr
import bini_real_script as brs
from bini_real_script import CREATURES, generate_real_episode

SERIES_TITLE = "Bini's Real Ocean"
_MANIFEST_URL = "https://raw.githubusercontent.com/oceanfarm1992-design/ai-drama-showcase/master/manifest.json"
_RECENT_CREATURES = 10   # with 22 creatures and 5 episodes/day, no animal repeats within 2 days
_CLIP_COOLDOWN_EPISODES = 150  # ~30 days: a clip can't reappear until viewers have long forgotten it
_MIN_SEGMENTS = 2
_CREATURE_ATTEMPTS = 3   # switch animal if one hasn't enough fresh, verified footage
_MUSIC_GAIN = 0.15


def _history():
    """Best-effort past Real Ocean episodes (newest first) from the public
    manifest. Public repo, no auth needed; any failure means no exclusions."""
    try:
        data = json.loads(urllib.request.urlopen(_MANIFEST_URL, context=sr._SSL, timeout=10).read())
    except Exception as exc:
        print(f"[history] manifest unavailable ({exc}); no repeat protection this run")
        return []
    return [e for e in data.get("episodes", []) if e.get("series_title") == SERIES_TITLE]


def _creature_of(ep):
    return ep.get("creature") or next((c for c in CREATURES if c in ep.get("title", "").lower()), None)


def _pick_theme(theme, excluded):
    """The LLM is unreliable at picking a genuinely random creature when
    just asked to "pick any" — it's primed by its own prompt describing
    Bini as a seahorse and gravitates there. Pick uniformly at random
    ourselves instead, excluding recent and already-failed creatures."""
    if theme:
        return theme
    pool = [c for c in CREATURES if c not in excluded]
    return random.choice(pool or CREATURES)


def _past_angles(history, creature):
    """Titles + search hooks of earlier episodes about this creature."""
    out = []
    for ep in history:
        if _creature_of(ep) == creature:
            out += [x for x in (ep.get("title"), ep.get("search_hook")) if x]
    return out


def _music_bed(n_samples):
    """Background music (stereo int16) looped/trimmed to n_samples, or None.
    theme.mp3 is the committed copy; *.wav is gitignored, so CI only has the mp3."""
    for name in ("theme.mp3", "theme.wav"):
        src = sr.ASSET / "music" / name
        if src.exists():
            break
    else:
        return None
    mw = str(sr.WORK / "real_music.wav")
    subprocess.run([sr.FF, "-y", "-i", str(src), "-ac", "2", "-ar", str(sr.SR), mw], check=True, capture_output=True)
    w = wave.open(mw, "rb")
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)
    w.close()
    return np.tile(a, (int(np.ceil(n_samples / len(a))), 1))[:n_samples]


def _samples(frames):
    return int(round(len(frames) / sr.FPS * sr.SR))


def _gather_footage(episode, character, used_clips):
    """Fetch + verify a clip per segment. Returns [(segment, video_path, shows)]."""
    creature = episode["creature"]
    found = []
    for i, seg in enumerate(episode["segments"]):
        query = seg.get("search_query", creature)
        print(f"  [{seg.get('behavior', '?')}] fetching '{query}' ...")
        got = brf.fetch_footage(query, f"seg{i}", sr.WORK, sr.FF, used_clips, subject=creature)
        if got:
            found.append((seg, *got))
        else:
            print("    no verified footage, dropping this segment")
    return found


def _plan_episode(theme, character, history, used_clips):
    """Script + verified footage, retrying with a different creature when one
    can't fill an episode with fresh on-topic clips."""
    recent = [c for c in (_creature_of(e) for e in history[:_RECENT_CREATURES]) if c]
    tried = []
    for attempt in range(1 if theme else _CREATURE_ATTEMPTS):
        pick = _pick_theme(theme, recent + tried)
        episode = generate_real_episode(pick, character, avoid=_past_angles(history, pick))
        print(f"EPISODE: {episode.get('title')} ({episode['creature']}) — narrated by {character}")
        attempt_used = set(used_clips)
        clips = _gather_footage(episode, character, attempt_used)
        if len(clips) >= _MIN_SEGMENTS:
            used_clips |= attempt_used
            return episode, clips
        print(f"Only {len(clips)} verified segment(s) for {episode['creature']}; trying another creature")
        tried += [pick, episode["creature"]]
    raise SystemExit("Couldn't find enough verified, unused footage — not publishing an off-topic episode.")


def build_real_episode(theme, out_path, character="bini"):
    history = _history()
    past_clips = {i for ep in history[:_CLIP_COOLDOWN_EPISODES] for i in ep.get("footage_ids", [])}
    used_clips = set(past_clips)
    episode, clips = _plan_episode(theme, character, history, used_clips)
    creature = episode["creature"]
    name = character.title()
    title = episode.get("title", f"{name}'s Real Ocean: {creature}")
    hook = episode.get("search_hook", "").strip()

    # The script was written before the footage existed; make each line talk
    # about what its clip actually shows.
    narrations = brs.match_narration(episode, [(seg, shows) for seg, _, shows in clips], character)

    # Open straight on real footage with the search question on screen:
    # on Shorts/TikTok the first second decides whether a viewer swipes away,
    # so the branded title card moved to the end.
    segments, segment_videos = [], []
    for i, ((seg, video_path, _), text) in enumerate(zip(clips, narrations)):
        hook_text = f"{hook[0].upper()}{hook[1:]}?" if hook and not segments else None
        frames, wav = brr.render_real_scene(text, video_path, f"seg{i}", character, hook_text=hook_text)
        segments.append((frames, wav))
        segment_videos.append(video_path)
        print(f"  rendered segment {i}: {len(frames)} frames")

    all_frames, chunks = [], []
    for frames, wav in segments:
        all_frames.extend(frames)
        chunks.append(sr._wav_samples(wav, _samples(frames)))
    narration_audio = np.vstack(chunks).astype(np.float32)

    end_frames, _ = sr._card(f"{name}'s Real Ocean", "New episode every day!", dur=1.8, character=character)
    end_len = _samples(end_frames)
    bed = _music_bed(len(narration_audio) + end_len)
    end_audio = np.zeros((end_len, 2), dtype=np.float32)
    if bed is not None:
        narration_audio += bed[:len(narration_audio)].astype(np.float32) * _MUSIC_GAIN
        end_audio += bed[len(narration_audio):].astype(np.float32) * _MUSIC_GAIN
    else:
        print("  (no background music found)")

    song = episode.get("song")
    if song and song.get("lyrics"):
        print("generating song...")
        song_mp3 = sr.get_song(song["lyrics"], "realsong0")
        song_wav = sr._song_to_wav(song_mp3, "realsong0")
        song_frames, _ = brr.render_real_dance_scene(song_wav, segment_videos[0], "realsong0", character)
        all_frames = all_frames + song_frames
        narration_audio = np.vstack([narration_audio, sr._wav_samples(song_wav, _samples(song_frames)).astype(np.float32)])
        print("added song:", song.get("title"), round(len(song_frames) / sr.FPS, 1), "s")

    all_frames = all_frames + end_frames
    final = np.clip(np.vstack([narration_audio, end_audio]), -32768, 32767).astype(np.int16)
    wav_path = str(sr.WORK / "real_episode_audio.wav")
    ww = wave.open(wav_path, "wb")
    ww.setnchannels(2); ww.setsampwidth(2); ww.setframerate(sr.SR); ww.writeframes(final.tobytes())
    ww.close()

    silent = str(sr.WORK / "real_episode_silent.mp4")
    ImageSequenceClip(all_frames, fps=sr.FPS).write_videofile(
        silent, fps=sr.FPS, codec="libx264", audio=False, preset="medium", logger=None)
    subprocess.run([sr.FF, "-y", "-i", silent, "-i", wav_path,
                    "-c:v", "copy", "-c:a", "aac", "-shortest", out_path], check=True, capture_output=True)
    print(f"wrote {out_path} ({round(len(all_frames) / sr.FPS, 1)}s)")

    meta = {"title": title, "creature": creature, "series_title": SERIES_TITLE,
            "character": character, "narrator": name, "search_hook": hook,
            "footage_ids": sorted(used_clips - past_clips),
            "learning_objective": narrations[0]}
    pathlib.Path("episode_meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    theme = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "episode.mp4"
    character = sys.argv[3] if len(sys.argv) > 3 else "bini"
    build_real_episode(theme, out, character)
