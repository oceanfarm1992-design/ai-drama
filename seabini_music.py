"""Generate an original, gentle preschool background music track procedurally
(numpy) — no external service, fully owned, brand-safe ("all music original").
Music-box melody over soft pad chords, in a pentatonic major key so it always
sounds pleasant and loops cleanly.

Run:  python seabini_music.py [out.wav]   (default seabini_assets/music/theme.wav)
"""
import sys, pathlib, subprocess
import numpy as np
import imageio_ffmpeg

SR = 44100
HERE = pathlib.Path(__file__).resolve().parent

def _note(freq, dur, amp=0.5):
    t = np.linspace(0, dur, int(SR*dur), False)
    env = np.exp(-3.2*t)                       # bell-like decay
    w = np.sin(2*np.pi*freq*t) + 0.4*np.sin(2*np.pi*2*freq*t) + 0.18*np.sin(2*np.pi*3*freq*t)
    return amp*env*w

def _pad(freqs, dur, amp=0.16):
    t = np.linspace(0, dur, int(SR*dur), False)
    env = np.minimum(1, t/0.35) * np.minimum(1, (dur-t)/0.4)   # soft attack/release
    w = sum(np.sin(2*np.pi*f*t) for f in freqs) / len(freqs)
    return amp*env*w

def generate(seconds=24.0):
    A = 440.0
    def hz(semi):  # semitones from A4
        return A * 2**(semi/12)
    # C-major pentatonic across two octaves (C D E G A)
    penta = [hz(s) for s in (-9, -7, -5, -2, 0, 3, 5, 7, 10, 12)]
    # gentle chord progression (C, Am, F, G) as triads, one per bar
    chords = [[hz(-9), hz(-5), hz(-2)], [hz(0), hz(3), hz(7)], [hz(-4), hz(0), hz(3)], [hz(-2), hz(2), hz(5)]]
    beat = 0.5           # seconds per melody note
    bar = beat*4
    buf = np.zeros(int(SR*seconds), dtype=np.float32)
    # pad chords
    tpos = 0.0; ci = 0
    while tpos < seconds:
        seg = _pad(chords[ci % 4], min(bar, seconds-tpos))
        s = int(tpos*SR); buf[s:s+len(seg)] += seg[:len(buf)-s]
        tpos += bar; ci += 1
    # melody: gentle random walk over the pentatonic, one note per beat
    rng = np.random.default_rng(7); idx = 4
    tpos = 0.0
    while tpos < seconds:
        idx = int(np.clip(idx + rng.integers(-1, 2), 2, len(penta)-1))
        if rng.random() < 0.2:            # occasional rest for breathing room
            tpos += beat; continue
        seg = _note(penta[idx], beat*1.6, amp=0.42)
        s = int(tpos*SR); e = min(len(buf), s+len(seg)); buf[s:e] += seg[:e-s]
        tpos += beat
    # simple soft reverb (a couple of decayed delays)
    for delay, g in ((0.09, 0.25), (0.17, 0.15)):
        d = int(delay*SR); buf[d:] += g*buf[:-d]
    buf /= np.max(np.abs(buf)) + 1e-6
    buf *= 0.9
    stereo = np.stack([buf, buf], axis=1)
    return (stereo*32767).astype(np.int16)

if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE/"seabini_assets"/"music"/"theme.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    import wave
    data = generate()
    w = wave.open(str(out), "wb"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(data.tobytes()); w.close()
    mp3 = out.with_suffix(".mp3")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(out), str(mp3)], check=True, capture_output=True)
    print("wrote", mp3)
