# Layerdle

Guess the song from its layers: drums, then bass, then the other instruments, then vocals.

Everything is static files, so it hosts free on GitHub Pages / Netlify like any other site.
Once it's hosted, open the link in Safari, tap Share, then **Add to Home Screen**.

Until `songs.json` has songs in it, the game runs in demo mode with public-domain tunes it synthesizes itself.

## Adding real songs

One-time setup on your PC:

1. Install Python 3 from python.org (tick "Add to PATH").
2. Install ffmpeg: `winget install ffmpeg`
3. `pip install demucs`

Then for each batch of songs:

1. Put audio files in `incoming/`, named `Artist - Title (Year).mp3`.
   Leave the start time out and the script picks it: it finds where Apple Music's 30-second preview (usually the hook)
   sits in your song, or else detects the chorus. To force a start, add `@1m05` to the name (clip starts at 1:05),
   e.g. for songs whose intro is the famous part.
2. Double-click `add_songs.cmd` (or run `python process_songs.py`). Roughly 1 minute per song on a CPU.
3. Upload the new `songs/` folder and `songs.json`.

Optional: add extra `"Title — Artist"` strings to `"decoys"` in `songs.json` so the autocomplete list doesn't give the answers away.

## Files

- `index.html` – the whole game
- `songs.json` – your song library (the script fills it in)
- `songs/<id>/1-4.mp3` – cumulative clips (drums → full song)
- `process_songs.py` – splits songs with Demucs and makes the clips
- `manifest.json`, `icon-*.png` – home-screen app icon and name
