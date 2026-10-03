"""Fetches real stock video footage for the "Bini's Real Ocean" series.

Candidates come from Pexels (often native vertical 9:16) and Pixabay, both
free for commercial use with no attribution required. Three filters decide
which clip is used:
  1. relevance - clips whose Pexels page slug / Pixabay tags name the
     subject creature are tried first (stock search is fuzzy: a "walrus"
     query happily returns generic ocean footage);
  2. novelty - clip ids already used recently are skipped;
  3. a vision check on 3 frames - the creature must actually be visible
     and nothing may be scary for a 2-6 year old (this is real footage, so
     the script's safety rules can't guarantee what the camera shows).
"""
import base64, json, os, pathlib, random, re, ssl, subprocess, urllib.parse, urllib.request

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()

HERE = pathlib.Path(__file__).resolve().parent
_MIN_DUR, _MAX_DUR = 6, 40  # seconds — long enough to loop cleanly, short enough to stay small
_MAX_TRIES = 4              # candidate clips downloaded + vision-checked per segment
_STOP = {"sea", "of", "the", "school", "moon", "a"}
# Pixabay's CDN (and some Pexels edge cases) 403 requests with Python's default
# urllib User-Agent; a normal browser UA avoids that.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _api_key(name):
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


def _get_json(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **headers})
    return json.loads(urllib.request.urlopen(req, context=_SSL, timeout=20).read())


def _pexels_search(query):
    """[(clip_id, download_url, descriptive_text, duration)]"""
    key = _api_key("PEXELS_API_KEY")
    if not key:
        return []
    url = f"https://api.pexels.com/videos/search?query={urllib.parse.quote(query)}&per_page=40&orientation=portrait"
    try:
        data = _get_json(url, {"Authorization": key})
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
                out.append((f"pexels:{v['id']}", f["link"], v.get("url", ""), v["duration"]))
                break
    return out


def _pixabay_search(query):
    """[(clip_id, download_url, descriptive_text, duration)]"""
    key = _api_key("PIXABAY_API_KEY")
    if not key:
        return []
    url = f"https://pixabay.com/api/videos/?key={key}&q={urllib.parse.quote(query)}&per_page=40"
    try:
        data = _get_json(url, {})
    except Exception as exc:
        print(f"[footage] Pixabay search failed: {exc}")
        return []
    out = []
    for v in data.get("hits", []):
        if not (_MIN_DUR <= v.get("duration", 0) <= _MAX_DUR):
            continue
        for tier in ("medium", "large", "small"):
            if tier in v["videos"]:
                out.append((f"pixabay:{v['id']}", v["videos"][tier]["url"], v.get("tags", ""), v["duration"]))
                break
    return out


def _mentions(text, subject):
    """Does a clip's slug/tags name the subject? Letters-only matching so
    'clown-fish' matches 'clownfish' and 'sea-turtle' matches 'sea turtle'."""
    norm = re.sub(r"[^a-z]", "", text.lower())
    words = [w for w in subject.lower().split() if w not in _STOP] or [subject.replace(" ", "")]
    # All words must appear: "eagle ray" must not match any clip saying "gray",
    # "mandarin fish" must not match every clip that merely says "fish".
    return all(re.sub(r"[^a-z]", "", w) in norm for w in words)


def _normalize_vertical(src_path, dst_path, ff_exe):
    """Cover-fit crop to exactly 720x1280, whatever the source aspect ratio."""
    subprocess.run([
        ff_exe, "-y", "-i", src_path,
        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
        "-an", "-r", "24", dst_path,
    ], check=True, capture_output=True)


