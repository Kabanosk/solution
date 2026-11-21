import { readFile } from "node:fs/promises";
import { join } from "node:path";
export const runtime = "nodejs";

// Official Google sign-in artwork, served locally without a third-party image request.
export async function GET() {
  const png = await readFile(join(process.cwd(), "assets/google-g.png"));
  return new Response(new Uint8Array(png), { headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=86400" } });
}
