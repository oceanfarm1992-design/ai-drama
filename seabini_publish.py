"""Publishes a finished SEABINI episode:
uploads the video as a GitHub Release asset in ai-drama-showcase (public URL),
updates manifest.json in ai-drama-showcase via GitHub API, and posts to
YouTube / TikTok / Facebook via Buffer's GraphQL API.

Env: GH_TOKEN (SHOWCASE_TOKEN from workflow secrets), BUFFER_API_KEY.
Run: python seabini_publish.py <video.mp4> "<title>" ["<lang>"] ["<objective>"]
"""
import base64, os, sys, json, datetime, pathlib, subprocess, ssl, urllib.request

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

SERIES_TITLE = "SEABINI"
REPO = os.environ.get("GITHUB_REPOSITORY", "oceanfarm1992-design/ai-drama-showcase")

# Channel IDs confirmed 2026-09-08 via buffer_channels.py
_BUFFER_ORG_ID = "6aa0477126e41236abff5ad7"
_BUFFER_CHANNELS = {
    "youtube":  "6aa047f4cd8b9c702c2e9786",
    "tiktok":   "6aa048c7cd8b9c702c2e9b3b",
    "facebook": "6aa047c5cd8b9c702c2e96ac",
}

_CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status } }
    ... on InvalidInputError { message }
    ... on UnauthorizedError { message }
    ... on UnexpectedError { message }
    ... on NotFoundError { message }
    ... on RestProxyError { message }
    ... on LimitReachedError { message }
  }
}
"""


def _run(cmd):
    subprocess.run(cmd, check=True)


def _gh_token():
    raw = os.environ.get("GH_TOKEN") or ""
    return raw.strip().lstrip("﻿")


def _buffer_key():
    k = os.environ.get("BUFFER_API_KEY")
    if k:
        return k
    apis = pathlib.Path(__file__).with_name(".APIs.txt")
    if not apis.exists():
        apis = pathlib.Path("C:/Calude Apps/AI Drama/.APIs.txt")
    if apis.exists():
        for line in apis.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("buffer-secret="):
                return line.split("=", 1)[1].strip()
    return None


def _buffer_gql(query, variables):
    key = _buffer_key()
    if not key:
        print("[Buffer] No API key — skipping social post.")
        return None
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.buffer.com/graphql",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, context=_SSL_CTX, timeout=30).read())
    except Exception as exc:
        print(f"[Buffer] GraphQL error: {exc}")
        return None


_YT_TAGS = [
    "SEABINI", "ocean for kids", "preschool cartoon", "sea animals for kids",
    "kids ocean show", "underwater cartoon", "toddler learning", "preschool learning",
    "kids educational video", "ocean adventure", "seahorse cartoon", "kids cartoon",
    "baby shark alternative", "ocean for toddlers", "animated kids show",
]

_HASHTAGS_TT = (
    "#SEABINI #kidscartoon #cartoonforkids #kidsanimation #fyp"
)
_HASHTAGS_FB = (
    "#SEABINI #KidsShow #Preschool #OceanForKids #ToddlerLearning #KidsCartoon "
    "#ChildrensContent #OceanAdventure #KidsVideos #FamilyFriendly"
)
_HASHTAGS_YT = (
    "#SEABINI #KidsCartoon #OceanForKids #Preschool #ToddlerLearning "
    "#SeaAnimals #KidsShow #Shorts #ChildrensContent #EducationalKids"
)

def _captions(title: str, objective: str) -> dict:
    yt_desc = (
        f"🌊 {title} | SEABINI — Adventures Beneath the Blue!\n\n"
        f"Today Bini and friends discover: {objective}\n\n"
        "SEABINI is a preschool ocean cartoon for ages 2–6. Every episode has "
        "an adventure story + a fun sing-and-dance song with Bini! 🐠🦑🐢\n\n"
        "✅ Safe for kids  ✅ AI-assisted  ✅ New episode every day\n\n"
        + _HASHTAGS_YT
    )
    tt_text = (
        f"🌊 {title}! Join Bini the seahorse on an ocean adventure! "
        f"{objective} 🐠 New episode daily! " + _HASHTAGS_TT
    )
    fb_text = (
        f"🌊 New SEABINI episode: {title}!\n\n"
        f"Bini and friends learn: {objective} 🐠\n\n"
        "A brand-new ocean adventure + sing-along song every day! "
        "Perfect for little ones ages 2–6. Share with a parent today! 💙\n\n"
        + _HASHTAGS_FB
    )
    return {"youtube": yt_desc, "tiktok": tt_text, "facebook": fb_text}


def _buffer_post(channel_id: str, service: str, video_url: str,
                 captions: dict, title: str):
    text = captions.get(service, captions["youtube"])
    assets = [{"video": {"url": video_url, "metadata": {"title": title}}}]
    metadata = {}
    if service == "youtube":
        metadata["youtube"] = {
            "title": f"SEABINI | {title} 🌊 | Kids Ocean Cartoon | #Shorts",
            "privacy": "public",
            "categoryId": "1",       # Film & Animation
            "madeForKids": True,
            "isAiGenerated": True,
            "embeddable": True,
            "notifySubscribers": True,
        }
    if service == "tiktok":
        metadata["tiktok"] = {
            "title": f"SEABINI | {title} 🌊",
            "isAiGenerated": True,
        }
    if service == "facebook":
        metadata["facebook"] = {
            "type": "reel",   # vertical short-form video → Facebook Reels
        }
    post_input = {
        "channelId": channel_id,
        "text": text,
        "assets": assets,
        "mode": "shareNow",
        "schedulingType": "automatic",
        "needsApproval": False,
    }
    if metadata:
        post_input["metadata"] = metadata

    resp = _buffer_gql(_CREATE_POST, {"input": post_input})
    if resp:
        errs = resp.get("errors")
        data = (resp.get("data") or {}).get("createPost") or {}
        typename = data.get("__typename", "")
        post = data.get("post")
        err_msg = data.get("message")
        if errs:
            print(f"[Buffer:{service}] GraphQL error: {errs}")
        elif typename == "PostActionSuccess" and post:
            print(f"[Buffer:{service}] Queued → id={post['id']} status={post['status']}")
        elif err_msg:
            print(f"[Buffer:{service}] {typename}: {err_msg}")
        else:
            print(f"[Buffer:{service}] Response: {resp}")


def _post_all_channels(video_url: str, title: str, objective: str):
    caps = _captions(title, objective)
    for service, channel_id in _BUFFER_CHANNELS.items():
        _buffer_post(channel_id, service, video_url, caps, title)


def _gh_api(method, path, body=None, content_type="application/json"):
    """Call the GitHub REST API with the showcase token."""
    token = _gh_token()
    if not token:
        raise RuntimeError("No GH_TOKEN / SHOWCASE_TOKEN available")
    url = f"https://api.github.com{path}" if path.startswith("/") else path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": content_type,
    })
    try:
        return json.loads(urllib.request.urlopen(req, context=_SSL_CTX, timeout=120).read())
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode(errors="replace")
        print(f"[GitHub API] {exc.code} {method} {url}: {err_body[:500]}")
        raise


def _upload_release(video_path: str, title: str) -> str:
    tag = "seabini-" + datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    token = _gh_token()
    print(f"[Release] Creating {tag} in {REPO} (token len={len(token)})")

    # 1. Create the release
    release = _gh_api("POST", f"/repos/{REPO}/releases", {
        "tag_name": tag,
        "name": title,
        "body": "Auto-published by the SEABINI pipeline.",
    })
    upload_url = release["upload_url"].split("{")[0]  # strip {?name,label} template
    print(f"[Release] Created: {release['html_url']}")

    # 2. Upload the video asset
    filename = pathlib.Path(video_path).name
    file_data = pathlib.Path(video_path).read_bytes()
    asset_url = f"{upload_url}?name={filename}"
    req = urllib.request.Request(asset_url, data=file_data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/octet-stream",
    })
    try:
        asset = json.loads(urllib.request.urlopen(req, context=_SSL_CTX, timeout=300).read())
        print(f"[Release] Uploaded asset: {asset['name']} ({asset['size']} bytes)")
    except urllib.error.HTTPError as exc:
        print(f"[Release] Asset upload failed: {exc.code} {exc.read().decode(errors='replace')[:300]}")

    return f"https://github.com/{REPO}/releases/download/{tag}/{filename}"


def _get_manifest():
    """Fetch manifest.json from ai-drama-showcase via GitHub API. Returns (dict, sha)."""
    token = _gh_token()
    if not token:
        return {"episodes": []}, None
    api_url = f"https://api.github.com/repos/{REPO}/contents/manifest.json"
    req = urllib.request.Request(api_url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    })
    try:
        resp = json.loads(urllib.request.urlopen(req, context=_SSL_CTX, timeout=15).read())
        content = base64.b64decode(resp["content"]).decode("utf-8")
        return json.loads(content), resp["sha"]
    except Exception as exc:
        print(f"[Manifest] Could not fetch existing manifest ({exc}), starting fresh.")
        return {"episodes": []}, None


def _put_manifest(manifest_data: dict, title: str, sha=None):
    """Commit updated manifest.json to ai-drama-showcase via GitHub API."""
    token = _gh_token()
    if not token:
        print("[Manifest] No token — skipping manifest update.")
        return
    api_url = f"https://api.github.com/repos/{REPO}/contents/manifest.json"
    content_b64 = base64.b64encode(
        json.dumps(manifest_data, ensure_ascii=False, indent=2).encode()
    ).decode()
    body = {
        "message": f"Publish episode: {title}",
        "content": content_b64,
        "committer": {"name": "seabini-bot", "email": "seabini-bot@users.noreply.github.com"},
    }
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(api_url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
        },
        method="PUT",
    )
    try:
        urllib.request.urlopen(req, context=_SSL_CTX, timeout=15)
        print(f"[Manifest] Updated in {REPO}")
    except Exception as exc:
        print(f"[Manifest] API error: {exc}")


def publish(video_path: str, title: str, language: str = "en",
            objective: str = "a fun ocean discovery") -> str:
    video_url = _upload_release(video_path, title)

    manifest, sha = _get_manifest()
    episode_number = sum(1 for e in manifest["episodes"] if e.get("series_title") == SERIES_TITLE) + 1
    manifest["episodes"].insert(0, {
        "series_title": SERIES_TITLE,
        "title": title,
        "episode_number": episode_number,
        "series_length": episode_number,
        "language": language,
        "learning_objective": objective,
        "video_url": video_url,
        "published_at": datetime.datetime.utcnow().isoformat() + "Z",
    })
    _put_manifest(manifest, title, sha)

    _post_all_channels(video_url, title, objective)

    return video_url


if __name__ == "__main__":
    video = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else "SEABINI Short"
    language = sys.argv[3] if len(sys.argv) > 3 else "en"
    objective = sys.argv[4] if len(sys.argv) > 4 else "a fun ocean discovery"
    print("PUBLISHED:", publish(video, title, language, objective))
