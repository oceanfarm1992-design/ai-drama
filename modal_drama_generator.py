"""
SKILL FILE: Serverless AI Drama Generator Pipeline
Platform: Modal (serverless CPU) + hosted APIs, chosen per stage for cost/quality:
  - Script:  OpenAI gpt-4o-mini (API)       - cheap, reliable structured JSON at this volume
  - Voice:   ElevenLabs eleven_multilingual_v2 (API) - realistic, covers 31 languages incl. Tamil
  - Photos:  Replicate black-forest-labs/flux-schnell (API) - cheap, realistic stills
  - Motion:  Ken Burns pan/zoom over the stills (moviepy, CPU) - no GPU, ~cents/episode.
             (An earlier build self-hosted Stable Video Diffusion on an A100, but it
             cost ~$7/episode and looked choppy; Ken Burns is far cheaper and cleaner.)
Target: 63-second Vertical Video for TikTok Creator Rewards / YouTube Shorts

Serialized format: each language runs its own persistent SERIES_LENGTH-episode arc
(same cast, continuing plot, cliffhanger endings) via the `series_state` modal.Dict.
Every 31st run for a given language automatically starts a brand-new series with an
AI-invented cast/premise. No manual intervention needed between series rotations.

Required Modal secrets (create with `modal secret create <name> KEY=value ...`):
  - openai-secret:      OPENAI_API_KEY
  - elevenlabs-secret:  ELEVENLABS_API_KEY (optional ELEVENLABS_VOICE_ID override)
  - replicate-secret:   REPLICATE_API_TOKEN
  - r2-credentials:     R2_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
                        R2_BUCKET_NAME, R2_PUBLIC_BASE_URL
  - social-api-secret:  SOCIAL_API_KEY
Optional env var: WATERMARK_TEXT (defaults to "@YourBrand")

Deploy with `modal deploy modal_drama_generator.py` to get the HTTPS URL for
trigger_pipeline, which .github/workflows/daily_render.yaml POSTs to.
"""

import os
import json
import uuid
import modal

# ==========================================
# 1. INFRASTRUCTURE & ENVIRONMENT SETUP
# ==========================================

# Persists each language's ongoing series: episode number, cast bios, and running
# plot recap, so consecutive daily runs continue the same story instead of each
# being a standalone one-off.
series_state = modal.Dict.from_name("drama-series-state", create_if_missing=True)

SERIES_LENGTH = 30  # episodes per series before a new cast/premise takes over
FRAME_SIZE = (720, 1280)  # vertical 9:16 output

# Lightweight CPU-only image: visuals come from Replicate (API) and motion is a
# Ken Burns pan/zoom done in moviepy, so there's no GPU/torch/diffusers stack.
app_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "moviepy==1.0.3",
        "numpy<2",
        "openai",
        "elevenlabs",
        "replicate",
        "boto3",
        "requests",
        "pillow<10",  # moviepy 1.0.3's resize() uses Image.ANTIALIAS, removed in Pillow 10+
        "fastapi[standard]"
    )
)

app = modal.App("multi-lang-drama-generator", image=app_image)

# ==========================================
# 2. SCRIPT GENERATION (LLM)
# ==========================================

