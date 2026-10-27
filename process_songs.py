"""Turn song files into Layerdle clips.

Drop audio files into ./incoming named like:

    Artist - Title.mp3
    Artist - Title (1999).mp3          <- adds the year (used for the hint)
    Artist - Title (1999) @1m05.mp3    <- force the layer clip to start at 1:05 (@65 works too)
    Artist - Title (1999) #intro.mp3   <- Note for Note starts at the intro (#chorus or #1m05 also work)

Without an @ time the script picks the start itself: it finds where Apple
Music's 30s preview (usually the hook) sits in your file, and falls back to
detecting the chorus (the loudest, most repeated section).
Without a # the Note for Note clip starts at the intro if it kicks in
straight away, otherwise at the start of the chorus.

Put files in a subfolder to give them a category, e.g. incoming/2010s/ or
incoming/Disney/. A song can be in several: drop an already-processed song
into another category folder and it just gets tagged, not re-processed.

Then run:

    python process_songs.py

For each song this splits the audio into drums / bass / other / vocals with
Demucs and writes:

    songs/<id>/1.mp3    drums                      } Layer by Layer:
    songs/<id>/2.mp3    + bass                     } the same 20s section,
    songs/<id>/3.mp3    + other instruments        } one more layer
    songs/<id>/4.mp3    + muffled vocals           } each time
    songs/<id>/5.mp3    full song                  }
    songs/<id>/nfn.mp3  10s with muffled vocals    - Note for Note (played 1s, 2s, ... 10s)

and adds the song to songs.json. Processed files move to incoming/done.

Needs: Python 3.9+, `pip install demucs`, and ffmpeg on your PATH.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MUFFLED = "vocals~"  # vocals through a "wall": only the low end gets through
LAYERS = [  # Layer by Layer clips, in reveal order
    ["drums"],
    ["drums", "bass"],
    ["drums", "bass", "other"],
    ["drums", "bass", "other", MUFFLED],
    ["drums", "bass", "other", "vocals"],
]
NFN_MIX = ["drums", "bass", "other", MUFFLED]
NFN_LENGTH = 10
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
NAME_RE = re.compile(
    r"^(?P<artist>.+?)\s+-\s+(?P<title>.+?)"
    r"(?:\s*\((?P<year>\d{4})\))?"
    r"(?:\s*@(?P<start>[\d:m.]+))?"
    r"(?:\s*#(?P<nfn>(?i:intro|chorus)|[\d:m.]+))?$"
)
# Downloader junk like "(Lyrics)", "[Official Video]", "(Audio)". Lyric text tacked on after it goes too.
JUNK_RE = re.compile(
    r"\s*[(\[][^)\]]*\b(?:lyrics?|official|video|audio|visuali[sz]er|hd|hq|4k|remaster(?:ed)?|explicit|clean|mv)\b[^)\]]*[)\]]"
    r"(?:(?!\s*(?:ft|feat|featuring)\b)[^(@#]*)?",
    re.I,
)
FEAT_RE = re.compile(r"\s*\(?\b(?:ft|feat|featuring)\b\.?\s+([^()]+?)\)?\s*$", re.I)


def clean_stem(stem):
    """Tidy a downloaded file name so NAME_RE can read it."""
    stem = "".join(c for c in stem if unicodedata.category(c) != "So")  # emoji like 🎵
    stem = re.sub(r"\s*(?:[–—‒−]|_-_)\s*", " - ", stem, count=1)  # en/em dashes
    stem = re.sub(r"(?<=[A-Za-z])_(?=[A-Za-z])", "'", stem)  # Windows turns ' into _ (Can_t)
    stem = re.sub(r"@(?=[A-Za-z])", "", stem)  # @eminem
    stem = JUNK_RE.sub("", stem)
    stem = re.sub(r"\s+(?:ll\s+|\|\s*)?lyrics?(?=\s*(?:\(\d{4}\)|@|#|$))", "", stem, flags=re.I)
    return re.sub(r"\s{2,}", " ", stem).strip()


def parse_name(stem):
    """Artist, title and the NAME_RE match for a file name; features move to the artist."""
    m = NAME_RE.match(clean_stem(stem))
    if not m:
        return None, None, None
    artist, title = m["artist"].strip(), m["title"].strip()
    feat = FEAT_RE.search(title)
    if feat:
        title = title[: feat.start()].strip()
        artist = f"{artist} ft. {feat[1].strip()}"
    return artist, title, m


def to_seconds(text):
    secs = 0.0
    for part in re.split(r"[:m]", text):
        secs = secs * 60 + float(part)
    return secs


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def separate(src, workdir, model):
    subprocess.run(
        [sys.executable, "-m", "demucs", "-n", model, "-o", str(workdir), str(src)],
        check=True,
    )
    return workdir / model / src.stem


def make_clip(stem_dir, parts, start, length, out, fade_in=0.2, fade_out=1.5):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for part in parts:
        cmd += ["-ss", str(start), "-t", str(length), "-i", str(stem_dir / f"{part.rstrip('~')}.wav")]
    graph, labels = [], ""
    for i, part in enumerate(parts):
        if part == MUFFLED:
            graph.append(f"[{i}:a]lowpass=f=350,lowpass=f=350,volume=1.6[m{i}]")
            labels += f"[m{i}]"
        else:
            labels += f"[{i}:a]"
    mix = f"{labels}amix=inputs={len(parts)}:normalize=0," if len(parts) > 1 else labels
    fade_at = max(length - fade_out, 0)
    graph.append(f"{mix}alimiter=limit=0.9,afade=t=in:d={fade_in},afade=t=out:st={fade_at}:d={fade_out}[a]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[a]", "-ac", "2", "-ar", "44100", "-b:a", "128k", str(out)]
    subprocess.run(cmd, check=True)


# ---------- Picking the clip start automatically ----------

AUTO_SR = 8000  # analysis sample rate; plenty for matching and chorus detection


def decode_mono(path):
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(AUTO_SR), "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def loose(text):
    text = re.sub(r"\(.*?\)|\[.*?\]| - .*$", "", text.lower())  # drop (feat. x), - Remastered, ...
    return re.sub(r"[^a-z0-9]+", "", text)


def apple_preview(artist, title, dest):
    """Download Apple Music's 30s preview for the song. Apple usually cuts these from the hook."""
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
    with urllib.request.urlopen(f"https://itunes.apple.com/search?{query}", timeout=15) as r:
        results = json.load(r).get("results", [])
    for res in results:
        if not res.get("previewUrl"):
            continue
        t, a = loose(res.get("trackName", "")), loose(res.get("artistName", ""))
        if (loose(title) in t or t in loose(title)) and (loose(artist) in a or a in loose(artist)):
            for attempt in range(3):  # downloads sometimes get cut off; retry
                try:
                    with urllib.request.urlopen(res["previewUrl"], timeout=30) as r:
                        data = r.read()
                        expected = int(r.headers.get("Content-Length") or len(data))
                    if len(data) == expected:
                        Path(dest).write_bytes(data)
                        return dest
                except OSError:
                    if attempt == 2:
                        raise
            return None
    return None


