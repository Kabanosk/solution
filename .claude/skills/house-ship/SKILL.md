---
name: house-ship
description: Release for build contests - deploy the demo, prepare fallback, pitch script, slides, submission README, and a clean public export.
disable-model-invocation: true
---
Follow **Release** in `/home/wojciech.fiolka/house/contests/collabothon-2026/.house/playbooks/build.md` and `/home/wojciech.fiolka/house/contests/collabothon-2026/.house/protocols/export.md`.

1. Deploy if `spec.deploy` is true, with every API key set server-side in the hosting environment. Then run it
   like the jury will: the public URL from a clean browser on another device/network, and a fresh clone without our
   `.env` (must fall back to the labelled demo mode, not crash). Record the URL and both results in `PROGRESS.md`.
2. Fallback: demo video plan + offline/seeded mode.
3. Dispatch `pitch` for script, slides, README, demo plan (in `spec.languages.public`).
3b. Go through `/home/wojciech.fiolka/house/contests/collabothon-2026/.house/protocols/demo-checklist.md` line by line (judge runs it); anything red blocks.
4. Final judge round on the release candidate: dispatch the `judge` agent.
5. Export: `/home/wojciech.fiolka/house/bin/house export <contest> /home/wojciech.fiolka/house/exports/<contest>-<YYYYMMDD-HHMM>`; show the human what will be published. Pushing the
   export to a public remote is the human's call unless `spec` says otherwise.
