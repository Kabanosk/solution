# Scout presentation and pitch video

This directory contains the HTML pitch deck, its supporting assets, design concepts, and the completed pitch/explainer video.

## View the presentation

- [Pitch deck](index.html)
- [Alternate Scout deck](scout.html)
- [Pitch / explainer video (MP4)](lantern.mp4)
- [Design concepts](concepts/)

GitHub shows HTML source rather than running the interactive slides. To view the deck, clone the repository and serve this directory from the repository root:

```bash
python3 -m http.server 8000 --directory presentaito
```

Then open <http://localhost:8000/> (or <http://localhost:8000/scout.html>). The local server is needed for JavaScript module loading. The slides also load fonts from Google Fonts; system fonts are used if those are unavailable.

## Video

The actual video file is included in this repository as [`lantern.mp4`](lantern.mp4), not just linked to an external service. Open its GitHub file page and use the raw/download link to watch or save it if inline playback is unavailable.

The completed export is approximately two minutes long at 1920 × 1080, with H.264 video and AAC audio. It retains the original Lantern branding; the HTML pitch deck uses Scout branding. This is the existing completed export, not a newly rendered Scout-branded refresh.

The directory name `presentaito` is intentional and matches the requested repository path. Supporting files retain their original relative paths so the deck and concept pages can load them.
