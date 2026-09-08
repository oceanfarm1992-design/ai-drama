"""Publishes a finished SEABINI episode as a GitHub Release asset on the public
ai-drama-showcase repo, and updates the manifest.json the showcase site reads
from its own GitHub Pages (same-origin fetch — no R2/S3 needed).

Env vars required: SHOWCASE_TOKEN (a GitHub PAT with contents+releases write
access to the showcase repo — the default Actions token only covers this repo).
Optional: SHOWCASE_REPO (default "oceanfarm1992-design/ai-drama-showcase").

Run: python seabini_publish.py <video.mp4> "<episode title>" ["<language>"]
"""
import os, sys, json, base64, datetime, pathlib, subprocess, urllib.request, urllib.error

SERIES_TITLE = "SEABINI"
SHOWCASE_REPO = os.environ.get("SHOWCASE_REPO", "oceanfarm1992-design/ai-drama-showcase")
API = f"https://api.github.com/repos/{SHOWCASE_REPO}"


def _token():
    return os.environ["SHOWCASE_TOKEN"]


def _api(method, path, body=None):
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {_token()}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _upload_release(video_path: str, title: str) -> str:
    tag = "seabini-" + datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    env = {**os.environ, "GH_TOKEN": _token()}
    subprocess.run(["gh", "release", "create", tag, video_path,
                     "--repo", SHOWCASE_REPO, "--title", title,
                     "--notes", "Auto-published by the SEABINI pipeline."],
                    check=True, env=env)
    filename = pathlib.Path(video_path).name
    return f"https://github.com/{SHOWCASE_REPO}/releases/download/{tag}/{filename}"


def publish(video_path: str, title: str, language: str = "en") -> str:
    video_url = _upload_release(video_path, title)

    existing = _api("GET", "/contents/manifest.json")
    if existing:
        manifest = json.loads(base64.b64decode(existing["content"]))
        sha = existing["sha"]
    else:
        manifest, sha = {"episodes": []}, None

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
    body = {
        "message": f"Publish episode: {title}",
        "content": base64.b64encode(json.dumps(manifest, ensure_ascii=False, indent=2).encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    _api("PUT", "/contents/manifest.json", body)
    return video_url


if __name__ == "__main__":
    video = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else "SEABINI Short"
    language = sys.argv[3] if len(sys.argv) > 3 else "en"
    url = publish(video, title, language)
    print("PUBLISHED:", url)
