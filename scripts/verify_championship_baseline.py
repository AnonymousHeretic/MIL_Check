"""Stage 0–1 gate: preserve every baseline file except the README entry point.

Run from any directory. No Git/network/training dependencies. Future product PRs
must explicitly revise this gate; the archive branch remains the recovery source.
"""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/championship/baseline-manifest.json"


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures = []
    checked = 0
    for entry in manifest["files"]:
        if entry["path"] == "README.md":
            continue  # Deliberate documentation entry-point change in this PR.
        path = ROOT / entry["path"]
        if not path.is_file():
            failures.append(f"missing: {entry['path']}")
            continue
        content = path.read_bytes()
        blob = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
        if (hashlib.sha1(blob).hexdigest() != entry["git_blob_sha"]
                or len(content) != entry["size_bytes"]):
            failures.append(f"changed: {entry['path']}")
        checked += 1
    if failures:
        raise SystemExit("Baseline preservation FAILED\n" + "\n".join(failures))
    print(f"Baseline preservation OK: {checked} files byte-identical; README excluded")


if __name__ == "__main__":
    main()
