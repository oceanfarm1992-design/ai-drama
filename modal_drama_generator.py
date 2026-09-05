"""
SKILL FILE: Serverless AI Drama Generator Pipeline
Platform: Modal (Serverless GPU) + hosted APIs, chosen per stage for cost/quality:
  - Script:  OpenAI gpt-4o-mini (API)       - cheap, reliable structured JSON at this volume
  - Voice:   ElevenLabs eleven_multilingual_v2 (API) - realistic, covers 31 languages incl. Tamil
  - Photos:  Replicate black-forest-labs/flux-schnell (API) - cheap, realistic stills
  - Video:   Stable Video Diffusion img2vid-xt (self-hosted Modal A100) - the one stage
             where self-hosting is meaningfully cheaper than any comparable video API
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

# Create a persistent volume to cache heavy model weights so they don't redownload every run.
model_volume = modal.Volume.from_name("drama-models-cache", create_if_missing=True)

# Persists each language's ongoing series: episode number, cast bios, and running
# plot recap, so consecutive daily runs continue the same story instead of each
# being a standalone one-off.
series_state = modal.Dict.from_name("drama-series-state", create_if_missing=True)

SERIES_LENGTH = 30  # episodes per series before a new cast/premise takes over

# Define the container image with all necessary system packages and Python libraries.
app_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "wget", "imagemagick")
    .pip_install(
        "torch==2.5.1",
        "moviepy==1.0.3",
        "openai",
        "elevenlabs",
        "replicate",
        "boto3",
        "requests",
        "diffusers==0.30.0",
        "transformers==4.41.2",
        "accelerate==0.34.2",
        "imageio",
        "imageio-ffmpeg",
        "pillow"
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
def generate_voiceover(text: str, language: str, index: int) -> str:
    """Generates localized voiceover audio for a specific scene via ElevenLabs.

    eleven_multilingual_v2 covers all 31 of its supported languages (including
    Tamil) through one API/voice, so no per-language branching is needed here.
    """
    from elevenlabs.client import ElevenLabs

    output_path = f"/tmp/scene_{index}_audio.mp3"

    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # "Rachel", a default premade voice

    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        model_id="eleven_multilingual_v2",
        text=text,
        language_code=language,
    )

    with open(output_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)

    return output_path

# ==========================================
# 4. VISUALS: STILL IMAGE (API) -> ANIMATED VIDEO (self-hosted)
# ==========================================

@app.function(secrets=[modal.Secret.from_name("replicate-secret")], timeout=120)
def generate_scene_image(visual_prompt: str, index: int) -> bytes:
    """Generates a realistic vertical still image for a scene via Replicate's hosted FLUX.1-schnell.

    Cheap and high-quality relative to self-hosting an image model on Modal at
    this pipeline's volume, since a hosted API avoids per-container cold starts.
    """
    import replicate

    output = replicate.run(
        "black-forest-labs/flux-schnell",
        input={"prompt": visual_prompt, "aspect_ratio": "9:16", "output_format": "png"},
    )
    return output[0].read()


@app.function(gpu="A100", volumes={"/models": model_volume}, timeout=600)
def animate_scene_image(image_bytes: bytes, index: int) -> str:
    """Animates a still image into a ~4-second vertical video clip via Stable Video Diffusion.

    This is the one stage kept self-hosted: video-generation APIs (Kling, Runway)
    run 5-25x more expensive per episode than running SVD on Modal's A100s.
    """
    import io
    import torch
    from PIL import Image
    from diffusers import StableVideoDiffusionPipeline
    from diffusers.utils import export_to_video

    output_path = f"/tmp/scene_{index}_video.mp4"

    pipe = StableVideoDiffusionPipeline.from_pretrained(
        "stabilityai/stable-video-diffusion-img2vid-xt",
        torch_dtype=torch.float16,
        variant="fp16",
        cache_dir="/models",
    )
    pipe.to("cuda")
    pipe.enable_model_cpu_offload()

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((576, 1024))

    # SVD-XT generates a fixed 25 frames; exporting at 5fps yields ~5s/scene
    # to match the target ~5.25s-per-scene pacing from a 63s, 12-scene episode.
    frames = pipe(image, height=1024, width=576, decode_chunk_size=8, motion_bucket_id=127).frames[0]
    export_to_video(frames, output_path, fps=5)
    model_volume.commit()

    return output_path

# ==========================================
# 5. ASSEMBLY & WATERMARKING
# ==========================================

@app.function(timeout=600)
def assemble_final_video(video_paths: list, audio_paths: list, output_filename: str) -> str:
    """Merges audio/video, enforces the 63-second rule, and burns the watermark."""
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips,
        concatenate_audioclips, CompositeVideoClip, TextClip
    )

    # Load and concatenate
    v_clips = [VideoFileClip(p) for p in video_paths]
    a_clips = [AudioFileClip(p) for p in audio_paths]

    final_video = concatenate_videoclips(v_clips, method="compose")
    final_audio = concatenate_audioclips(a_clips)

    # MONETIZATION ENFORCEMENT: Must be >= 63 seconds
    final_duration = max(63.0, final_audio.duration)
    if final_video.duration < final_duration:
        # SVD's fixed 25-frame clips rarely land on an exact total, so freeze
        # the last frame to fill any gap instead of leaving trailing silence/black.
        print(f"Padding video: {final_video.duration}s -> {final_duration}s")
        freeze_frame = ImageClip(final_video.get_frame(final_video.duration - 0.04))
        freeze_frame = freeze_frame.set_duration(final_duration - final_video.duration)
        final_video = concatenate_videoclips([final_video, freeze_frame], method="compose")

    final_video = final_video.set_audio(final_audio).set_duration(final_duration)

    watermark_text = os.environ.get("WATERMARK_TEXT", "@YourBrand")
    watermark = (
        TextClip(watermark_text, fontsize=28, color="white", font="Arial-Bold",
                 stroke_color="black", stroke_width=1)
        .set_opacity(0.75)
        .set_duration(final_duration)
        .margin(right=20, bottom=30, opacity=0)
        .set_position(("right", "bottom"))
    )

    final_video = CompositeVideoClip([final_video, watermark])

    final_path = f"/tmp/{output_filename}"
    final_video.write_videofile(final_path, codec="libx264", audio_codec="aac", fps=24, preset="fast", logger=None)

    return final_path

# ==========================================
# 6. CLOUD STORAGE UPLOAD
# ==========================================

@app.function(secrets=[modal.Secret.from_name("r2-credentials")])
def upload_to_r2(local_file_path: str, object_name: str) -> str:
    """Streams the final MP4 to Cloudflare R2 / AWS S3."""
    import boto3
    
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"]
    )

    bucket_name = os.environ["R2_BUCKET_NAME"]
    s3.upload_file(local_file_path, bucket_name, object_name)

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

    # 2. Parallel Generation (images, video animation, and audio all fan out concurrently)
    visual_prompts = [scene["visual_prompt"] for scene in script_data["scenes"]]
    voiceovers = [scene["voiceover"] for scene in script_data["scenes"]]
    indices = list(range(len(visual_prompts)))

    # Generate stills via Replicate, then animate each into a clip on Modal's A100s
    print("🖼️ Generating scene stills via Replicate (FLUX.1-schnell)...")
    scene_images = list(generate_scene_image.map(visual_prompts, indices))

    print("⏳ Animating scenes into video on A100 GPUs (Stable Video Diffusion)...")
    video_paths = list(animate_scene_image.map(scene_images, indices))

    # Render audio in parallel via ElevenLabs
    print("⏳ Rendering voiceovers via ElevenLabs...")
    audio_paths = list(generate_voiceover.map(voiceovers, [language] * len(voiceovers), indices))

    # 3. Assemble & Watermark
    print("🎬 Assembling 63-second final cut...")
    episode_id = str(uuid.uuid4())[:8]
    output_filename = f"episode_{language}_{episode_id}.mp4"
    final_video_path = assemble_final_video.remote(video_paths, audio_paths, output_filename)

    # 4. Upload to Cloud
    print("☁️ Uploading to Cloudflare R2...")
    cloud_url = upload_to_r2.remote(final_video_path, f"renders/{output_filename}")
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
@modal.web_endpoint(method="POST")
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