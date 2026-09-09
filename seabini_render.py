"""SEABINI episode renderer (2D cutout-puppet rig, pure Python/CPU).

Given an episode JSON from seabini_script.py, renders each scene — the speaker's
rigged head (fixed base + drawn mouth visemes in the character's real colours,
lip-synced by Rhubarb) over the scene's location background — then stitches a
title card, all scenes, and an outro into one vertical Short with synced audio
and optional background music.

Runtime deps: moviepy, pillow<10, numpy, imageio-ffmpeg (Python) + the Rhubarb
CLI + ffmpeg. Point SEABINI_RHUBARB at rhubarb.exe (or set it below).
"""
import os, json, math, wave, random, subprocess, pathlib, urllib.request, tempfile, base64, time, ssl
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
    "bini": dict(base="bini_base.png", cx=180, cy=284, lip=(222, 106, 114), dark=(58, 16, 24), tongue=(239, 130, 140), ms=1.0, erase_mult=1.0, face_pt=(180, 262), pitch=1.28, speed=0.86, kokoro_voice="af_heart", kokoro_speed=1.0, kokoro_pitch=1.35, kokoro_post_speed=0.85),
    "tula": dict(base="tula_base.png", cx=216, cy=303, lip=(120, 140, 70), dark=(58, 18, 20), tongue=(185, 70, 68), ms=1.2, erase_mult=1.15, face_pt=(216, 282), pitch=1.05, speed=0.82, kokoro_voice="af_bella", kokoro_speed=1.0, kokoro_pitch=1.11, kokoro_post_speed=0.81),
    "ollo": dict(base="ollo_base.png", cx=275, cy=262, lip=(133, 81, 189), dark=(50, 12, 22), tongue=(150, 55, 60), ms=1.15, erase_mult=1.15, face_pt=(275, 240), pitch=1.15, speed=0.95, kokoro_voice="bf_emma", kokoro_speed=1.0, kokoro_pitch=1.21, kokoro_post_speed=0.94),
    "dodo": dict(base="dodo_base.png", cx=188, cy=235, lip=(110, 170, 200), dark=(55, 24, 28), tongue=(160, 65, 68), ms=1.4, erase_mult=1.7, face_pt=(188, 180), pitch=1.20, speed=1.04, kokoro_voice="am_fenrir", kokoro_speed=1.0, kokoro_pitch=1.27, kokoro_post_speed=1.03),
    "pipi": dict(base="pipi_base.png", cx=165, cy=266, lip=(234, 159, 66), dark=(60, 26, 32), tongue=(170, 68, 72), ms=0.9, erase_mult=1.15, face_pt=(165, 240), pitch=1.35, speed=0.92, kokoro_voice="af_nicole", kokoro_speed=1.0, kokoro_pitch=1.42, kokoro_post_speed=0.91),
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
    for line in (HERE / ".APIs.txt").read_text().splitlines():
        if line.lower().startswith("replicate"): return line.split("=", 1)[1].strip()
    raise SystemExit("No REPLICATE_API_TOKEN")

def _fit_lyrics(lyrics, limit=390):
    """minimax/music-01 hard-caps lyrics at 350-400 chars and errors past that;
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

def get_song(lyrics, tag):
    """Sung closing number via Replicate's minimax/music-01, voice-anchored to
    Bini's own Kokoro voice (seabini_assets/voice/bini_song_ref.mp3) so the
    singing voice matches her speaking voice. ~$0.04/song."""
    lyrics = _fit_lyrics(lyrics)
    voice_ref = ASSET / "voice" / "sample_song_ref.mp3"
    voice_b64 = base64.b64encode(voice_ref.read_bytes()).decode()
    body = {"input": {"lyrics": lyrics, "voice_file": f"data:audio/mpeg;base64,{voice_b64}"}}
    headers = {"Authorization": f"Bearer {_replicate_key()}", "Content-Type": "application/json", "Prefer": "wait"}
    req = urllib.request.Request("https://api.replicate.com/v1/models/minimax/music-01/predictions",
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
        head = heads[shape_at(t)]; sc = 1+0.015*math.sin(t*1.5)
        im2 = head.resize((int(BW*sc), int(BH*sc))).rotate(3*math.sin(t*1.0), expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        px, py = W/2+25*math.sin(t*0.45), H*0.42+20*math.sin(t*1.5)
        win.alpha_composite(im2, (int(px-im2.width/2), int(py-im2.height/2)))
        frames.append(np.array(win.convert("RGB")))
    return frames, baby

def get_episode_bg(creature, tag):
    """Generate one underwater background featuring `creature` via FLUX Schnell
    (~$0.003). Falls back to the static reef.png if generation fails."""
    prompt = (
        f"cute friendly cartoon {creature} underwater ocean scene, soft pastel watercolor, "
        "coral reef, colorful fish, kawaii preschool children show style, warm sunlight rays, "
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
        im2 = head.resize((int(BW*sc), int(BH*sc))).rotate(rot, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        px, py = W/2+sway, H*0.46-bounce
        win.alpha_composite(im2, (int(px-im2.width/2), int(py-im2.height/2)))
        frames.append(np.array(win.convert("RGB")))
    return frames, song_wav

def _card(title, sub, dur=1.8):
    bg, bgx, bgy = _prep_bg(bg_for("Rainbow Reef")); frame0 = bg.crop((bgx, bgy, bgx+W, bgy+H)).convert("RGBA")
    head = Image.open(str(ASSET / "characters" / "bini_base.png")).convert("RGBA")
    s = (TH*0.9)/head.height; head = head.resize((int(head.width*s), int(TH*0.9)))
    frames = []
    for i in range(int(dur*FPS)):
        t = i/FPS; f = frame0.copy()
        f.alpha_composite(head, (int(W/2-head.width/2), int(H*0.5-head.height/2+12*math.sin(t*1.6))))
        d = ImageDraw.Draw(f)
        d.text((W/2, 150), title, anchor="mm", font=_font(118), fill=(255, 255, 255), stroke_width=6, stroke_fill=(30, 90, 160))
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
