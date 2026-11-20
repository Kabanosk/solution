# AGENTS.md — collabothon-2026

## Styling conventions

- Use icons and bright colors sparingly.
- Build the website in React and Next.js using existing `shadcn/ui` components from https://ui.shadcn.com/. Compose the interface from these components and their theme tokens; avoid ad-hoc component styling.
- Restrict the color palette to dark tones, green, red, and orange.
- Use unique HTML id tag for each element on the site.

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
