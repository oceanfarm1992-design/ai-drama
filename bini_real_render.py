"""Renderer for the "Bini's Real Ocean" series — composites Bini's rigged
head (same mouth-viseme rig as the main SEABINI series) over real stock
video footage instead of an AI-generated static background.

Reuses seabini_render's character rig, lip-sync, and voice pipeline as-is
(same Bini voice/appearance across both series) — only the background
source changes from a static cropped image to a real decoded video clip.
"""
import math, wave
import numpy as np
from PIL import Image
from moviepy.editor import VideoFileClip

import seabini_render as sr

W, H, FPS = sr.W, sr.H, sr.FPS


def render_real_scene(text, video_path, tag, character="bini"):
    """Mirrors seabini_render.render_scene()'s structure, but crops each
    frame from a real (already-vertical, pre-normalized) video clip instead
    of panning across a static background image."""
    ch = sr.CHARACTERS[character]
    base = Image.open(str(sr.ASSET / "characters" / ch["base"])).convert("RGBA")
    s = sr.TH / base.height
    base = base.resize((int(base.width * s), sr.TH))
    BW, BH = base.size
    heads = sr._build_heads(base, ch)

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
    seed = sum(ord(c) for c in tag)
    blink_times = sr._blink_times(dur, seed)

    # Real footage isn't directed by us, so we never know where in frame the
    # subject creature actually is (unlike the AI-generated backgrounds,
    # which are prompted to keep it in the upper half). Rather than risk
    # Bini covering it, she sits as a small "narrator bubble" in a bottom
    # corner instead of centered — the real footage stays fully visible.
    CORNER_SCALE = 0.55
    corner_x = int(W * 0.24)
    corner_y = int(H * 0.82)

    frames = []
    for i in range(int(dur * FPS)):
        t = i / FPS
        bg_t = t % clip.duration
        bg_frame = clip.get_frame(bg_t)  # RGB uint8 array, already 720x1280
        win = Image.fromarray(bg_frame).convert("RGBA")

        head = heads[shape_at(t)]
        if ch.get("blink_ready") and sr._is_blinking(t, blink_times): head = sr._blink_frame(head, ch)
        sc = CORNER_SCALE * (1 + 0.015 * math.sin(t * 1.5))
        im2 = head.resize((int(BW * sc), int(BH * sc))).rotate(
            3 * math.sin(t * 1.0), expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        px = corner_x + 12 * math.sin(t * 0.45)
        py = corner_y + 10 * math.sin(t * 1.5)
        win.alpha_composite(im2, (int(px - im2.width / 2), int(py - im2.height / 2)))
        frames.append(np.array(win.convert("RGB")))

    clip.close()
    return frames, baby


def render_real_dance_scene(song_wav, video_path, tag, character="bini"):
    """Mirrors seabini_render._dance_scene()'s bounce/sway motion, but over
    real video like render_real_scene() instead of a static cartoon bg — so
    the closing song stays visually consistent with the narration segments
    instead of switching styles. Same corner-bubble sizing/position for the
    same reason (never know where the real footage's subject creature is)."""
    ch = sr.CHARACTERS[character]
    base = Image.open(str(sr.ASSET / "characters" / ch["base"])).convert("RGBA")
    s = sr.TH / base.height
    base = base.resize((int(base.width * s), sr.TH))
    BW, BH = base.size
    heads = sr._build_heads(base, ch)

    import wave as _wave
    wf = _wave.open(song_wav, "rb")
    dur = wf.getnframes() / wf.getframerate()
    wf.close()

    clip = VideoFileClip(video_path)
    seed = sum(ord(c) for c in tag)
    blink_times = sr._blink_times(dur, seed)
    mouth_cycle = ["closed", "wide", "round", "mid"]

    CORNER_SCALE = 0.55
    corner_x = int(W * 0.24)
    corner_y = int(H * 0.82)

    frames = []
    for i in range(int(dur * FPS)):
        t = i / FPS
        bg_t = t % clip.duration
        bg_frame = clip.get_frame(bg_t)
        win = Image.fromarray(bg_frame).convert("RGBA")

        beat = t * 1.5  # same gentle, unhurried tempo as the cartoon dance scene
        bounce = 10 * abs(math.sin(beat)); sway = 10 * math.sin(beat * 0.6)
        sc = CORNER_SCALE * (1 + 0.05 * abs(math.sin(beat)))
        rot = 8 * math.sin(beat * 0.6)
        head = heads[mouth_cycle[int(t * 2.2) % 4]]
        if ch.get("blink_ready") and sr._is_blinking(t, blink_times): head = sr._blink_frame(head, ch)
        im2 = head.resize((int(BW * sc), int(BH * sc))).rotate(
            rot, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        px, py = corner_x + sway, corner_y - bounce
        win.alpha_composite(im2, (int(px - im2.width / 2), int(py - im2.height / 2)))
        frames.append(np.array(win.convert("RGB")))

    clip.close()
    return frames, song_wav
