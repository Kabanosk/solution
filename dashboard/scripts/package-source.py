"""Create an allowlisted binary-build context without data, secrets or dependencies."""
from pathlib import Path
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
destination = Path(sys.argv[1])
allowed = [
    "Containerfile", ".dockerignore", "package.json", "package-lock.json",
    "next.config.ts", "tsconfig.json", "postcss.config.mjs", "proxy.ts",
    "app", "components", "lib", "hooks", "assets", "scripts/prepare-assets.mjs",
]
with tarfile.open(destination, "w:gz") as archive:
    for name in allowed:
        archive.add(root / name, arcname=name)
print(f"Dashboard build context: {destination} ({destination.stat().st_size} bytes)")
