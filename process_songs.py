"""Turn song files into Layerdle clips.

Drop audio files into ./incoming named like:

    Artist - Title.mp3
    Artist - Title (1999).mp3          <- adds the year (used for the hint)
    Artist - Title (1999) @1m05.mp3    <- clip starts at 1:05 instead of the default
                                          (Windows doesn't allow ":" in names; @65 works too)

Then run:

    python process_songs.py

For each song this splits the audio into drums / bass / other / vocals with
Demucs, cuts the same section from each, and writes four cumulative clips:

    songs/<id>/1.mp3  drums
    songs/<id>/2.mp3  drums + bass
    songs/<id>/3.mp3  drums + bass + other
    songs/<id>/4.mp3  full song

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEMS = ["drums", "bass", "other", "vocals"]
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
NAME_RE = re.compile(
    r"^(?P<artist>.+?)\s+-\s+(?P<title>.+?)"
    r"(?:\s*\((?P<year>\d{4})\))?"
    r"(?:\s*@(?P<start>[\d:m.]+))?$"
)


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


def make_clip(stem_dir, layers, start, length, out):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for stem in STEMS[:layers]:
        cmd += ["-ss", str(start), "-t", str(length), "-i", str(stem_dir / f"{stem}.wav")]
    inputs = "".join(f"[{i}:a]" for i in range(layers))
    mix = f"{inputs}amix=inputs={layers}:normalize=0," if layers > 1 else inputs
    fade_at = max(length - 1.5, 0)
    chain = f"{mix}alimiter=limit=0.9,afade=t=in:d=0.2,afade=t=out:st={fade_at}:d=1.5[a]"
    cmd += ["-filter_complex", chain, "-map", "[a]", "-ac", "2", "-ar", "44100", "-b:a", "128k", str(out)]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="indir", default="incoming", help="folder with new songs (default: incoming)")
    ap.add_argument("--start", type=float, default=30, help="default clip start in seconds (default: 30)")
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
    files = sorted(p for p in indir.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    if not files:
        sys.exit(f"No audio files in {indir}. Name them like 'Artist - Title (Year).mp3'.")

    db_path = ROOT / "songs.json"
    db = json.loads(db_path.read_text(encoding="utf-8")) if db_path.exists() else {}
    db.setdefault("songs", [])
    db.setdefault("decoys", [])

    for src in files:
        m = NAME_RE.match(src.stem)
        if not m:
            print(f"SKIP {src.name}: name it 'Artist - Title.mp3'")
            continue
        artist, title = m["artist"].strip(), m["title"].strip()
        song_id = slug(f"{artist} {title}")
        existing = next((s for s in db["songs"] if s["id"] == song_id), None)
        if existing and not args.force:
            print(f"SKIP {title}: already in songs.json (use --force to redo)")
            continue

        start = to_seconds(m["start"]) if m["start"] else args.start
        print(f"\n== {artist} - {title} (clip {start:.0f}s to {start + args.length:.0f}s)")
        out_dir = ROOT / "songs" / song_id
        out_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            # Copy to a plain name so Demucs' output folder is predictable.
            work_src = Path(tmp) / f"track{src.suffix.lower()}"
            shutil.copy(src, work_src)
            stem_dir = separate(work_src, Path(tmp) / "out", args.model)
            for n in range(1, len(STEMS) + 1):
                make_clip(stem_dir, n, start, args.length, out_dir / f"{n}.mp3")

        entry = {
            "id": song_id,
            "title": title,
            "artist": artist,
            "year": int(m["year"]) if m["year"] else None,
            "clips": [f"songs/{song_id}/{n}.mp3" for n in range(1, len(STEMS) + 1)],
        }
        if existing:
            db["songs"][db["songs"].index(existing)] = entry
        else:
            db["songs"].append(entry)
        db_path.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        done = indir / "done"
        done.mkdir(exist_ok=True)
        shutil.move(str(src), str(done / src.name))
        print(f"   added {title}")

    print(f"\nDone. songs.json now has {len(db['songs'])} songs.")


if __name__ == "__main__":
    main()