@app.function(secrets=[modal.Secret.from_name("openai-secret")], timeout=120)
def generate_script(language: str, topic_seed: str, episode_number: int, characters: str, plot_summary: str) -> dict:
    """Writes one episode's script, continuing an ongoing SERIES_LENGTH-episode serialized story."""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    if episode_number == 1:
        continuity_instructions = f"""
        This is EPISODE 1 of a brand-new {SERIES_LENGTH}-episode series. Invent a fresh
        premise and cast of characters, loosely inspired by: {topic_seed}.
        End the episode on a sharp cliffhanger that makes viewers desperate for episode 2.
        """
    elif episode_number < SERIES_LENGTH:
        continuity_instructions = f"""
        This is EPISODE {episode_number} of an ongoing {SERIES_LENGTH}-episode series.
        Established characters: {characters}
        What has happened so far: {plot_summary}
        Continue the story directly from that point — do not restart or re-introduce the
        cast. End on a new, sharper cliffhanger ("brain rot" serialized drama pacing) that
        makes viewers feel they *must* watch tomorrow's episode.
        """
    else:
        continuity_instructions = f"""
        This is the FINAL EPISODE ({SERIES_LENGTH}) of this series.
        Established characters: {characters}
        What has happened so far: {plot_summary}
        Deliver a satisfying resolution to the overall story arc. Do not set up a new
        cliffhanger for this series — a new, unrelated series with a new cast begins next.
        """

    prompt = f"""
    {continuity_instructions}

    Language: {language}.
    Output strictly as JSON containing exactly 12 scenes plus continuity fields.
    Format:
    {{
      "series_title": "...",
      "title": "this episode's title...",
      "characters": "updated one-paragraph bios of the ongoing cast, for continuity into the next episode",
      "recap": "one-paragraph summary of everything that has happened through this episode, for continuity into the next episode",
      "scenes": [
        {{"visual_prompt": "detailed cinematic prompt...", "voiceover": "dialogue here..."}}
      ]
    }}
    Ensure the total spoken dialogue takes roughly 63 seconds at a normal speaking pace.
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini", # Replace with your Qwen 2.5 endpoint
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}]
    )

    return json.loads(response.choices[0].message.content)

# ==========================================
# 3. VOICEOVER GENERATION (TTS)
# ==========================================

@app.function(secrets=[modal.Secret.from_name("elevenlabs-secret")], timeout=120)
def generate_voiceover(text: str, language: str, index: int) -> bytes:
    """Generates localized voiceover audio for a specific scene via ElevenLabs.

    eleven_multilingual_v2 covers all 31 of its supported languages (including
    Tamil) through one API/voice, so no per-language branching is needed here.

    Returns the raw mp3 bytes (not a path) so the clip can travel to the
    assembly container, which has a separate filesystem.
    """
    import time
    import random
    from elevenlabs.client import ElevenLabs
    from elevenlabs.core.api_error import ApiError

    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # "Rachel", a default premade voice

    # Retry on HTTP 429: the free/low tiers cap concurrent requests, and we fan
    # out all 12 scene voiceovers at once, so back off and retry when throttled.
    for attempt in range(8):
        try:
            audio = client.text_to_speech.convert(
                voice_id=voice_id,
                model_id="eleven_multilingual_v2",
                text=text,
                language_code=language,
            )
            return b"".join(audio)
        except ApiError as e:
            if getattr(e, "status_code", None) == 429 and attempt < 7:
                backoff = min(30, 5 * (attempt + 1)) + random.uniform(0, 2)
                print(f"Scene {index} voiceover throttled (429); retrying in {backoff:.1f}s")
                time.sleep(backoff)
                continue
            raise

# ==========================================
# 4. VISUALS: STILL IMAGE (API) -> ANIMATED VIDEO (self-hosted)
# ==========================================

@app.function(secrets=[modal.Secret.from_name("replicate-secret")], timeout=300)
def generate_scene_image(visual_prompt: str, index: int) -> bytes:
    """Generates a realistic vertical still image for a scene via Replicate's hosted FLUX.1-schnell.

    Cheap and high-quality relative to self-hosting an image model on Modal at
    this pipeline's volume, since a hosted API avoids per-container cold starts.
    """
    import time
    import random
    import requests
    from replicate.client import Client
    from replicate.exceptions import ReplicateError

    client = Client(api_token=os.environ["REPLICATE_API_TOKEN"])

    # Create the prediction, then poll with quick GETs instead of holding one
    # long HTTP request open (which is what was hitting httpx ReadTimeout).
    # Retry on HTTP 429: fanning out 12 scenes at once exceeds Replicate's
    # reduced burst limit when the account balance is under $10, so back off
    # and retry until the throttle window clears.
    model = client.models.get("black-forest-labs/flux-schnell")

    prediction = None
    for attempt in range(8):
        try:
            prediction = client.predictions.create(
                version=model.latest_version.id,
                input={"prompt": visual_prompt, "aspect_ratio": "9:16", "output_format": "png"},
            )
            break
        except ReplicateError as e:
            if getattr(e, "status", None) == 429 and attempt < 7:
                backoff = min(30, 5 * (attempt + 1)) + random.uniform(0, 2)
                print(f"Scene {index} throttled (429); retrying in {backoff:.1f}s")
                time.sleep(backoff)
                continue
            raise

    deadline = time.time() + 240
    while prediction.status not in ("succeeded", "failed", "canceled"):
        if time.time() > deadline:
            raise RuntimeError(f"Replicate image for scene {index} timed out (status={prediction.status})")
        time.sleep(2)
        prediction.reload()

    if prediction.status != "succeeded":
        raise RuntimeError(f"Replicate image for scene {index} {prediction.status}: {prediction.error}")

    # flux-schnell returns a list of image URLs; download the first.
    image_url = prediction.output[0]
    return requests.get(image_url, timeout=120).content


# SadTalker on Replicate: audio-driven "stylized" talking-face from one image.
SADTALKER_VERSION = "a519cc0cfebaaeade068b23899165a11ec76aaa1d2b313d40d214f204ec957a3"


@app.function(
    secrets=[modal.Secret.from_name("replicate-secret"), modal.Secret.from_name("r2-credentials")],
    timeout=900,
)
def lipsync_scene(image_bytes: bytes, audio_bytes: bytes, index: int) -> bytes:
    """Animates a character portrait to speak the given voiceover, via SadTalker.

    SadTalker wants the audio as .wav and both inputs as URLs, so we transcode the
    ElevenLabs mp3 with ffmpeg and stage both files in R2, then poll the prediction
    and return the resulting talking-head mp4 bytes (audio already baked in).
    """
    import io
    import time
    import subprocess
    import boto3
    import requests
    from replicate.client import Client

    # ElevenLabs returns mp3; SadTalker wants wav.
    with open(f"/tmp/a{index}.mp3", "wb") as f:
        f.write(audio_bytes)
    subprocess.run(
        ["ffmpeg", "-y", "-i", f"/tmp/a{index}.mp3", "-ar", "16000", f"/tmp/a{index}.wav"],
        check=True, capture_output=True,
    )
    with open(f"/tmp/a{index}.wav", "rb") as f:
        wav_bytes = f.read()

    # Stage the image + wav in R2 so SadTalker can fetch them by URL.
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    bucket = os.environ["R2_BUCKET_NAME"]
    base = os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")
    img_key, wav_key = f"tmp/ls_{index}.png", f"tmp/ls_{index}.wav"
    s3.put_object(Bucket=bucket, Key=img_key, Body=image_bytes, ContentType="image/png")
    s3.put_object(Bucket=bucket, Key=wav_key, Body=wav_bytes, ContentType="audio/wav")

    client = Client(api_token=os.environ["REPLICATE_API_TOKEN"])
    prediction = client.predictions.create(
        version=SADTALKER_VERSION,
        input={
            "source_image": f"{base}/{img_key}",
            "driven_audio": f"{base}/{wav_key}",
            "preprocess": "full",       # keep the whole framed portrait, not just a crop
            "still_mode": True,         # gentle head motion, steadier for stylized faces
            "use_enhancer": True,
        },
    )

    deadline = time.time() + 780
    while prediction.status not in ("succeeded", "failed", "canceled"):
        if time.time() > deadline:
            raise RuntimeError(f"SadTalker scene {index} timed out (status={prediction.status})")
        time.sleep(3)
        prediction.reload()

    if prediction.status != "succeeded":
        raise RuntimeError(f"SadTalker scene {index} {prediction.status}: {prediction.error}")

    return requests.get(prediction.output, timeout=180).content


@app.function(secrets=[modal.Secret.from_name("r2-credentials")], timeout=120)
def upload_bytes_to_r2(data: bytes, object_name: str, content_type: str = "video/mp4") -> str:
    """Uploads raw bytes to R2 and returns the public URL."""
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    s3.put_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=object_name, Body=data, ContentType=content_type)
    return f"{os.environ['R2_PUBLIC_BASE_URL'].rstrip('/')}/{object_name}"


@app.local_entrypoint()
def test_lipsync(
    prompt: str = "expressive stylized cartoon portrait of a young woman with long dark hair, semi-realistic illustration, clear frontal face, upper body, soft studio lighting, vertical 9:16",
    line: str = "You promised you'd never lie to me. And now I know the truth.",
):
    """One-scene proof of the talking-cartoon approach: portrait + voiceover -> SadTalker.
    Run: modal run modal_drama_generator.py::test_lipsync
    """
    img = generate_scene_image.remote(prompt, 0)
    audio = generate_voiceover.remote(line, "en", 0)
    clip = lipsync_scene.remote(img, audio, 0)
    url = upload_bytes_to_r2.remote(clip, "tests/lipsync_test.mp4")
    print(f"✅ LIPSYNC TEST VIDEO: {url}")


# Wan 2.2 A14B image-to-video on Replicate: open-source, real generated motion.
WAN_I2V_VERSION = "2c62e0842338726c74ad99a3c469255ce3f4c1f66ee000c265451b87754ac0c9"


@app.function(secrets=[modal.Secret.from_name("replicate-secret")], timeout=900)
def generate_video_wan(image_url: str, motion_prompt: str, index: int) -> bytes:
    """Animates a still (by URL) into a ~5s clip via the open-source Wan 2.2 i2v model."""
    import time
    import requests
    from replicate.client import Client

    client = Client(api_token=os.environ["REPLICATE_API_TOKEN"])
    prediction = client.predictions.create(
        version=WAN_I2V_VERSION,
        input={
            "image": image_url,
            "prompt": motion_prompt,
            "resolution": "480p",   # cheaper tier for the test; 720p available
            "num_frames": 81,       # ~5s at 16fps
            "frames_per_second": 16,
            "go_fast": True,
        },
    )

    deadline = time.time() + 780
    while prediction.status not in ("succeeded", "failed", "canceled"):
        if time.time() > deadline:
            raise RuntimeError(f"Wan i2v scene {index} timed out (status={prediction.status})")
        time.sleep(3)
        prediction.reload()

    if prediction.status != "succeeded":
        raise RuntimeError(f"Wan i2v scene {index} {prediction.status}: {prediction.error}")

    out = prediction.output
    if isinstance(out, list):
        out = out[0]
    return requests.get(out, timeout=180).content


@app.local_entrypoint()
def test_wan(
    prompt: str = "cinematic vertical portrait of a young woman with long dark hair in a dimly lit room, dramatic moody lighting, film still, 9:16",
    motion: str = "slow cinematic push-in, she slowly turns her head, hair and fabric move gently, subtle atmosphere",
):
    """One-scene proof of open-source generated motion: FLUX still -> Wan 2.2 i2v.
    Run: modal run modal_drama_generator.py::test_wan
    """
    img = generate_scene_image.remote(prompt, 0)
    img_url = upload_bytes_to_r2.remote(img, "tests/wan_input.png", "image/png")
    vid = generate_video_wan.remote(img_url, motion, 0)
    url = upload_bytes_to_r2.remote(vid, "tests/wan_test.mp4")
    print(f"✅ WAN TEST VIDEO: {url}")


# ==========================================
# 5. ASSEMBLY, WATERMARKING & UPLOAD
# ==========================================

def _make_watermark(text: str, duration: float):
    """Builds a bottom-right watermark clip using Pillow (avoids ImageMagick)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from moviepy.editor import ImageClip

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    except OSError:
        font = ImageFont.load_default()

    # Measure the text, then draw white text with a black outline on a transparent canvas.
    dummy = Image.new("RGBA", (1, 1))
    box = ImageDraw.Draw(dummy).textbbox((0, 0), text, font=font, stroke_width=2)
    pad = 8
    w, h = box[2] - box[0] + pad * 2, box[3] - box[1] + pad * 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text(
        (pad - box[0], pad - box[1]), text, font=font,
        fill=(255, 255, 255, 200), stroke_width=2, stroke_fill=(0, 0, 0, 200),
    )

    # Split RGB + alpha explicitly so the transparency survives compositing.
    arr = np.array(img)
    rgb, alpha = arr[:, :, :3], arr[:, :, 3] / 255.0
    mask = ImageClip(alpha, ismask=True).set_duration(duration)

    return (
        ImageClip(rgb)
        .set_duration(duration)
        .set_mask(mask)
        .set_position(("right", "bottom"))
        .margin(right=20, bottom=30, opacity=0)
    )


