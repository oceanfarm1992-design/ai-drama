"""Fetches real stock video footage for the "Bini's Real Ocean" series.

Candidates come from Pexels (often native vertical 9:16) and Pixabay, both
free for commercial use with no attribution required. Three filters decide
which clip is used:
  1. relevance - clips whose Pexels page slug / Pixabay tags name the
     subject creature are tried first (stock search is fuzzy: a "walrus"
     query happily returns generic ocean footage);
  2. novelty - clip ids used by any earlier episode are skipped;
  3. a vision check on 3 frames - the creature must actually be visible
     and nothing may be scary for a 2-6 year old (this is real footage, so
     the script's safety rules can't guarantee what the camera shows).
A clip that fails the check is never used; the segment is dropped instead.
"""
import base64, json, os, pathlib, random, re, ssl, subprocess, urllib.parse, urllib.request

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()

HERE = pathlib.Path(__file__).resolve().parent
_MIN_DUR, _MAX_DUR = 6, 40  # seconds — long enough to loop cleanly, short enough to stay small
_MAX_TRIES = 3              # candidate clips vision-checked per search (up to 3 searches)
_STOP = {"sea", "of", "the", "school", "moon", "a"}
# Pixabay's CDN (and some Pexels edge cases) 403 requests with Python's default
# urllib User-Agent; a normal browser UA avoids that.
_REJECTED = set()  # clips that failed the vision check this run; never retried
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
    """(subject_visible, kid_safe, what_it_shows) from 3 frames. If the check
    itself can't run (no key, network) the clip is rejected: an unverified
    clip is how off-topic footage reached viewers before."""
    try:
        import seabini_script as ss
        key = ss._openai_key()
    except BaseException:
        print("[footage] no OpenAI key for the vision check")
        return False, False, ""
    images = []
    for frac in (0.2, 0.5, 0.8):
        jpg = f"{video_path}.{int(frac * 100)}.jpg"
        subprocess.run([ff_exe, "-y", "-ss", f"{duration * frac:.2f}", "-i", video_path,
                        "-vframes", "1", "-vf", "scale=256:-2", jpg], capture_output=True)
        if os.path.exists(jpg):
            b64 = base64.b64encode(pathlib.Path(jpg).read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})
    if not images:
        return False, False, ""
    prompt = (f"These are 3 frames from a stock clip for a preschool (ages 2-6) video about a {subject}. "
              'Reply with JSON only: {"subject_visible": true/false, "kid_safe": true/false, "shows": "..."}. '
              f"subject_visible = a real {subject} is clearly the main subject in most frames (not a tiny "
              "speck, not a different animal, not a toy or drawing). "
              "kid_safe = false if anything could scare or upset a small child: blood, injury, a dead "
              "or eaten animal, hunting/attacking, fishing hooks or nets with caught animals, trash-choked "
              "animals, or anything violent. Divers, boats and ordinary ocean scenes are fine. "
              f"shows = one plain sentence (max 15 words) saying what the {subject} is doing and where.")
    body = {"model": "gpt-4o-mini", "response_format": {"type": "json_object"}, "temperature": 0,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, *images]}]}
    for attempt in range(2):
        try:
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            verdict = json.loads(json.loads(urllib.request.urlopen(req, context=_SSL, timeout=60).read())
                                 ["choices"][0]["message"]["content"])
            return (bool(verdict.get("subject_visible")), bool(verdict.get("kid_safe")),
                    str(verdict.get("shows", "")).strip())
        except Exception as exc:
            print(f"[footage] vision check failed (attempt {attempt + 1}): {exc}")
    return False, False, ""


def _download(url, raw_path, out_path, ff_exe):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    pathlib.Path(raw_path).write_bytes(urllib.request.urlopen(req, context=_SSL, timeout=60).read())
    _normalize_vertical(raw_path, out_path, ff_exe)


def _candidates(query, subject, used):
    """Unused clips for `query`, those whose metadata names the subject first,
    randomised within each group so repeat queries don't always pick the same."""
    seen, pool = set(), []
    for c in _pexels_search(query) + _pixabay_search(query):
        if c[0] not in seen and c[0] not in used and c[0] not in _REJECTED:
            seen.add(c[0]); pool.append(c)
    random.shuffle(pool)
    pool.sort(key=lambda c: not _mentions(c[2], subject))
    return pool


def fetch_footage(query, tag, work_dir, ff_exe, used=None, subject=None):
    """Returns (local mp4 path, what the clip shows) — normalized to vertical
    720x1280 @24fps — or None if no verified footage exists. `used` holds clip
    ids that must not be reused (this episode + past episodes); the chosen id
    is added to it. `subject` is the creature the footage must actually show.
    Tries the behaviour-specific query first, then the plain creature name."""
    used = used if used is not None else set()
    subject = subject or query
    n = 0
    for search in dict.fromkeys([query, subject, f"{subject} underwater"]):
        for clip_id, url, _text, duration in _candidates(search, subject, used)[:_MAX_TRIES]:
            raw_path = str(work_dir / f"{tag}_raw{n}.mp4")
            out_path = str(work_dir / f"{tag}_vertical{n}.mp4")
            n += 1
            try:
                _download(url, raw_path, out_path, ff_exe)
            except Exception as exc:
                print(f"[footage] download failed for {clip_id}: {exc}")
                continue
            visible, safe, shows = _vision_check(out_path, duration, subject, ff_exe)
            if visible and safe:
                used.add(clip_id)
                print(f"[footage] {clip_id}: '{search}' -> {shows}")
                return out_path, shows
            _REJECTED.add(clip_id)
            print(f"[footage] rejected {clip_id}: {'not kid-safe' if not safe else f'no {subject} visible'}")
    print(f"[footage] no verified {subject} footage for '{query}'")
    return None
