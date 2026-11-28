# Pitch deck and film

- [`pitch.html`](pitch.html): the 14-slide pitch deck, built on screens from the live app. Clone the repository and open the file in a browser; it needs no server. Keys: arrows or a clicker move between slides, `F` is full screen.
- [`pitch/scout-promo.mp4`](pitch/scout-promo.mp4): *Scout in 66 seconds*, the promo film shown in the deck (720p web version).
- [`pitch/`](pitch): the screenshots, the film and the Inter and Geist Mono fonts with their OFL licences.

GitHub shows the HTML source instead of running the slides, so open the deck from a local clone.

## Rebuilding the frogs

`pitch.html` loads `pitch-frogs.bundle.js`, a single-file build of `pitch-frogs.js` and `vendor/three.module.js`, so the deck also works from `file://`. After editing `pitch-frogs.js`, rebuild it from the repository root:

```bash
esbuild presentation/pitch-frogs.js --bundle --format=iife --minify --target=es2020 --outfile=presentation/pitch-frogs.bundle.js
```

The earlier decks are in the git history at the tag `collabothon-2026-submission`. The full-quality 1080p film and the first explainer film (made under the project's working name, Lantern) are attached to the GitHub release of that tag.