def _ken_burns_clip(image_bytes: bytes, duration: float, index: int):
    """Turns a still image into a slow pan/zoom clip filling FRAME_SIZE.

    Alternates zoom-in / zoom-out per scene for variety. Pure CPU (moviepy +
    Pillow), so it needs no GPU — this is what replaces Stable Video Diffusion.
    """
    import io
    from PIL import Image
    from moviepy.editor import ImageClip

    fw, fh = FRAME_SIZE

    # Cover the frame (center-crop to 9:16) so there are no black bars.
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    scale = max(fw / img.width, fh / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)))
    left, top = (img.width - fw) // 2, (img.height - fh) // 2
    img = img.crop((left, top, left + fw, top + fh))

    import numpy as np
    base = ImageClip(np.array(img)).set_duration(duration)

    # Slow zoom: in on even scenes, out on odd. resize() grows the clip while a
    # fixed-size composite crops it, producing the Ken Burns motion.
    zoom = 0.10
    if index % 2 == 0:
        resizer = lambda t: 1 + zoom * (t / duration)          # 1.00 -> 1.10
    else:
        resizer = lambda t: 1 + zoom * (1 - t / duration)      # 1.10 -> 1.00

    from moviepy.editor import CompositeVideoClip
    moving = base.resize(resizer).set_position(("center", "center"))
    return CompositeVideoClip([moving], size=FRAME_SIZE).set_duration(duration)