def _vision_check(video_path, duration, subject, ff_exe):
    """(subject_visible, kid_safe) from 3 frames. Best-effort: if the check
    itself can't run (no key, network), the clip is accepted as before."""
    try:
        import seabini_script as ss
        key = ss._openai_key()
    except BaseException:
        return True, True
    images = []
    for frac in (0.2, 0.5, 0.8):
        jpg = f"{video_path}.{int(frac * 100)}.jpg"
        subprocess.run([ff_exe, "-y", "-ss", f"{duration * frac:.2f}", "-i", video_path,
                        "-vframes", "1", "-vf", "scale=256:-2", jpg], capture_output=True)
        if os.path.exists(jpg):
            b64 = base64.b64encode(pathlib.Path(jpg).read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})
    if not images:
        return True, True
    prompt = (f"These are 3 frames from a stock clip for a preschool (ages 2-6) video about a {subject}. "
              'Reply with JSON only: {"subject_visible": true/false, "kid_safe": true/false}. '
              f"subject_visible = a {subject} is clearly visible in at least one frame. "
              "kid_safe = false if anything could scare or upset a small child: blood, injury, a dead "
              "or eaten animal, hunting/attacking, fishing hooks or nets with caught animals, trash-choked "
              "animals, or anything violent. Divers, boats and ordinary ocean scenes are fine.")
    body = {"model": "gpt-4o-mini", "response_format": {"type": "json_object"}, "temperature": 0,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, *images]}]}
    try:
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        verdict = json.loads(json.loads(urllib.request.urlopen(req, context=_SSL, timeout=60).read())
                             ["choices"][0]["message"]["content"])
        return bool(verdict.get("subject_visible")), bool(verdict.get("kid_safe"))
    except Exception as exc:
        print(f"[footage] vision check unavailable ({exc}), accepting clip")
        return True, True


def _download(url, raw_path, out_path, ff_exe):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    pathlib.Path(raw_path).write_bytes(urllib.request.urlopen(req, context=_SSL, timeout=60).read())
    _normalize_vertical(raw_path, out_path, ff_exe)


def fetch_footage(query, tag, work_dir, ff_exe, used=None, subject=None):
    """Returns a local mp4 path, normalized to vertical 720x1280 @24fps, or
    None if no suitable footage exists. `used` is a set of clip ids already
    used (this episode + recent ones); the chosen clip's id is added to it.
    `subject` is the creature the footage must actually show."""
    used = used if used is not None else set()
    subject = subject or query
    cands = _pexels_search(query) + _pixabay_search(query)
    if len(cands) < 5:  # very specific phrasing found little; broaden once
        cands += _pexels_search(f"{subject} underwater") + _pixabay_search(f"{subject} underwater")
    seen, pool = set(), []
    for c in cands:
        if c[0] not in seen:
            seen.add(c[0]); pool.append(c)
    fresh = [c for c in pool if c[0] not in used] or pool
    random.shuffle(fresh)
    fresh.sort(key=lambda c: not _mentions(c[2], subject))  # stable: relevant first, random within
    if not fresh:
        print(f"[footage] No results for '{query}' on Pexels or Pixabay")
        return None

    fallback = None
    for n, (clip_id, url, _text, duration) in enumerate(fresh[:_MAX_TRIES]):
        raw_path = str(work_dir / f"{tag}_raw{n}.mp4")
        out_path = str(work_dir / f"{tag}_vertical{n}.mp4")
        try:
            _download(url, raw_path, out_path, ff_exe)
        except Exception as exc:
            print(f"[footage] download failed for {clip_id}: {exc}")
            continue
        visible, safe = _vision_check(out_path, duration, subject, ff_exe)
        if not safe:
            print(f"[footage] rejected {clip_id}: not kid-safe")
            continue
        if visible:
            used.add(clip_id)
            print(f"[footage] {clip_id}: '{query}' -> {out_path}")
            return out_path
        print(f"[footage] rejected {clip_id}: no {subject} visible")
        fallback = fallback or (clip_id, out_path)
    if fallback:  # safe but the creature wasn't clearly visible - better than nothing
        used.add(fallback[0])
        print(f"[footage] {fallback[0]}: using best available (subject unclear)")
        return fallback[1]
    return None
