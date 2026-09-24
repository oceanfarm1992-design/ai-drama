"""Renderer for the "Bini's Real Ocean" series — composites a character's
rigged head (same mouth-viseme rig as the main SEABINI series) over real
stock video footage instead of an AI-generated static background, with an
optional search-question hook and burned-in subtitles.

Reuses seabini_render's character rig, lip-sync, and voice pipeline as-is —
only the background source changes from a static image to a real video clip.
"""
import math, wave
import numpy as np
from PIL import Image, ImageDraw
from moviepy.editor import VideoFileClip

import seabini_render as sr

W, H, FPS = sr.W, sr.H, sr.FPS

# Real footage isn't directed by us, so we never know where in frame the
# subject creature is. Rather than risk the character covering it, they sit
# as a small "narrator bubble" at the left. It's raised off the very bottom
# because TikTok / Shorts / Reels all overlay their own username + caption
# text on the bottom-left ~20% of the video.
CORNER_SCALE = 0.55
CORNER_X = int(W * 0.24)
CORNER_Y = int(H * 0.74)
SUB_Y = int(H * 0.57)      # subtitles sit just above the narrator bubble
HOOK_Y = int(H * 0.17)     # opening question, below the platforms' top tabs
TEXT_MAX_W = 560           # keeps text clear of the right-side action buttons
HOOK_SECONDS = 3.0


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


def _draw_block(win, text, center_y, size, boxed):
    """Centered multi-line text with a thick outline (readable on any footage);
    `boxed` adds a translucent panel behind it for the big opening hook."""
    d = ImageDraw.Draw(win)
    font = sr._font(size)
    lines = _wrap(d, text, font, TEXT_MAX_W)
    line_h = int(size * 1.2)
    top = center_y - line_h * len(lines) // 2
    if boxed:
        panel = Image.new("RGBA", win.size, (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle(
            (W / 2 - TEXT_MAX_W / 2 - 24, top - 18, W / 2 + TEXT_MAX_W / 2 + 24, top + line_h * len(lines) + 12),
            radius=28, fill=(10, 40, 90, 170))
        win.alpha_composite(panel)
        d = ImageDraw.Draw(win)
    for i, line in enumerate(lines):
        d.text((W / 2, top + i * line_h + line_h // 2), line, anchor="mm", font=font,
               fill=(255, 255, 255), stroke_width=max(3, size // 12), stroke_fill=(0, 0, 0))


def _subtitle_chunks(text, dur, max_words=5):
    """Split narration into short caption chunks, timed by character count —
    close enough to real speech pacing without a word-level aligner."""
    words = text.split()
    chunks = [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]
    total = sum(len(c) for c in chunks) or 1
    out, t = [], 0.0
    for c in chunks:
        span = dur * len(c) / total
        out.append((t, t + span, c))
        t += span
    return out


def _rig(character):
    ch = sr.CHARACTERS[character]
    base = Image.open(str(sr.ASSET / "characters" / ch["base"])).convert("RGBA")
    s = sr.TH / base.height
    base = base.resize((int(base.width * s), sr.TH))
    return ch, sr._build_heads(base, ch), base.size


def render_real_scene(text, video_path, tag, character="bini", hook_text=None, subtitles=True):
    """Mirrors seabini_render.render_scene()'s structure, but takes each frame
    from a real (already-vertical, pre-normalized) video clip instead of
    panning across a static background image."""
    ch, heads, (BW, BH) = _rig(character)

    mp3 = sr.get_voiceover(text, tag, ch)
    if sr.VOICE_ENGINE == "kokoro":
        clean, baby = sr._prep_audio(mp3, tag, ch["kokoro_pitch"], ch["kokoro_post_speed"])
    else:
        clean, baby = sr._prep_audio(mp3, tag, ch["pitch"], ch["speed"])
    wf = wave.open(baby, "rb")
    dur = wf.getnframes() / wf.getframerate()
    wf.close()
    cues = sr._cues(clean, dur)
    starts = np.array([c["start"] for c in cues])
    shape_at = lambda t: sr.VMAP.get(
        cues[max(0, min(np.searchsorted(starts, t + 0.05, side="right") - 1, len(cues) - 1))]["value"],
        "closed")

    clip = VideoFileClip(video_path)
    blink_times = sr._blink_times(dur, sum(ord(c) for c in tag))
    gaze = sr._gaze_schedule(dur, sum(ord(c) for c in tag))
    subs = _subtitle_chunks(text, dur) if subtitles else []

    frames = []
    for i in range(int(dur * FPS)):
        t = i / FPS
        win = Image.fromarray(clip.get_frame(t % clip.duration)).convert("RGBA")

        head = heads[shape_at(t)]
        if ch.get("blink_ready") and sr._is_blinking(t, blink_times): head = sr._blink_frame(head, ch)
        elif ch.get("look_ready"): head = sr._look_frame(head, ch, sr._gaze_at(t, gaze))
        sc = CORNER_SCALE * (1 + 0.015 * math.sin(t * 1.5))
        im2 = head.resize((int(BW * sc), int(BH * sc))).rotate(
            3 * math.sin(t * 1.0), expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        px = CORNER_X + 12 * math.sin(t * 0.45)
        py = CORNER_Y + 10 * math.sin(t * 1.5)
        win.alpha_composite(im2, (int(px - im2.width / 2), int(py - im2.height / 2)))

        if hook_text and t < HOOK_SECONDS:
            _draw_block(win, hook_text, HOOK_Y, 54, boxed=True)
        for start, end, chunk in subs:
            if start <= t < end:
                _draw_block(win, chunk, SUB_Y, 46, boxed=False)
                break
        frames.append(np.array(win.convert("RGB")))

    clip.close()
    return frames, baby


def render_real_dance_scene(song_wav, video_path, tag, character="bini"):
    """Mirrors seabini_render._dance_scene()'s bounce/sway motion, but over
    real video like render_real_scene() instead of a static cartoon bg — so
    the closing song stays visually consistent with the narration segments."""
    ch, heads, (BW, BH) = _rig(character)
    wf = wave.open(song_wav, "rb")
    dur = wf.getnframes() / wf.getframerate()
    wf.close()

    clip = VideoFileClip(video_path)
    blink_times = sr._blink_times(dur, sum(ord(c) for c in tag))
    gaze = sr._gaze_schedule(dur, sum(ord(c) for c in tag))
    mouth_cycle = ["closed", "wide", "round", "mid"]

    frames = []
    for i in range(int(dur * FPS)):
        t = i / FPS
        win = Image.fromarray(clip.get_frame(t % clip.duration)).convert("RGBA")

        beat = t * 1.5  # same gentle, unhurried tempo as the cartoon dance scene
        bounce = 10 * abs(math.sin(beat)); sway = 10 * math.sin(beat * 0.6)
        sc = CORNER_SCALE * (1 + 0.05 * abs(math.sin(beat)))
        rot = 8 * math.sin(beat * 0.6)
        head = heads[mouth_cycle[int(t * 2.2) % 4]]
        if ch.get("blink_ready") and sr._is_blinking(t, blink_times): head = sr._blink_frame(head, ch)
        elif ch.get("look_ready"): head = sr._look_frame(head, ch, sr._gaze_at(t, gaze))
        im2 = head.resize((int(BW * sc), int(BH * sc))).rotate(
            rot, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        win.alpha_composite(im2, (int(CORNER_X + sway - im2.width / 2), int(CORNER_Y - bounce - im2.height / 2)))
        frames.append(np.array(win.convert("RGB")))

    clip.close()
    return frames, song_wav
