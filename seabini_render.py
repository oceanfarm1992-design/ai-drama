"""SEABINI episode renderer (2D cutout-puppet rig, pure Python/CPU).

Given an episode JSON from seabini_script.py, renders each scene — the speaker's
rigged head (fixed base + drawn mouth visemes in the character's real colours,
lip-synced by Rhubarb) over the scene's location background — then stitches a
title card, all scenes, and an outro into one vertical Short with synced audio
and optional background music.

Runtime deps: moviepy, pillow<10, numpy, imageio-ffmpeg (Python) + the Rhubarb
CLI + ffmpeg. Point SEABINI_RHUBARB at rhubarb.exe (or set it below).
"""
import os, json, math, wave, random, subprocess, pathlib, urllib.request, tempfile, time, ssl
try:
    import certifi as _certifi; _SSL = ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()
import numpy as np
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from moviepy.editor import ImageSequenceClip

HERE = pathlib.Path(__file__).resolve().parent
ASSET = HERE / "seabini_assets"
WORK = pathlib.Path(os.environ.get("SEABINI_WORK", tempfile.gettempdir())) / "seabini_build"
WORK.mkdir(parents=True, exist_ok=True)
RHUBARB = os.environ.get("SEABINI_RHUBARB", r"C:/Users/Official/AppData/Local/Temp/claude/C--Calude-Apps-AI-Drama/0adc92ae-7e8c-4231-82bc-a08ec65a170e/scratchpad/rhubarb/Rhubarb-Lip-Sync-1.14.0-Windows/rhubarb.exe")
FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H, FPS, SR, TH = 720, 1280, 24, 44100, int(1280 * 0.42)
VMAP = {"A": "closed", "X": "closed", "B": "mid", "G": "mid", "H": "mid", "C": "wide", "D": "wide", "E": "round", "F": "round"}

# Finalized per-character rig params. pitch/speed drive the Piper/ElevenLabs
# pitch-shift hack (one shared voice, reshaped per character); kokoro_voice
# picks a genuinely distinct natural voice, then kokoro_pitch/kokoro_post_speed
# push it toward a baby/funny character read (user-approved anchor: bini at
# pitch=1.35, post_speed=0.85 — others scaled proportionally from the old table).
CHARACTERS = {
    "bini": dict(base="bini_base.png", cx=180, cy=284, lip=(222, 106, 114), dark=(58, 16, 24), tongue=(239, 130, 140), ms=1.0, erase_mult=1.0, face_pt=(180, 262), eyes=((125, 232, 28, 27), (247, 230, 26, 29)), blink_ready=True, look_ready=True, pitch=1.28, speed=0.86, kokoro_voice="af_heart", kokoro_speed=1.0, kokoro_pitch=1.35, kokoro_post_speed=0.85),
    "tula": dict(base="tula_base.png", cx=216, cy=303, lip=(120, 140, 70), dark=(58, 18, 20), tongue=(185, 70, 68), ms=1.2, erase_mult=1.15, face_pt=(216, 282), eyes=((137, 202, 43, 45), (296, 203, 39, 44)), blink_ready=True, look_ready=True, pitch=1.05, speed=0.82, kokoro_voice="af_bella", kokoro_speed=1.0, kokoro_pitch=1.11, kokoro_post_speed=0.81),
    "ollo": dict(base="ollo_base.png", cx=275, cy=262, lip=(133, 81, 189), dark=(50, 12, 22), tongue=(150, 55, 60), ms=1.15, erase_mult=1.15, face_pt=(275, 240), eyes=((197, 168, 40, 45), (348, 165, 34, 46)), blink_ready=True, look_ready=True, pitch=1.15, speed=0.95, kokoro_voice="bf_emma", kokoro_speed=1.0, kokoro_pitch=1.21, kokoro_post_speed=0.94),
    "dodo": dict(base="dodo_base.png", cx=188, cy=235, lip=(110, 170, 200), dark=(55, 24, 28), tongue=(160, 65, 68), ms=1.4, erase_mult=1.7, face_pt=(188, 180), eyes=((100, 138, 30, 40), (268, 139, 31, 41)), blink_ready=True, look_ready=True, pitch=1.20, speed=1.04, kokoro_voice="am_fenrir", kokoro_speed=1.0, kokoro_pitch=1.27, kokoro_post_speed=1.03),
    "pipi": dict(base="pipi_base.png", cx=165, cy=266, lip=(234, 159, 66), dark=(60, 26, 32), tongue=(170, 68, 72), ms=0.9, erase_mult=1.15, face_pt=(165, 240), eyes=((96, 197, 39, 41), (246, 197, 41, 41)), blink_ready=True, look_ready=True, pitch=1.35, speed=0.92, kokoro_voice="af_nicole", kokoro_speed=1.0, kokoro_pitch=1.42, kokoro_post_speed=0.91),
}
VOICE_ID = "MF3mGyEYCl7XYWbV9V6O"  # ElevenLabs "Elli"; pitch/speed per character above
LOC_BG = {"rainbow reef": "reef.png", "seagrass garden": "seagrass.png", "shell beach": "starfish.png",
          "mangrove bay": "seagrass.png", "blue lagoon": "reef.png", "the old wreck": "reef.png", "moonlight reef": "reef.png"}

