import { scoutIconSvg } from "@/lib/brand-icon";

// Public (auth-exempt in proxy.ts) so Slack can fetch it as the bot avatar.
export function GET() {
  return new Response(scoutIconSvg(512), { headers: { "Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400" } });
}
