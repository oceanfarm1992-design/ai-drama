"""Fetches real stock video footage for the "Bini's Real Ocean" series —
Bini narrating over genuine nature footage instead of AI-generated scenes.

Pexels is tried first (often has native vertical 9:16 clips, no cropping
needed); Pixabay is the fallback (landscape only, always needs a vertical
crop). Both are free for commercial use with no attribution required.
"""
import json, pathlib, random, ssl, subprocess, urllib.parse, urllib.request

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()

HERE = pathlib.Path(__file__).resolve().parent
_MIN_DUR, _MAX_DUR = 6, 40  # seconds — long enough to loop cleanly, short enough to stay small
# Pixabay's CDN (and some Pexels edge cases) 403 requests with Python's default
# urllib User-Agent; a normal browser UA avoids that.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _api_key(name):
    import os
    k = os.environ.get(name)
    if k:
        return k
    apis = HERE / ".APIs.txt"
    if apis.exists():
        prefix = {"PEXELS_API_KEY": "pexels-secret=", "PIXABAY_API_KEY": "pixabay-secret="}[name]
        for line in apis.read_text(encoding="utf-8-sig").splitlines():
            if line.lower().startswith(prefix.lower()):
                return line.split("=", 1)[1].strip()
    return None


def _pexels_search(topic):
    key = _api_key("PEXELS_API_KEY")
    if not key:
        return None
    q = urllib.parse.quote(f"{topic} underwater ocean")
    url = f"https://api.pexels.com/videos/search?query={q}&per_page=40&orientation=portrait"
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": _UA})
    try:
        data = json.loads(urllib.request.urlopen(req, context=_SSL, timeout=20).read())
    except Exception as exc:
        print(f"[footage] Pexels search failed: {exc}")
        return []
    out = []
    for v in data.get("videos", []):
        if not (_MIN_DUR <= v.get("duration", 0) <= _MAX_DUR):
            continue
        # Prefer the file closest to our target 720x1280 without upscaling past it
        files = sorted(v["video_files"], key=lambda f: abs(f.get("width", 0) - 720))
        for f in files:
            if f.get("width", 0) >= 720 and f.get("file_type") == "video/mp4":
                out.append((f"pexels:{v['id']}", f["link"]))
                break
    return out


def _pixabay_search(topic):
    key = _api_key("PIXABAY_API_KEY")
    if not key:
        return None
    q = urllib.parse.quote(f"{topic} underwater ocean")
    url = f"https://pixabay.com/api/videos/?key={key}&q={q}&per_page=40"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        data = json.loads(urllib.request.urlopen(req, context=_SSL, timeout=20).read())
    except Exception as exc:
        print(f"[footage] Pixabay search failed: {exc}")
        return []
    out = []
    for v in data.get("hits", []):
        if not (_MIN_DUR <= v.get("duration", 0) <= _MAX_DUR):
            continue
        videos = v["videos"]
        for tier in ("medium", "large", "small"):
            if tier in videos:
                out.append((f"pixabay:{v['id']}", videos[tier]["url"]))
                break
    return out


def _normalize_vertical(src_path, dst_path, ff_exe):
    """Cover-fit crop to exactly 720x1280, whatever the source aspect ratio."""
    subprocess.run([
        ff_exe, "-y", "-i", src_path,
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        "-an", "-r", "24", dst_path,
    ], check=True, capture_output=True)


def _pick(cands, used):
    """Random choice among clips not yet used. If every candidate is already
    used, repeat one rather than return no footage at all."""
    fresh = [c for c in cands if c[0] not in used]
    return random.choice(fresh or cands) if cands else None


def fetch_footage(topic, tag, work_dir, ff_exe, used=None):
    """Returns a local mp4 path, normalized to vertical 720x1280 @24fps, or
    None if no footage could be found on either provider. `used` is a set of
    clip ids already used (this episode + recent ones); the chosen clip's id
    is added to it so callers can carry it across segments."""
    used = used if used is not None else set()
    pick = _pick(_pexels_search(topic), used)
    source = "pexels"
    if not pick:
        pick = _pick(_pixabay_search(topic), used)
        source = "pixabay"
    if not pick:
        print(f"[footage] No results for '{topic}' on Pexels or Pixabay")
        return None
    clip_id, url = pick
    used.add(clip_id)
    raw_path = str(work_dir / f"{tag}_raw.mp4")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    data = urllib.request.urlopen(req, context=_SSL, timeout=60).read()
    pathlib.Path(raw_path).write_bytes(data)
    out_path = str(work_dir / f"{tag}_vertical.mp4")
    _normalize_vertical(raw_path, out_path, ff_exe)
    print(f"[footage] {source}: {topic} -> {out_path}")
    return out_path
