# Layerdle

A party game for guessing songs layer by layer: drums, then bass, then the other instruments, then vocals.
One phone runs the game and everyone shouts their guesses. Fewer layers means more points (5 → 1).

Hosted on Cloudflare (auto-deploys on every push to `main`): https://layerdle.joshuajford7.workers.dev
On iPhone: open it in Safari → Share → **Add to Home Screen**.

## Playing offline (road trips)

On Wi-Fi, tap **⬇ Save** next to a category. Once it shows **✓ Offline** the app and those songs work with no signal.
Use the home-screen icon: iOS keeps home-screen apps' saved data, but may clear a Safari tab's.

## Adding songs

One-time setup: Python 3.12, ffmpeg and Demucs (`pip install "torch<2.9" "torchaudio<2.9" demucs numpy soundfile`).

1. Put audio files in `incoming/`, named `Artist - Title (Year).mp3`.
   - **Categories:** put files in a subfolder instead, e.g. `incoming/2010s/` or `incoming/Disney/`.
     To add an existing song to another category, drop the same file in that folder. It gets tagged, not re-processed.
   - **Clip start:** picked automatically, by matching Apple Music's 30s preview (usually the hook) or else detecting
     the chorus. To force it, add `@1m05` to the name (starts at 1:05).
2. Double-click `add_songs.cmd`. Roughly 1–2 minutes per song. When it finishes it publishes the new songs automatically.

## Files

- `index.html` – the whole game
- `service-worker.js` – offline support
- `songs.json` – song library (the script fills it in)
- `songs/<id>/1-4.mp3` – cumulative clips (drums → full song)
- `process_songs.py`, `add_songs.cmd` – split songs with Demucs and make the clips
- `manifest.json`, `icon-*.png` – home-screen name and icon