def bg_for(loc):
    return str(ASSET / "backgrounds" / LOC_BG.get((loc or "").strip().lower(), "reef.png"))

VOICE_ENGINE = os.environ.get("SEABINI_VOICE_ENGINE", "kokoro")  # "kokoro" (free, local, natural) / "piper" (free, local, robotic) / "elevenlabs" (paid, cloud)
_PIPER_VOICE = None  # lazy-loaded singleton; loading the model is the slow part
_KOKORO = None

def _eleven_key():
    k = os.environ.get("ELEVENLABS_API_KEY")
    if k: return k
    for line in (HERE / ".APIs.txt").read_text().splitlines():
        if line.lower().startswith("elevenlabs"): return line.split("=", 1)[1].strip()
    raise SystemExit("No ELEVENLABS_API_KEY")

def _get_voiceover_elevenlabs(text, tag):
    mp3 = str(WORK / f"{tag}.mp3")
    body = {"text": text.replace("\u2019", "'").replace("\u2018", "'"), "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.8, "style": 0.4}}
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
                                 data=json.dumps(body).encode(),
                                 headers={"xi-api-key": _eleven_key(), "Content-Type": "application/json", "Accept": "audio/mpeg"})
    open(mp3, "wb").write(urllib.request.urlopen(req, context=_SSL).read())
    return mp3

def _get_voiceover_piper(text, tag):
    global _PIPER_VOICE
    from piper import PiperVoice  # optional dep; only needed for the free local engine
    if _PIPER_VOICE is None:
        onnx = ASSET / "voice" / "en_US-amy-medium.onnx"
        _PIPER_VOICE = PiperVoice.load(str(onnx), str(onnx) + ".json")
    wav_path = str(WORK / f"{tag}_piper.wav")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    with wave.open(wav_path, "wb") as wf:
        _PIPER_VOICE.synthesize_wav(text, wf)
    mp3 = str(WORK / f"{tag}.mp3")
    subprocess.run([FF, "-y", "-i", wav_path, mp3], check=True, capture_output=True)
    return mp3

def _get_voiceover_kokoro(text, tag, voice, speed):
    global _KOKORO
    if _KOKORO is None:
        from kokoro_onnx import Kokoro  # optional dep; only needed for this engine
        model = os.environ.get("SEABINI_KOKORO_MODEL", str(ASSET / "voice" / "kokoro-v1.0.int8.onnx"))
        voices = os.environ.get("SEABINI_KOKORO_VOICES", str(ASSET / "voice" / "voices-v1.0.bin"))
        _KOKORO = Kokoro(model, voices)
    text = text.replace("’", "'").replace("‘", "'")
    samples, sr = _KOKORO.create(text, voice=voice, speed=speed, lang="en-us")
    wav_path = str(WORK / f"{tag}_kokoro.wav")
    pcm = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    mp3 = str(WORK / f"{tag}.mp3")
    subprocess.run([FF, "-y", "-i", wav_path, mp3], check=True, capture_output=True)
    return mp3

def _replicate_key():
    k = os.environ.get("REPLICATE_API_TOKEN")
    if k: return k
    apis = HERE / ".APIs.txt"
    if apis.exists():
        for line in apis.read_text().splitlines():
            if line.lower().startswith("replicate"): return line.split("=", 1)[1].strip()
    raise SystemExit("No REPLICATE_API_TOKEN (set the env var or add replicate-secret=... to .APIs.txt)")

def _fit_lyrics(lyrics, limit=3500):
    """minimax/music-2.5 hard-caps lyrics at 3500 chars and errors past that;
    the writer LLM isn't reliable at precise character counting, so truncate at
    a line boundary as a safety net rather than trust the prompt alone."""
    if len(lyrics) <= limit:
        return lyrics
    lines, out, total = lyrics.split("\n"), [], 0
    for line in lines:
        if total + len(line) + 1 > limit:
            break
        out.append(line); total += len(line) + 1
    return "\n".join(out).strip()

_SONG_VOICE_PROMPT = (
    "Cheerful children's sing-along, preschool kids show, warm sweet young "
    "female voice, gentle and bright, ukulele and soft percussion, playful "
    "and bouncy, major key, clear diction, upbeat but tender"
)

