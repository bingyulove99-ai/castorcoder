"""Download the public code benchmarks used by the evaluation suites.

    python evals/scripts/fetch_datasets.py --check
    python evals/scripts/fetch_datasets.py --force

Files are pinned by SHA-256.  jsDelivr is used first because it is reachable
from networks where raw.githubusercontent.com is not; the GitHub raw URL is the
fallback.  Dataset code is only ever executed inside throw-away evaluation
workspaces, never imported by CastorCoder itself.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

DATASETS = {
    "HumanEval.jsonl.gz": {
        "sha256": "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef",
        "urls": [
            "https://cdn.jsdelivr.net/gh/openai/human-eval@master/data/HumanEval.jsonl.gz",
            "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz",
        ],
        "license": "MIT (openai/human-eval)",
    },
    "sanitized-mbpp.json": {
        "sha256": "ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9",
        "urls": [
            "https://cdn.jsdelivr.net/gh/google-research/google-research@master/mbpp/sanitized-mbpp.json",
            "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json",
        ],
        "license": "CC-BY-4.0 (google-research/mbpp)",
    },
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(urls: list[str], destination: Path) -> bytes:
    last_error: Exception | None = None
    for url in urls:
        try:
            print(f"fetching {url}")
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except Exception as exc:  # try the next mirror
            last_error = exc
            print(f"  failed: {type(exc).__name__}: {exc}")
    raise SystemExit(f"all mirrors failed: {last_error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default="evals/datasets")
    parser.add_argument("--force", action="store_true", help="Re-download existing files")
    parser.add_argument("--check", action="store_true", help="Only verify checksums")
    args = parser.parse_args(argv)

    destination = Path(args.destination)
    destination.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, spec in DATASETS.items():
        path = destination / name
        if path.is_file() and not args.force and not args.check:
            actual = digest(path)
            state = "ok" if actual == spec["sha256"] else "CHECKSUM MISMATCH"
            print(f"{name}: {state}")
            failed |= actual != spec["sha256"]
            continue
        if args.check:
            if not path.is_file():
                print(f"{name}: missing")
                failed = True
                continue
            actual = digest(path)
            state = "ok" if actual == spec["sha256"] else "CHECKSUM MISMATCH"
            print(f"{name}: {state}")
            failed |= actual != spec["sha256"]
            continue

        payload = download(spec["urls"], path)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != spec["sha256"]:
            print(f"{name}: rejecting download with sha256 {actual}")
            failed = True
            continue
        path.write_bytes(payload)
        print(f"{name}: {len(payload)} bytes, sha256 ok, {spec['license']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
