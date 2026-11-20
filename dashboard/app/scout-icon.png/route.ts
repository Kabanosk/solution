import { readFile } from "node:fs/promises";
import { join } from "node:path";
export const runtime = "nodejs";

// Public (auth-exempt in proxy.ts) 512x512 PNG for the Slack avatar; traced into the standalone build via next.config.ts.
export async function GET() {
  const png = await readFile(join(process.cwd(), "assets/scout-icon-512.png"));
  return new Response(new Uint8Array(png), { headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=86400" } });
}