@app.function(secrets=[modal.Secret.from_name("r2-credentials")], timeout=900)
def assemble_and_upload(image_blobs: list, audio_blobs: list, output_filename: str, object_name: str) -> str:
    """Builds the episode from still images + per-scene voiceovers using Ken Burns
    motion, enforces the 63-second rule, burns the watermark, and uploads to R2 —
    all in one CPU container (no GPU).

    Each scene's image is shown for exactly the length of its voiceover, so audio
    and visuals stay in sync. Takes raw png/mp3 bytes (not paths).
    """
    import boto3
    from moviepy.editor import (
        AudioFileClip, ImageClip, concatenate_videoclips, CompositeVideoClip
    )

    # Build one Ken Burns clip per scene, each carrying its own voiceover.
    scene_clips = []
    for i, (img_blob, audio_blob) in enumerate(zip(image_blobs, audio_blobs)):
        audio_path = f"/tmp/scene_{i}_audio.mp3"
        with open(audio_path, "wb") as f:
            f.write(audio_blob)
        audio = AudioFileClip(audio_path)
        clip = _ken_burns_clip(img_blob, audio.duration, i).set_audio(audio)
        scene_clips.append(clip)

    final_video = concatenate_videoclips(scene_clips, method="compose")

    # MONETIZATION ENFORCEMENT: Must be >= 63 seconds. If the voiceovers total
    # less, hold the last frame (silent) to reach 63s.
    if final_video.duration < 63.0:
        print(f"Padding video: {final_video.duration}s -> 63.0s")
        freeze = ImageClip(final_video.get_frame(final_video.duration - 0.04))
        freeze = freeze.set_duration(63.0 - final_video.duration)
        final_video = concatenate_videoclips([final_video, freeze], method="compose")

    final_duration = final_video.duration

    # Render the watermark with Pillow (not MoviePy's TextClip, which shells out
    # to ImageMagick — blocked by Debian's default security policy.xml).
    watermark_text = os.environ.get("WATERMARK_TEXT", "@YourBrand")
    watermark_clip = _make_watermark(watermark_text, final_duration)
    final_video = CompositeVideoClip([final_video, watermark_clip])

    final_path = f"/tmp/{output_filename}"
    final_video.write_videofile(final_path, codec="libx264", audio_codec="aac", fps=24, preset="fast", logger=None)

    # Upload straight to R2 from this same container.
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"]
    )
    s3.upload_file(final_path, os.environ["R2_BUCKET_NAME"], object_name)

    public_base_url = os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")
    return f"{public_base_url}/{object_name}"


