"""SEABINI episode renderer (2D cutout-puppet rig, pure Python/CPU).

Given an episode JSON from seabini_script.py, renders each scene — the speaker's
rigged head (fixed base + drawn mouth visemes in the character's real colours,
lip-synced by Rhubarb) over the scene's location background — then stitches a
title card, all scenes, and an outro into one vertical Short with synced audio
and optional background music.

Runtime deps: moviepy, pillow<10, numpy, imageio-ffmpeg (Python) + the Rhubarb
CLI + ffmpeg. Point SEABINI_RHUBARB at rhubarb.exe (or set it below).
"""
import os, json, math, wave, random, subprocess, pathlib, urllib.request, tempfile
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

# Finalized per-character rig params + a distinct voice (pitch/speed vary the
# shared ElevenLabs voice so each personality sounds different but reliable).
CHARACTERS = {
    "bini": dict(base="bini_base.png", cx=180, cy=284, lip=(222, 106, 114), dark=(58, 16, 24), tongue=(239, 130, 140), ms=1.0, erase_mult=1.0, face_pt=(180, 262), pitch=1.28, speed=0.86),
    "tula": dict(base="tula_base.png", cx=216, cy=303, lip=(120, 140, 70), dark=(58, 18, 20), tongue=(185, 70, 68), ms=1.2, erase_mult=1.15, face_pt=(216, 282), pitch=1.05, speed=0.82),
    "ollo": dict(base="ollo_base.png", cx=275, cy=262, lip=(133, 81, 189), dark=(50, 12, 22), tongue=(150, 55, 60), ms=1.15, erase_mult=1.15, face_pt=(275, 240), pitch=1.15, speed=0.95),
    "dodo": dict(base="dodo_base.png", cx=188, cy=235, lip=(110, 170, 200), dark=(55, 24, 28), tongue=(160, 65, 68), ms=1.4, erase_mult=1.7, face_pt=(188, 180), pitch=1.20, speed=1.04),
    "pipi": dict(base="pipi_base.png", cx=165, cy=266, lip=(234, 159, 66), dark=(60, 26, 32), tongue=(170, 68, 72), ms=0.9, erase_mult=1.15, face_pt=(165, 240), pitch=1.35, speed=0.92),
}
VOICE_ID = "MF3mGyEYCl7XYWbV9V6O"  # ElevenLabs "Elli"; pitch/speed per character above
LOC_BG = {"rainbow reef": "reef.png", "seagrass garden": "seagrass.png", "shell beach": "starfish.png",
          "mangrove bay": "seagrass.png", "blue lagoon": "reef.png", "the old wreck": "reef.png", "moonlight reef": "reef.png"}

def bg_for(loc):
    return str(ASSET / "backgrounds" / LOC_BG.get((loc or "").strip().lower(), "reef.png"))

VOICE_ENGINE = os.environ.get("SEABINI_VOICE_ENGINE", "piper")  # "piper" (free, local) or "elevenlabs" (paid, cloud)
_PIPER_VOICE = None  # lazy-loaded singleton; loading the model is the slow part

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
    open(mp3, "wb").write(urllib.request.urlopen(req).read())
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

def get_voiceover(text, tag):
    """Free local Piper by default; set SEABINI_VOICE_ENGINE=elevenlabs for the paid cloud voice."""
    if VOICE_ENGINE == "elevenlabs":
        return _get_voiceover_elevenlabs(text, tag)
    return _get_voiceover_piper(text, tag)

def _prep_audio(mp3, tag, pitch, speed):
    clean, baby = str(WORK / f"{tag}_clean.wav"), str(WORK / f"{tag}_baby.wav")
    subprocess.run([FF, "-y", "-i", mp3, "-ac", "1", "-ar", "44100", clean], check=True, capture_output=True)
    subprocess.run([FF, "-y", "-i", mp3, "-ac", "2", "-ar", "44100", "-af",
                    f"asetrate=44100*{pitch},aresample=44100,atempo={(1/pitch)*speed:.4f}", baby], check=True, capture_output=True)
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

def render_scene(speaker, location, line, tag):
    ch = CHARACTERS.get(speaker.strip().lower(), CHARACTERS["bini"])
    base = Image.open(str(ASSET / "characters" / ch["base"])).convert("RGBA")
    s = TH/base.height; base = base.resize((int(base.width*s), TH)); BW, BH = base.size
    heads = _build_heads(base, ch)
    mp3 = get_voiceover(line, tag)
    clean, baby = _prep_audio(mp3, tag, ch["pitch"], ch["speed"])
    wf = wave.open(baby, "rb"); dur = wf.getnframes()/wf.getframerate(); wf.close()
    cues = _cues(clean, dur); starts = np.array([c["start"] for c in cues])
    shape_at = lambda t: VMAP.get(cues[max(0, min(np.searchsorted(starts, t+0.05, side="right")-1, len(cues)-1))]["value"], "closed")
    bg, bgx, bgy = _prep_bg(bg_for(location))
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
    segments = [_card("SEABINI", "Adventures Beneath the Blue!")]
    for i, sc in enumerate(episode["scenes"]):
        segments.append(render_scene(sc["speaker"], sc.get("location", "Rainbow Reef"), sc["line"], f"sc{i}"))
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
    final = narration.astype(np.int16)
    wav_path = str(WORK / "episode_audio.wav")
    ww = wave.open(wav_path, "wb"); ww.setnchannels(2); ww.setsampwidth(2); ww.setframerate(SR); ww.writeframes(final.tobytes()); ww.close()
    silent = str(WORK / "episode_silent.mp4")
    ImageSequenceClip(all_frames, fps=FPS).write_videofile(silent, fps=FPS, codec="libx264", audio=False, preset="medium", logger=None)
    subprocess.run([FF, "-y", "-i", silent, "-i", wav_path, "-c:v", "copy", "-c:a", "aac", "-shortest", out_path], check=True, capture_output=True)
    print("wrote", out_path, round(len(all_frames)/FPS, 1), "s")
    return out_path
