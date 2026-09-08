"""Uploads a finished SEABINI episode to R2 and appends it to the public
manifest.json the showcase site reads (same schema as the old drama pipeline,
so oceanfarm1992-design/ai-drama-showcase keeps working unmodified).

Env vars required: R2_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
R2_BUCKET_NAME, R2_PUBLIC_BASE_URL.

Run: python seabini_publish.py <video.mp4> "<episode title>" ["<language>"]
"""
import os, sys, json, datetime, pathlib
import boto3
import botocore

SERIES_TITLE = "SEABINI"


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )


def publish(video_path: str, title: str, language: str = "en") -> str:
    bucket = os.environ["R2_BUCKET_NAME"]
    base = os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")
    s3 = _client()

    object_name = f"renders/seabini_{pathlib.Path(video_path).stem}.mp4"
    s3.upload_file(video_path, bucket, object_name)
    video_url = f"{base}/{object_name}"

    try:
        existing = s3.get_object(Bucket=bucket, Key="manifest.json")
        manifest = json.loads(existing["Body"].read())
    except botocore.exceptions.ClientError:
        manifest = {"episodes": []}

    episode_number = sum(1 for e in manifest["episodes"] if e.get("series_title") == SERIES_TITLE) + 1
    manifest["episodes"].insert(0, {
        "series_title": SERIES_TITLE,
        "title": title,
        "episode_number": episode_number,
        "series_length": episode_number,  # SEABINI is an open-ended show, not a fixed-length series
        "language": language,
        "video_url": video_url,
        "published_at": datetime.datetime.utcnow().isoformat() + "Z",
    })
    s3.put_object(Bucket=bucket, Key="manifest.json",
                   Body=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                   ContentType="application/json")
    return video_url


if __name__ == "__main__":
    video = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else "SEABINI Short"
    language = sys.argv[3] if len(sys.argv) > 3 else "en"
    url = publish(video, title, language)
    print("PUBLISHED:", url)
