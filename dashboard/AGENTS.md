# AGENTS.md — collabothon-2026

## Mandatory design baseline

The mandatory frontend and design standard in `../AGENTS.md` applies to all work here. Always invoke `ui-ux-pro-max` for frontend/design work and match Scout's existing Linear-like UI. Additional skills and page-specific guidance may supplement, not override, that baseline; exceptions require the user's explicit approval. Preserve this requirement when regenerating or editing this file.

## Styling conventions

- Use icons sparingly.
- Build the website in React and Next.js using existing `shadcn/ui` components from https://ui.shadcn.com/. Compose the interface from these components and their theme tokens; avoid ad-hoc component styling.
- Colours come only from the tokens in `app/globals.css`, never raw hex or Tailwind colour classes (`emerald-500`,
  `amber-600`, …) in components. The look is calm and Linear-like: neutral surfaces and numbers, one teal accent
  (`--primary`, `--chart-balance`), data in the teal ramp and greys (`--chart-2`…`--chart-5`), amber
  (`--warning`, `--warning-mark`) and red (`--danger`) only for status.
- Use unique HTML id tag for each element on the site.

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
