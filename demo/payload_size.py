#!/usr/bin/env python
"""Print every shipped demo file with its size and the total; exit 1 if the total exceeds 10,000,000 bytes.

Shipped = docs/assets/js/demo.js + docs/assets/js/demo/** + docs/assets/demo/**.
"""
import os
import sys

LIMIT = 10_000_000
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def shipped_files():
    files = [os.path.join(REPO, "docs", "assets", "js", "demo.js")]
    for top in (os.path.join(REPO, "docs", "assets", "js", "demo"), os.path.join(REPO, "docs", "assets", "demo")):
        for d, dirs, fs in os.walk(top):
            dirs.sort()
            files += [os.path.join(d, f) for f in sorted(fs)]
    return files


def main():
    total = 0
    for f in shipped_files():
        size = os.path.getsize(f)
        total += size
        print(f"{size:>10}  {os.path.relpath(f, REPO)}")
    verdict = "OK" if total <= LIMIT else "OVER LIMIT"
    print(f"{total:>10}  total shipped demo payload ({total / 1e6:.2f} MB; limit {LIMIT:,} bytes): {verdict}")
    return 0 if total <= LIMIT else 1


if __name__ == "__main__":
    sys.exit(main())
