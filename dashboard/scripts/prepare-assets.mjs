import { cp, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const dashboard = fileURLToPath(new URL("../", import.meta.url));
const publicDir = join(dashboard, "public");
await mkdir(publicDir, { recursive: true });
await cp(join(dashboard, "assets/fonts"), join(publicDir, "fonts"), { recursive: true });
console.log("Local Inter and Geist Mono fonts prepared for Next.js.");