def get_song(lyrics, tag):
    """Sung closing number via Replicate's minimax/music-2.5. Chosen over the
    older music-01 (which supported a cloned voice_file reference) for its
    markedly better vocal quality and emotional delivery; music-2.5 dropped
    voice cloning in favor of describing the voice through _SONG_VOICE_PROMPT
    instead, which is used on every episode to keep the voice consistent.
    ~$0.05/song. See seabini-pipeline-gotchas memory for the A/B comparison
    that led to this choice."""
    lyrics = _fit_lyrics(lyrics)
    body = {"input": {"lyrics": lyrics, "prompt": _SONG_VOICE_PROMPT}}
    headers = {"Authorization": f"Bearer {_replicate_key()}", "Content-Type": "application/json", "Prefer": "wait"}
    req = urllib.request.Request("https://api.replicate.com/v1/models/minimax/music-2.5/predictions",
                                  data=json.dumps(body).encode(), headers=headers)
    resp = json.loads(urllib.request.urlopen(req, context=_SSL, timeout=180).read())
    poll_url = f"https://api.replicate.com/v1/predictions/{resp['id']}"
    while resp["status"] not in ("succeeded", "failed", "canceled"):
        time.sleep(3)
        r = urllib.request.Request(poll_url, headers={"Authorization": f"Bearer {_replicate_key()}"})
        resp = json.loads(urllib.request.urlopen(r, context=_SSL).read())
    if resp["status"] != "succeeded":
        raise SystemExit(f"Song generation failed: {resp.get('error')}")
    out_url = resp["output"]
    if isinstance(out_url, list): out_url = out_url[0]
    mp3 = str(WORK / f"{tag}.mp3")
    data = urllib.request.urlopen(urllib.request.Request(out_url), context=_SSL).read()
    pathlib.Path(mp3).write_bytes(data)
    return mp3

def get_voiceover(text, tag, ch):
    """Free local Kokoro (natural) by default; SEABINI_VOICE_ENGINE=piper for the
    faster-but-robotic local engine, or =elevenlabs for the paid cloud voice."""
    if VOICE_ENGINE == "elevenlabs":
        return _get_voiceover_elevenlabs(text, tag)
    if VOICE_ENGINE == "kokoro":
        return _get_voiceover_kokoro(text, tag, ch["kokoro_voice"], ch["kokoro_speed"])
    return _get_voiceover_piper(text, tag)

def _prep_audio(mp3, tag, pitch, speed):
    clean, baby = str(WORK / f"{tag}_clean.wav"), str(WORK / f"{tag}_baby.wav")
    subprocess.run([FF, "-y", "-i", mp3, "-ac", "1", "-ar", "44100", clean], check=True, capture_output=True)
    # asetrate relabels the sample rate to fake a pitch shift, so the audio must
    # already be true 44100Hz going in (an explicit aresample first) — Piper's
    # amy-medium voice synthesizes at 22050Hz, and skipping this made asetrate's
    # "44100*pitch" label roughly double the real shift, halving duration.
    subprocess.run([FF, "-y", "-i", mp3, "-ac", "2", "-ar", "44100", "-af",
                    f"aresample=44100,asetrate=44100*{pitch},aresample=44100,atempo={(1/pitch)*speed:.4f}", baby], check=True, capture_output=True)
    return clean, baby

def _cues(clean_wav, dur):
    subprocess.run([RHUBARB, "-r", "phonetic", "-f", "json", "-o", clean_wav + ".json", clean_wav], check=True, capture_output=True)
    d = json.load(open(clean_wav + ".json")); scale = dur / d["metadata"]["duration"]
    cues, out = [{"start": c["start"]*scale, "end": c["end"]*scale, "value": c["value"]} for c in d["mouthCues"]], []
    for c in cues:
        if out and c["end"]-c["start"] < 0.11: out[-1]["end"] = c["end"]
        else: out.append(dict(c))
    return out

def _build_heads(base, ch):
    cx, cy, ms = ch["cx"], ch["cy"], ch["ms"]
    lip, dark, tongue = ch["lip"], ch["dark"], ch["tongue"]
    em = ms * ch.get("erase_mult", 1.0)
    ex0, ey0, ex1, ey1 = cx-int(38*em), cy-int(28*em), cx+int(38*em), cy+int(32*em)
    top_c = base.getpixel((cx, max(0, ey0-4)))[:3]; bot_c = base.getpixel((cx, min(base.height-1, ey1+4)))[:3]
    def erase(h):
        patch = Image.new("RGB", (ex1-ex0, ey1-ey0))
        for row in range(patch.height):
            f = row/max(1, patch.height-1); col = tuple(int(top_c[i]+(bot_c[i]-top_c[i])*f) for i in range(3))
            ImageDraw.Draw(patch).line((0, row, patch.width, row), fill=col)
        m = Image.new("L", patch.size, 0); ImageDraw.Draw(m).ellipse((0, 0, patch.width, patch.height), fill=255)
        l = Image.new("RGBA", h.size, (0, 0, 0, 0)); l.paste(patch, (ex0, ey0), m.filter(ImageFilter.GaussianBlur(3)))
        h.alpha_composite(l)
    def make(shape):
        h = base.copy(); erase(h); d = ImageDraw.Draw(h)
        E = lambda a, b, c, e, f: d.ellipse((cx+int(a*ms), cy+int(b*ms), cx+int(c*ms), cy+int(e*ms)), fill=f)
        C = lambda a, b, c, e, f: d.chord((cx+int(a*ms), cy+int(b*ms), cx+int(c*ms), cy+int(e*ms)), 0, 180, fill=f)
        if shape == "closed":
            d.arc((cx-int(20*ms), cy-int(14*ms), cx+int(20*ms), cy+int(10*ms)), 20, 160, fill=dark, width=max(2, int(3*ms)))
        elif shape == "mid":
            E(-20, -9, 20, 13, dark); C(-18, 1, 18, 15, tongue)
        elif shape == "wide":
            E(-33, -21, 33, 26, lip); E(-27, -15, 27, 17, dark); C(-23, 4, 23, 32, tongue)
        elif shape == "round":
            E(-22, -20, 22, 24, lip); E(-16, -14, 16, 17, dark)
        return h
    return {s: make(s) for s in ("closed", "mid", "wide", "round")}