@app.function(secrets=[modal.Secret.from_name("r2-credentials")])
def update_episode_manifest(video_url: str, series_title: str, title: str, episode_number: int, language: str) -> None:
    """Appends this episode to a public manifest.json in R2.

    This is the only link between the private pipeline and the public showcase
    site: the showcase repo has no code or secrets of its own for this project,
    it just fetches manifest.json client-side and renders whatever is in it.
    Requires CORS enabled on the R2 bucket for GET from the Pages origin.
    """
    import json as _json
    import datetime
    import boto3
    import botocore

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"]
    )
    bucket_name = os.environ["R2_BUCKET_NAME"]

    try:
        existing = s3.get_object(Bucket=bucket_name, Key="manifest.json")
        manifest = _json.loads(existing["Body"].read())
    except botocore.exceptions.ClientError:
        manifest = {"episodes": []}

    manifest["episodes"].insert(0, {
        "series_title": series_title,
        "title": title,
        "episode_number": episode_number,
        "series_length": SERIES_LENGTH,
        "language": language,
        "video_url": video_url,
        "published_at": datetime.datetime.utcnow().isoformat() + "Z",
    })

    s3.put_object(
        Bucket=bucket_name,
        Key="manifest.json",
        Body=_json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

# ==========================================
# 7. SOCIAL MEDIA WEBHOOK
# ==========================================

@app.function(secrets=[modal.Secret.from_name("social-api-secret")])
def publish_to_socials(video_url: str, series_title: str, title: str, episode_number: int, language: str):
    """Sends the hosted video URL to Upload-Post/Zernio for scheduled publishing."""
    import requests

    api_key = os.environ["SOCIAL_API_KEY"]
    webhook_url = "https://api.upload-post.com/v1/publish"

    hook = (
        f"🔚 SERIES FINALE — a brand-new series starts tomorrow!"
        if episode_number >= SERIES_LENGTH
        else f"⏳ Episode {episode_number + 1} drops tomorrow — follow so you don't miss it!"
    )

    payload = {
        "videoUrl": video_url,
        "caption": f"{series_title} | {title} (Ep {episode_number}/{SERIES_LENGTH}) 😱🍿 {hook} #drama #story",
        "platforms": ["tiktok", "youtube_shorts", "facebook_reels"],
        "channel_group": language
    }

    response = requests.post(webhook_url, json=payload, headers={"Authorization": f"Bearer {api_key}"})
    return response.json()

# ==========================================
# MASTER ORCHESTRATOR
# ==========================================

def _advance_series_state(language: str) -> dict:
    """Reads this language's persisted series state and advances it by one episode.

    Each language runs its own independent series clock, keyed in `series_state`.
    """
    state = series_state.get(language, {})
    episode_number = state.get("episode_number", 0) + 1

    if episode_number > SERIES_LENGTH:
        # Previous series just finished its finale — start a brand-new one, cast reset.
        episode_number = 1
        state = {}

    return {
        "episode_number": episode_number,
        "characters": state.get("characters", ""),
        "plot_summary": state.get("plot_summary", ""),
    }


def _save_series_state(language: str, episode_number: int, script_data: dict) -> None:
    series_state[language] = {
        "episode_number": episode_number,
        "characters": script_data.get("characters", ""),
        "plot_summary": script_data.get("recap", ""),
        "series_title": script_data.get("series_title", ""),
    }


def run_full_pipeline(topic_seed: str, language: str, publish: bool = True) -> dict:
    """Shared orchestration logic used by both the CLI entrypoint and the HTTP trigger below.

    When `publish` is False, everything runs (script -> voice -> photos -> video ->
    R2 -> manifest) except the social post — useful for smoke tests before an
    Upload-Post account exists, since it needs no `social-api-secret`.
    """
    context = _advance_series_state(language)
    episode_number = context["episode_number"]
    print(f"🚀 Starting Drama Pipeline | Lang: {language} | Episode {episode_number}/{SERIES_LENGTH}")

    # 1. Generate Script (continuing the series, or inventing a new one on episode 1)
    script_data = generate_script.remote(
        language, topic_seed, episode_number, context["characters"], context["plot_summary"]
    )
    series_title = script_data.get("series_title", script_data["title"])
    print(f"✅ Script generated: {series_title} — {script_data['title']} (Ep {episode_number}/{SERIES_LENGTH})")

    _save_series_state(language, episode_number, script_data)

    # 2. Parallel Generation (images and audio fan out concurrently).
    # Each step returns raw bytes (not paths), since Modal functions have isolated filesystems.
    visual_prompts = [scene["visual_prompt"] for scene in script_data["scenes"]]
    voiceovers = [scene["voiceover"] for scene in script_data["scenes"]]
    indices = list(range(len(visual_prompts)))

    print("🖼️ Generating scene stills via Replicate (FLUX.1-schnell)...")
    scene_images = list(generate_scene_image.map(visual_prompts, indices))

    # Render audio in parallel via ElevenLabs
    print("⏳ Rendering voiceovers via ElevenLabs...")
    audio_blobs = list(generate_voiceover.map(voiceovers, [language] * len(voiceovers), indices))

    # 3. Assemble with Ken Burns motion, watermark & upload (single CPU container)
    print("🎬 Assembling 63-second final cut (Ken Burns) and uploading to R2...")
    episode_id = str(uuid.uuid4())[:8]
    output_filename = f"episode_{language}_{episode_id}.mp4"
    object_name = f"renders/{output_filename}"
    cloud_url = assemble_and_upload.remote(scene_images, audio_blobs, output_filename, object_name)
    print(f"✅ Video secured in cloud: {cloud_url}")

    update_episode_manifest.remote(cloud_url, series_title, script_data["title"], episode_number, language)

    # 5. Publish (skipped for smoke tests / when no social account is configured yet)
    if publish:
        print("📱 Triggering Social Media APIs...")
        publish_status = publish_to_socials.remote(cloud_url, series_title, script_data["title"], episode_number, language)
        print(f"🎉 Pipeline Complete! Social API Response: {publish_status}")
    else:
        publish_status = "skipped"
        print(f"🎬 Pipeline Complete (publishing skipped). Video: {cloud_url}")

    return {
        "series_title": series_title,
        "title": script_data["title"],
        "episode_number": episode_number,
        "video_url": cloud_url,
        "publish_status": publish_status,
    }


@app.local_entrypoint()
def run_pipeline(topic_seed: str = "A hidden heir crashes a billionaire's wedding", language: str = "en", publish: bool = True):
    """
    Triggers today's episode locally — continuing the current series, or starting a
    brand-new one (with `topic_seed` as loose inspiration) if the last series just
    finished its finale. Run via:
    modal run modal_drama_generator.py --topic-seed "Your Plot" --language "ar"

    For a smoke test with no Upload-Post account, skip the social post:
    modal run modal_drama_generator.py --no-publish
    """
    run_full_pipeline(topic_seed, language, publish)


@app.function(timeout=1200)
@modal.fastapi_endpoint(method="POST")
def trigger_pipeline(data: dict):
    """
    HTTP-triggered entrypoint for remote/cron-based runs (replaces the old
    standalone app.py). Deploy with `modal deploy modal_drama_generator.py`
    and point .github/workflows/daily_render.yaml's MODAL_WEBHOOK_URL secret
    at the resulting URL for this function.

    `prompt` is only used as loose inspiration when a new series is starting
    (episode 1) — every other day it's ignored and the ongoing series continues
    automatically via the persisted state in `series_state`.
    """
    topic_seed = data.get("prompt", "A hidden heir crashes a billionaire's wedding")
    language = data.get("language", "en")
    publish = data.get("publish", True)  # POST {"publish": false} to render without posting
    result = run_full_pipeline(topic_seed, language, publish)
    return {"status": "success", **result}