"""Original SEABINI background music — a composed, catchy kids tune rendered
with REAL instruments (SoundFont via FluidSynth). Fully original + free/local.

Approach = "1 + 2": proper composition (melody hook + chords + bass + light
percussion, the catchy part) rendered through a General-MIDI soundfont for real
marimba / glockenspiel / vibraphone / bass / drums (the good-sound part).

Setup (one time): FluidSynth CLI + a GM soundfont. Point env vars at them, or
edit the defaults below:
  SEABINI_FLUIDSYNTH -> fluidsynth.exe
  SEABINI_SOUNDFONT  -> a .sf2/.sf3 General-MIDI soundfont

Run:  python seabini_music.py [out.mp3]   (default seabini_assets/music/theme.mp3)
Needs: pretty_midi (pip), imageio-ffmpeg, FluidSynth + soundfont.
"""
import os, sys, subprocess, pathlib
import pretty_midi
import imageio_ffmpeg

HERE = pathlib.Path(__file__).resolve().parent
_TOOLS = r"C:/Users/Official/AppData/Local/Temp/claude/C--Calude-Apps-AI-Drama/0adc92ae-7e8c-4231-82bc-a08ec65a170e/scratchpad/music_tools"
FLUIDSYNTH = os.environ.get("SEABINI_FLUIDSYNTH", f"{_TOOLS}/fluidsynth/fluidsynth-v2.6.0-win10-x64-cpp11/bin/fluidsynth.exe")
SOUNDFONT = os.environ.get("SEABINI_SOUNDFONT", f"{_TOOLS}/soundfont.sf2")

BPM = 104
SPB = 60.0 / BPM                      # seconds per beat
GM = dict(glockenspiel=9, marimba=12, vibraphone=11, acoustic_bass=32, pizzicato=45)

# 4-bar chord loop (happy, catchy): C - G - Am - F
CHORDS = [[60, 64, 67], [55, 59, 62], [57, 60, 64], [53, 57, 60]]
BASS = [48, 43, 45, 41]
# Catchy quarter-note pentatonic melody hook, one entry per beat across 4 bars (16 beats)
MELODY = [67, 72, 76, 72,  74, 72, 69, 69,  69, 72, 76, 72,  74, 72, 69, 67]
CYCLES = 3                            # ~28 s, loops cleanly

def compose():
    pm = pretty_midi.PrettyMIDI(initial_tempo=BPM)
    mel = pretty_midi.Instrument(program=GM["marimba"])
    spark = pretty_midi.Instrument(program=GM["glockenspiel"])
    chords = pretty_midi.Instrument(program=GM["vibraphone"])
    bass = pretty_midi.Instrument(program=GM["acoustic_bass"])
    drums = pretty_midi.Instrument(program=0, is_drum=True)

    def add(inst, pitch, beat, dur, vel):
        inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=beat*SPB, end=(beat+dur)*SPB))

    for c in range(CYCLES):
        base = c * 16
        for b in range(16):
            bar = (b // 4) % 4
            beat = base + b
            # melody + a soft octave sparkle on strong beats
            note = MELODY[b]
            add(mel, note, beat, 0.9, 88)
            if b % 2 == 0:
                add(spark, note + 12, beat, 0.5, 40)
            # chords: soft block on beats 1 and 3 of each bar
            if b % 4 in (0, 2):
                for p in CHORDS[bar]:
                    add(chords, p, beat, 1.8, 52)
            # bass: root on beats 1 and 3
            if b % 4 in (0, 2):
                add(bass, BASS[bar], beat, 1.6, 66)
            # percussion: kick 1&3, backbeat clap 2&4, light shaker every beat
            if b % 4 in (0, 2):
                add(drums, 36, beat, 0.5, 74)     # kick
            if b % 4 in (1, 3):
                add(drums, 39, beat, 0.4, 48)     # clap (backbeat)
            add(drums, 70, beat, 0.2, 34)         # maracas/shaker
            add(drums, 70, beat + 0.5, 0.2, 26)   # off-beat shaker for bounce

    for inst in (mel, spark, chords, bass, drums):
        pm.instruments.append(inst)
    return pm

def render(pm, out_mp3):
    mid = str(HERE / "_theme.mid"); wav = str(HERE / "_theme.wav")
    pm.write(mid)
    subprocess.run([FLUIDSYNTH, "-ni", "-g", "0.9", "-F", wav, "-r", "44100", SOUNDFONT, mid],
                   check=True, capture_output=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", wav, "-af", "afade=t=in:d=0.6,afade=t=out:st=%.2f:d=0.8" % (pm.get_end_time() - 0.8), out_mp3],
                   check=True, capture_output=True)
    os.remove(mid); os.remove(wav)
    print("wrote", out_mp3)

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "seabini_assets" / "music" / "theme.mp3")
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    render(compose(), out)