_EYELID_COLOR = (40, 40, 46)  # neutral near-black; each character's own
                                # "dark" is tuned for mouth interior (often a
                                # warm maroon), which reads wrong on eyelids

_BLINK_PAD = 15     # px beyond the eye white, so the dark outline is covered too
_BLINK_FEATHER = 4

def _skin_around(h, cx, cy, rx, ry):
    """Median colour of a ring of pixels just outside the eye (upper half
    skipped, since brows/eyelid shadow live there), i.e. the face's own skin."""
    pts = []
    for k in range(24):
        a = math.pi * k / 23  # lower half-circle only
        for scale in (1.12, 1.25):
            x = int(cx + rx * scale * math.cos(a)); y = int(cy + ry * scale * math.sin(a))
            if 0 <= x < h.width and 0 <= y < h.height and h.getpixel((x, y))[3] > 200:
                pts.append(h.getpixel((x, y))[:3])
    if not pts:
        return h.getpixel((cx, cy))[:3]
    return tuple(sorted(p[i] for p in pts)[len(pts) // 2] for i in range(3))

def _blink_frame(head, ch):
    """Returns a copy of an already mouth-composited head with both eyes
    replaced by a closed-eyelid curve. Each eye's calibrated ellipse
    (`eyes`: (cx, cy, rx, ry) in TH-scaled space) is painted over with the
    surrounding skin tone (feathered so it blends), then a lid line is drawn."""
    h = head.copy()
    m_pad = _BLINK_FEATHER * 3
    for (cx, cy, rx, ry) in ch["eyes"]:
        skin = _skin_around(head, cx, cy, rx + _BLINK_PAD, ry + _BLINK_PAD)
        rx2, ry2 = rx + _BLINK_PAD, ry + _BLINK_PAD
        size = (2 * (rx2 + m_pad), 2 * (ry2 + m_pad))
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).ellipse((m_pad, m_pad, size[0] - m_pad, size[1] - m_pad), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(_BLINK_FEATHER))
        patch = Image.new("RGBA", size, skin + (255,))
        patch.putalpha(mask)  # alpha carries the feather; pasting through a mask would darken the edge
        h.alpha_composite(patch, (cx - rx2 - m_pad, cy - ry2 - m_pad))
        aw = int(rx * 0.8)
        ImageDraw.Draw(h).arc((cx - aw, cy - int(ry * 0.3), cx + aw, cy + int(ry * 0.3)), 15, 165,
                              fill=_EYELID_COLOR, width=max(3, rx // 10))
    return h

# --- Gaze: the iris/pupil drifts around inside each eye ---------------------
_LOOK_X, _LOOK_Y = 0.16, 0.08      # max iris travel, as a fraction of eye radius
_LOOK_INNER = 0.9                  # repaint only inside this much of the eye, keeping its outline
_SACCADE = 0.12                    # seconds to move between gaze targets (eyes dart, not glide)
_GAZE_TARGETS = [(0, 0), (0, 0), (-1, 0), (1, 0), (-0.8, -0.8), (0.8, -0.8), (-0.6, 0.6), (0.6, 0.6)]
_EYE_RIG = {}

def _is_sclera(px):
    r, g, b = px[:3]
    return min(r, g, b) > 170 and max(r, g, b) - min(r, g, b) < 60

def _eye_rig(head, ch):
    """Per character (cached): for each eye, a 'blank' plate with the iris
    painted out in sclera white, plus the iris itself as a round RGBA layer
    (highlight included) that _look_frame slides around. The iris disc is
    the centroid/area of the non-white pixels inside the eye. The eye
    region is untouched by the mouth rig, so any mouth variant works."""
    key = ch["base"]
    if key in _EYE_RIG:
        return _EYE_RIG[key]
    rig = []
    for (cx, cy, rx, ry) in ch["eyes"]:
        box = (cx - rx, cy - ry, cx + rx, cy + ry)
        crop = head.crop(box)
        inner = Image.new("L", crop.size, 0)
        irx, iry = rx * _LOOK_INNER, ry * _LOOK_INNER
        ImageDraw.Draw(inner).ellipse((rx - irx, ry - iry, rx + irx, ry + iry), fill=255)
        px, ipx = crop.load(), inner.load()
        whites, dark = [], []
        for y in range(crop.height):
            for x in range(crop.width):
                if not ipx[x, y]:
                    continue
                if _is_sclera(px[x, y]):
                    whites.append(px[x, y][:3])
                else:
                    dark.append((x, y))
        if not dark or not whites:
            rig.append(None)
            continue
        icx = sum(p[0] for p in dark) / len(dark)
        icy = sum(p[1] for p in dark) / len(dark)
        ir = (len(dark) / math.pi) ** 0.5 * 1.08  # a touch wider: the highlight isn't counted as iris
        sclera = tuple(sorted(c[i] for c in whites)[len(whites) // 2] for i in range(3))
        disc = Image.new("L", crop.size, 0)
        ImageDraw.Draw(disc).ellipse((icx - ir, icy - ir, icx + ir, icy + ir), fill=255)
        disc = disc.filter(ImageFilter.GaussianBlur(1))
        iris = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        iris.paste(crop, (0, 0), disc)
        plate = crop.copy()
        plate.paste(Image.new("RGBA", crop.size, sclera + (255,)), (0, 0), disc)
        rig.append(dict(box=box, plate=plate, iris=iris, inner=inner.filter(ImageFilter.GaussianBlur(1)),
                        span=(rx * _LOOK_X, ry * _LOOK_Y)))
    _EYE_RIG[key] = rig
    return rig

def _gaze_schedule(dur, seed):
    """Deterministic list of (start_time, (gx, gy)) gaze targets in [-1, 1]:
    the eyes hold each spot for 1.2-3s, returning to centre most often."""
    rnd = random.Random(seed + 2)
    sched, t, last = [(0.0, (0, 0))], rnd.uniform(1.0, 2.2), (0, 0)
    while t < dur:
        target = rnd.choice([g for g in _GAZE_TARGETS if g != last])
        sched.append((t, target))
        last = target
        t += rnd.uniform(1.2, 3.0)
    return sched

def _gaze_at(t, sched):
    idx = max(i for i, (st, _) in enumerate(sched) if st <= t) if sched else 0
    st, (gx, gy) = sched[idx]
    if idx == 0 or t - st >= _SACCADE:
        return gx, gy
    px, py = sched[idx - 1][1]
    f = (t - st) / _SACCADE
    f = f * f * (3 - 2 * f)  # smoothstep
    return px + (gx - px) * f, py + (gy - py) * f

def _look_frame(head, ch, gaze):
    """Copy of `head` with each iris shifted toward `gaze` (gx, gy in [-1, 1]),
    clipped to the eye so it never spills onto the face."""
    gx, gy = gaze
    if gx == 0 and gy == 0:
        return head
    h = head.copy()
    for eye in _eye_rig(head, ch):
        if eye is None:
            continue
        sx, sy = eye["span"]
        eye_img = eye["plate"].copy()
        eye_img.alpha_composite(eye["iris"].transform(eye["iris"].size, Image.AFFINE, (1, 0, -gx * sx, 0, 1, -gy * sy),
                                                      resample=Image.BILINEAR))
        h.paste(eye_img, eye["box"][:2], eye["inner"])
    return h

def _blink_times(dur, seed):
    """Deterministic (seeded per-scene) natural-feeling blink schedule:
    first blink after 0.8-2.5s, then every 2.5-4.5s thereafter."""
    random.seed(seed + 1)  # +1 so it doesn't reuse _light_rays' sequence
    times, t = [], random.uniform(0.8, 2.5)
    while t < dur:
        times.append(t)
        t += random.uniform(2.5, 4.5)
    return times

def _is_blinking(t, blink_times, blink_dur=0.12):
    return any(bt <= t < bt + blink_dur for bt in blink_times)

def _prep_bg(path):
    bg = Image.open(path).convert("RGB"); s = max(W/bg.width, H/bg.height) * 1.12
    bg = bg.resize((int(bg.width*s), int(bg.height*s)))
    return bg, (bg.width-W)//2, (bg.height-H)//2

def _light_rays(seed):
    random.seed(seed)
    return [(random.randint(0, W), random.randint(55, 115), random.randint(16, 30), random.uniform(0, 6.28)) for _ in range(4)]

def _draw_rays(win, rays, t):
    ray_img = Image.new("RGBA", win.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ray_img)
    for x, rw, alpha, phase in rays:
        sway = 45*math.sin(t*0.3+phase); top_x = x+sway; bot_x = x+sway*2.3
        d.polygon([(top_x-rw*0.15, -20), (top_x+rw*0.15, -20), (bot_x+rw, H+20), (bot_x-rw, H+20)], fill=(255, 255, 235, alpha))
    win.alpha_composite(ray_img)

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Linux / Modal
    "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/Arial.ttf",  # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",     # macOS
]
def _font(sz):
    for p in _FONT_PATHS:
        try: return ImageFont.truetype(p, sz)
        except OSError: continue
    return ImageFont.load_default()

def render_scene(speaker, location, line, tag, bg_override=None):
    ch = CHARACTERS.get(speaker.strip().lower(), CHARACTERS["bini"])
    base = Image.open(str(ASSET / "characters" / ch["base"])).convert("RGBA")
    s = TH/base.height; base = base.resize((int(base.width*s), TH)); BW, BH = base.size
    heads = _build_heads(base, ch)
    mp3 = get_voiceover(line, tag, ch)
    # Kokoro already applies its own per-character voice + speed at synthesis
    # time (its voice bank is all adult narrators, so a modest pitch-only lift
    # still helps it read as younger); speed=1.0 keeps duration unchanged.
    if VOICE_ENGINE == "kokoro":
        clean, baby = _prep_audio(mp3, tag, ch["kokoro_pitch"], ch["kokoro_post_speed"])
    else:
        clean, baby = _prep_audio(mp3, tag, ch["pitch"], ch["speed"])
    wf = wave.open(baby, "rb"); dur = wf.getnframes()/wf.getframerate(); wf.close()
    cues = _cues(clean, dur); starts = np.array([c["start"] for c in cues])
    shape_at = lambda t: VMAP.get(cues[max(0, min(np.searchsorted(starts, t+0.05, side="right")-1, len(cues)-1))]["value"], "closed")
    bg, bgx, bgy = _prep_bg(bg_override or bg_for(location))
    seed = sum(ord(c) for c in tag); rays = _light_rays(seed)
    blink_times = _blink_times(dur, seed)
    bub = Image.new("RGBA", (30, 30), (0, 0, 0, 0)); ImageDraw.Draw(bub).ellipse((2, 2, 28, 28), outline=(255, 255, 255, 200), width=2, fill=(255, 255, 255, 45))
    random.seed(7); bubbles = [(random.randint(30, W-30), random.uniform(70, 130), random.uniform(0, dur), random.uniform(0.4, 1.1)) for _ in range(11)]
    frames = []
    for i in range(int(dur*FPS)):
        t = i/FPS; z = 1+0.05*t/dur; cw, ch2 = int(W/z), int(H/z)
        panx, pany = int(18*math.sin(t*0.22)), int(6*math.sin(t*0.17+1))
        x0 = max(0, min(bg.width-cw, bgx+(W-cw)//2+panx)); y0 = max(0, min(bg.height-ch2, bgy+(H-ch2)//2+pany))
        win = bg.crop((x0, y0, x0+cw, y0+ch2)).resize((W, H)).convert("RGBA")
        _draw_rays(win, rays, t)
        for bx, sp, ph, sz in bubbles: win.alpha_composite(bub.resize((int(30*sz), int(30*sz))), (bx, int(H-(sp*(t+ph)) % (H+40))))
        head = heads[shape_at(t)]
        if ch.get("blink_ready") and _is_blinking(t, blink_times): head = _blink_frame(head, ch)
        sc = 1+0.015*math.sin(t*1.5)
        im2 = head.resize((int(BW*sc), int(BH*sc))).rotate(3*math.sin(t*1.0), expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        px, py = W/2+25*math.sin(t*0.45), H*0.60+20*math.sin(t*1.5)
        win.alpha_composite(im2, (int(px-im2.width/2), int(py-im2.height/2)))
        frames.append(np.array(win.convert("RGB")))
    return frames, baby

def get_episode_bg(creature, tag):
    """Generate one underwater background featuring `creature` via FLUX Schnell
    (~$0.003). Falls back to the static reef.png if generation fails."""
    prompt = (
        f"cute friendly cartoon {creature} underwater ocean scene, soft pastel watercolor, "
        "coral reef, colorful fish, kawaii preschool children show style, warm sunlight rays, "
        f"the {creature} positioned in the upper half of the frame clearly visible, "
        "open clear water in the lower half of the frame, "
        "no text, no letters, vertical composition"
    )
    body = {"input": {"prompt": prompt, "width": W, "height": H,
                       "num_outputs": 1, "num_inference_steps": 4}}
    headers = {"Authorization": f"Bearer {_replicate_key()}",
               "Content-Type": "application/json", "Prefer": "wait"}
    try:
        req = urllib.request.Request(
            "https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions",
            data=json.dumps(body).encode(), headers=headers)
        resp = json.loads(urllib.request.urlopen(req, context=_SSL, timeout=120).read())
        poll_url = f"https://api.replicate.com/v1/predictions/{resp['id']}"
        while resp["status"] not in ("succeeded", "failed", "canceled"):
            time.sleep(2)
            r = urllib.request.Request(poll_url, headers={"Authorization": f"Bearer {_replicate_key()}"})
            resp = json.loads(urllib.request.urlopen(r, context=_SSL).read())
        if resp["status"] != "succeeded":
            raise RuntimeError(f"flux failed: {resp.get('error')}")
        out_url = resp["output"]
        if isinstance(out_url, list): out_url = out_url[0]
        img_path = str(WORK / f"{tag}_bg.png")
        data = urllib.request.urlopen(urllib.request.Request(out_url), context=_SSL).read()
        pathlib.Path(img_path).write_bytes(data)
        print(f"generated background: {creature}")
        return img_path
    except Exception as exc:
        print(f"[bg] FLUX failed ({exc}), using static reef.png")
        return bg_for("rainbow reef")

def _song_to_wav(mp3, tag):
    wav = str(WORK / f"{tag}_song.wav")
    subprocess.run([FF, "-y", "-i", mp3, "-ac", "2", "-ar", str(SR), wav], check=True, capture_output=True)
    return wav

def _dance_scene(song_wav, tag, bg_override=None):
    """Bini's closing sing-and-dance number. The song audio is a mixed vocal+
    instrumental track (not a clean vocal stem), so there's no reliable way to
    phoneme-align it with Rhubarb; instead the mouth cycles on a steady beat
    and the whole rig bounces/sways well beyond the talking-scene motion."""
    ch = CHARACTERS["bini"]
    base = Image.open(str(ASSET / "characters" / ch["base"])).convert("RGBA")
    s = TH/base.height; base = base.resize((int(base.width*s), TH)); BW, BH = base.size
    heads = _build_heads(base, ch)
    wf = wave.open(song_wav, "rb"); dur = wf.getnframes()/wf.getframerate(); wf.close()
    bg, bgx, bgy = _prep_bg(bg_override or bg_for("Rainbow Reef"))
    seed = sum(ord(c) for c in tag); rays = _light_rays(seed)
    blink_times = _blink_times(dur, seed)
    bub = Image.new("RGBA", (30, 30), (0, 0, 0, 0)); ImageDraw.Draw(bub).ellipse((2, 2, 28, 28), outline=(255, 255, 255, 200), width=2, fill=(255, 255, 255, 45))
    random.seed(11); bubbles = [(random.randint(30, W-30), random.uniform(70, 130), random.uniform(0, dur), random.uniform(0.4, 1.1)) for _ in range(14)]
    mouth_cycle = ["closed", "wide", "round", "mid"]
    frames = []
    for i in range(int(dur*FPS)):
        t = i/FPS
        panx, pany = int(18*math.sin(t*0.3)), int(6*math.sin(t*0.22+1))
        x0 = max(0, min(bg.width-W, bgx+panx)); y0 = max(0, min(bg.height-H, bgy+pany))
        win = bg.crop((x0, y0, x0+W, y0+H)).convert("RGBA")
        _draw_rays(win, rays, t)
        for bx, sp, ph, sz in bubbles: win.alpha_composite(bub.resize((int(30*sz), int(30*sz))), (bx, int(H-(sp*(t+ph)) % (H+40))))
        beat = t*1.5  # gentle, unhurried sway — matches the slow lullaby-adjacent song tempo
        bounce = 30*abs(math.sin(beat)); sway = 26*math.sin(beat*0.6); sc = 1+0.06*abs(math.sin(beat))
        rot = 8*math.sin(beat*0.6)
        head = heads[mouth_cycle[int(t*2.2) % 4]]
        if _is_blinking(t, blink_times): head = _blink_frame(head, ch)
        im2 = head.resize((int(BW*sc), int(BH*sc))).rotate(rot, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        px, py = W/2+sway, H*0.62-bounce
        win.alpha_composite(im2, (int(px-im2.width/2), int(py-im2.height/2)))
        frames.append(np.array(win.convert("RGB")))
    return frames, song_wav

def _card(title, sub, dur=1.8, title_size=None, character="bini"):
    bg, bgx, bgy = _prep_bg(bg_for("Rainbow Reef")); frame0 = bg.crop((bgx, bgy, bgx+W, bgy+H)).convert("RGBA")
    head = Image.open(str(ASSET / "characters" / CHARACTERS[character]["base"])).convert("RGBA")
    s = (TH*0.9)/head.height; head = head.resize((int(head.width*s), int(TH*0.9)))
    # Auto-shrink long titles ("SEABINI" fits fine at 118; longer titles like
    # "Bini's Real Ocean" need a smaller size or they clip past frame edges.
    if title_size is None:
        title_size = 118 if len(title) <= 8 else max(56, int(118 * 8 / len(title)))
    frames = []
    for i in range(int(dur*FPS)):
        t = i/FPS; f = frame0.copy()
        f.alpha_composite(head, (int(W/2-head.width/2), int(H*0.5-head.height/2+12*math.sin(t*1.6))))
        d = ImageDraw.Draw(f)
        d.text((W/2, 150), title, anchor="mm", font=_font(title_size), fill=(255, 255, 255), stroke_width=6, stroke_fill=(30, 90, 160))
        d.text((W/2, 250), sub, anchor="mm", font=_font(42), fill=(255, 255, 255), stroke_width=3, stroke_fill=(30, 90, 160))
        frames.append(np.array(f.convert("RGB")))
    return frames, None

def _wav_samples(path, n):
    w = wave.open(path, "rb"); nch = w.getnchannels(); a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16); w.close()
    a = a.reshape(-1, nch)
    if nch == 1: a = np.repeat(a, 2, axis=1)
    if len(a) >= n: return a[:n]
    return np.vstack([a, np.zeros((n-len(a), 2), dtype=np.int16)])

def build_episode(episode, out_path, music=None):
    title = episode.get("title", "SEABINI")
    # Extract the creature/topic for dynamic background generation.
    # learning_objective is "A jellyfish has no brain..." — the subject noun
    # sits right after "A/An" at the start, which is the most reliable signal.
    import re as _re
    _SKIP = {"the", "a", "an", "of", "and", "in", "at", "is", "are", "can", "has",
             "adventures", "discoveries", "little", "big", "amazing", "seabini",
             "wonderland", "friends", "fun", "time", "day", "world", "life", "journey"}
    creature = "sea creature"
    obj = episode.get("learning_objective", "")
    # "A jellyfish has..." / "An octopus has..." → extract noun after A/An
    m = _re.match(r"^An?\s+(\w+)", obj, _re.IGNORECASE)
    if m and m.group(1).lower() not in _SKIP:
        creature = m.group(1).lower()
    else:
        # Fall back to first content word of the title
        words = [w for w in _re.sub(r"[^a-zA-Z ]", "", title).split() if w.lower() not in _SKIP]
        if words:
            creature = words[0].lower()
    episode_bg = get_episode_bg(creature, "epbg")
    segments = [_card("SEABINI", "Adventures Beneath the Blue!")]
    for i, sc in enumerate(episode["scenes"]):
        segments.append(render_scene(sc["speaker"], sc.get("location", "Rainbow Reef"), sc["line"], f"sc{i}", bg_override=episode_bg))
        print("rendered scene", i, sc["speaker"])
    all_frames, chunks = [], []
    for frames, wav in segments:
        all_frames.extend(frames); n = int(round(len(frames)/FPS*SR))
        chunks.append(np.zeros((n, 2), dtype=np.int16) if wav is None else _wav_samples(wav, n))
    narration = np.vstack(chunks).astype(np.float32)
    if music:
        mw = str(WORK / "music.wav")
        subprocess.run([FF, "-y", "-i", music, "-ac", "2", "-ar", "44100", mw], check=True, capture_output=True)
        w = wave.open(mw, "rb"); a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2); w.close()
        if len(a) >= len(narration):
            m = a[:len(narration)]
        else:  # loop music to cover the whole episode
            reps = int(np.ceil(len(narration) / len(a))); m = np.tile(a, (reps, 1))[:len(narration)]
        narration = np.clip(narration + m.astype(np.float32) * 0.20, -32768, 32767)

    song = episode.get("song")
    if song and song.get("lyrics"):
        print("generating song...")
        song_mp3 = get_song(song["lyrics"], "song0")
        song_wav = _song_to_wav(song_mp3, "song0")
        song_frames, _ = _dance_scene(song_wav, "song0", bg_override=episode_bg)
        n = int(round(len(song_frames)/FPS*SR))
        song_audio = _wav_samples(song_wav, n).astype(np.float32)
        all_frames = all_frames + song_frames
        narration = np.vstack([narration, song_audio])
        print("added song:", song.get("title"), round(len(song_frames)/FPS, 1), "s")

    final = narration.astype(np.int16)
    wav_path = str(WORK / "episode_audio.wav")
    ww = wave.open(wav_path, "wb"); ww.setnchannels(2); ww.setsampwidth(2); ww.setframerate(SR); ww.writeframes(final.tobytes()); ww.close()
    silent = str(WORK / "episode_silent.mp4")
    ImageSequenceClip(all_frames, fps=FPS).write_videofile(silent, fps=FPS, codec="libx264", audio=False, preset="medium", logger=None)
    subprocess.run([FF, "-y", "-i", silent, "-i", wav_path, "-c:v", "copy", "-c:a", "aac", "-shortest", out_path], check=True, capture_output=True)
    print("wrote", out_path, round(len(all_frames)/FPS, 1), "s")
    return out_path