def locate(song, clip):
    """Where `clip` best lines up inside `song`: (seconds, match score 0..1)."""
    import numpy as np

    if len(clip) >= len(song):
        return 0.0, 0.0
    nfft = 1 << (len(song) + len(clip) - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(song, nfft) * np.conj(np.fft.rfft(clip, nfft)), nfft)
    corr = corr[: len(song) - len(clip) + 1]
    energy = np.concatenate([[0.0], np.cumsum(song.astype(np.float64) ** 2)])
    window = energy[len(clip):] - energy[: -len(clip)]
    score = corr / (np.sqrt(np.maximum(window, 1e-9)) * np.linalg.norm(clip) + 1e-9)
    i = int(np.argmax(score))
    return i / AUTO_SR, float(score[i])


def find_chorus(song, length):
    """Start (seconds) of the most repeated, loudest stretch: almost always the chorus."""
    import numpy as np

    hop, win = AUTO_SR // 2, 4096  # one frame per 0.5s
    n = (len(song) - win) // hop
    span = int(length * 2)
    if n < span * 2:
        return 0.0
    frames = np.lib.stride_tricks.as_strided(song, (n, win), (song.strides[0] * hop, song.strides[0]))
    mags = np.abs(np.fft.rfft(frames * np.hanning(win), axis=1))
    freqs = np.fft.rfftfreq(win, 1 / AUTO_SR)
    keep = (freqs > 55) & (freqs < 2000)
    pitch_class = np.round(12 * np.log2(freqs[keep] / 440)).astype(int) % 12
    chroma = np.zeros((n, 12))
    for pc in range(12):
        chroma[:, pc] = mags[:, keep][:, pitch_class == pc].sum(axis=1)
    chroma = np.log1p(chroma)
    chroma -= chroma.mean(axis=1, keepdims=True)
    chroma /= np.linalg.norm(chroma, axis=1, keepdims=True) + 1e-9
    sim = chroma @ chroma.T

    # For every start frame: how closely the next `span` frames repeat somewhere else in the song.
    repeat = np.full(n, -1.0)
    kernel = np.ones(span) / span
    for lag in range(span // 2, n - span):
        diag = np.convolve(np.diagonal(sim, lag), kernel, mode="valid")
        idx = np.arange(len(diag))
        repeat[idx] = np.maximum(repeat[idx], diag)
        repeat[idx + lag] = np.maximum(repeat[idx + lag], diag)

    rms = np.sqrt((frames ** 2).mean(axis=1))
    loud = np.convolve(rms, kernel, mode="full")[span - 1 : n]  # mean loudness of each window
    valid = np.arange(n - span + 1)
    valid = valid[(valid >= 10) & (repeat[valid] > -1)]  # skip the first 5s of intro
    if not len(valid):
        return 0.0
    rep, ld = repeat[valid], loud[valid]
    norm = lambda x: (x - x.min()) / (np.ptp(x) + 1e-9)
    best = valid[int(np.argmax(norm(rep) + 0.5 * norm(ld)))]
    return max(best / 2 - 1.0, 0.0)  # start a beat early so the section lands inside the clip


def loudness(song, hop_s=0.05):
    """RMS loudness every `hop_s` seconds."""
    import numpy as np

    hop = int(AUTO_SR * hop_s)
    n = len(song) // hop
    return np.sqrt((song[: n * hop].astype(np.float64).reshape(n, hop) ** 2).mean(axis=1)), hop_s


def intro_start(song):
    """When the music actually starts (skips leading silence)."""
    import numpy as np

    rms, hop = loudness(song)
    above = np.nonzero(rms > 0.1 * np.percentile(rms, 90))[0]
    return max(above[0] * hop - 0.05, 0.0) if len(above) else 0.0


def snap_to_onset(song, around, before=2.0, after=3.0):
    """The moment near `around` where the music jumps up most: the downbeat a section starts on."""
    rms, hop = loudness(song)
    lo, hi = max(int((around - before) / hop), 10), min(int((around + after) / hop), len(rms) - 20)
    best, best_i = 0.0, int(around / hop)
    for i in range(lo, hi):
        jump = rms[i : i + 20].mean() / (rms[i - 10 : i].mean() + 1e-6)  # next 1s vs previous 0.5s
        if jump > best:
            best, best_i = jump, i
    return best_i * hop


def nfn_start(song, chorus):
    """Note for Note start: the intro if it kicks in straight away, otherwise the chorus."""
    import numpy as np

    rms, hop = loudness(song)
    intro = intro_start(song)
    first_2s = rms[int(intro / hop) : int(intro / hop) + 40].mean()
    if first_2s >= 0.6 * np.median(rms):
        return intro, "punchy intro"
    return snap_to_onset(song, chorus), "chorus (intro is too quiet)"


def auto_start(song, artist, title, length, tmp):
    duration = len(song) / AUTO_SR
    try:
        preview = apple_preview(artist, title, Path(tmp) / "preview.m4a")
        if preview:
            start, score = locate(song, decode_mono(preview))
            if score >= 0.3:
                return min(start, max(duration - length, 0)), f"Apple preview match, score {score:.2f}"
            reason = f"Apple preview didn't match this recording (score {score:.2f})"
        else:
            reason = "no Apple preview found"
    except Exception as e:  # offline, API hiccup, ...
        reason = f"Apple lookup failed ({e.__class__.__name__})"
    return min(find_chorus(song, length), max(duration - length, 0)), f"chorus detection; {reason}"


def find_inputs(indir):
    """(file, category) pairs: files in incoming/ have no category, files in incoming/<Category>/ get that one."""
    found = []
    for p in sorted(indir.iterdir()):
        if p.is_dir() and p.name.lower() != "done":
            found += [(f, p.name) for f in sorted(p.iterdir()) if f.suffix.lower() in AUDIO_EXTS]
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            found.append((p, None))
    return found


def fmt_time(secs):
    return f"{int(secs // 60)}:{int(secs % 60):02d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="indir", default="incoming", help="folder with new songs (default: incoming)")
    ap.add_argument("--start", type=float, help="use this clip start (seconds) for every song instead of auto-picking")
    ap.add_argument("--length", type=float, default=20, help="clip length in seconds (default: 20)")
    ap.add_argument("--model", default="htdemucs", help="Demucs model (default: htdemucs)")
    ap.add_argument("--force", action="store_true", help="re-process songs already in songs.json")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found. Install it (e.g. `winget install ffmpeg`) and reopen the terminal.")
    try:
        import demucs  # noqa: F401
    except ImportError:
        sys.exit("Demucs not found. Install it with: pip install demucs")

    indir = ROOT / args.indir
    indir.mkdir(exist_ok=True)
    files = find_inputs(indir)
    if not files:
        sys.exit(f"No audio files in {indir}. Name them like 'Artist - Title (Year).mp3'.")

    db_path = ROOT / "songs.json"
    db = json.loads(db_path.read_text(encoding="utf-8")) if db_path.exists() else {}
    db.setdefault("songs", [])

    def save_db():
        db_path.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def move_done(src):
        done = indir / "done"
        done.mkdir(exist_ok=True)
        src.replace(done / src.name)

    for src, category in files:
        artist, title, m = parse_name(src.stem)
        if not m:
            print(f"SKIP {src.name}: name it 'Artist - Title.mp3'")
            continue
        song_id = slug(f"{artist} {title}")
        existing = next((s for s in db["songs"] if s["id"] == song_id), None)
        categories = sorted(set((existing or {}).get("categories", [])) | ({category} if category else set()))
        if existing and not args.force:
            if categories != existing.get("categories", []):
                existing["categories"] = categories
                save_db()
                move_done(src)
                print(f"TAGGED {title}: already processed, now in {', '.join(categories)}")
            else:
                print(f"SKIP {title}: already in songs.json (use --force to redo)")
            continue

        print(f"\n== {artist} - {title}")
        out_dir = ROOT / "songs" / song_id
        out_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            song = decode_mono(src)
            if m["start"]:
                start, why = to_seconds(m["start"]), "from file name"
            elif args.start is not None:
                start, why = args.start, "from --start"
            else:
                start, why = auto_start(song, artist, title, args.length, tmp)
            print(f"   layers clip {fmt_time(start)}-{fmt_time(start + args.length)} ({why})")

            nfn = (m["nfn"] or "").lower()
            if nfn == "intro":
                nstart, nwhy = intro_start(song), "intro, from file name"
            elif nfn == "chorus":
                nstart, nwhy = snap_to_onset(song, start), "chorus, from file name"
            elif nfn:
                nstart, nwhy = to_seconds(nfn), "from file name"
            else:
                nstart, nwhy = nfn_start(song, start)
            print(f"   note for note from {fmt_time(nstart)} ({nwhy})")

            # Copy to a plain name so Demucs' output folder is predictable.
            work_src = Path(tmp) / f"track{src.suffix.lower()}"
            shutil.copy(src, work_src)
            stem_dir = separate(work_src, Path(tmp) / "out", args.model)
            for n, parts in enumerate(LAYERS, 1):
                make_clip(stem_dir, parts, start, args.length, out_dir / f"{n}.mp3")
            # The game cuts this one off after 1, 2, 3... seconds, so no fade-in to blur the first second.
            make_clip(stem_dir, NFN_MIX, nstart, NFN_LENGTH, out_dir / "nfn.mp3", fade_in=0.01, fade_out=0.5)
            version = int(time.time())

        entry = {
            "id": song_id,
            "title": title,
            "artist": artist,
            "year": int(m["year"]) if m["year"] else (existing or {}).get("year"),
            "start": round(start, 1),
            "nfnStart": round(nstart, 2),
            "categories": categories,
            # ?v= gives re-processed clips new URLs, so phones don't keep playing an old cached copy
            "clips": [f"songs/{song_id}/{n}.mp3?v={version}" for n in range(1, len(LAYERS) + 1)],
            "nfn": f"songs/{song_id}/nfn.mp3?v={version}",
        }
        if existing:
            db["songs"][db["songs"].index(existing)] = entry
        else:
            db["songs"].append(entry)
        save_db()
        move_done(src)
        print(f"   added {title}" + (f" to {', '.join(categories)}" if categories else ""))

    print(f"\nDone. songs.json now has {len(db['songs'])} songs.")


if __name__ == "__main__":
    main()
