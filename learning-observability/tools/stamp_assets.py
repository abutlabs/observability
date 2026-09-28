#!/usr/bin/env python3
"""Cache-busting for the built site: every reference in DIR/*.html to a local stylesheet or
script (css/..., js/...) gets ?v=<first 10 hex digits of the file's SHA-256>. A changed
file gets a new URL, so no visitor runs a new page with an old cached script.

    python3 learning-observability/tools/stamp_assets.py DIR

Exits 1 if nothing was stamped (the build is then not what it should be). Stdlib only.
"""
import glob
import hashlib
import os
import re
import sys

REF = re.compile(r'(href|src)="((?:css|js)/[^"?#]+\.(?:css|js))"')


def stamp(out_dir):
    stamped = 0
    for page in sorted(glob.glob(os.path.join(out_dir, "*.html"))):
        with open(page, encoding="utf-8") as fh:
            html = fh.read()

        def sub(m):
            nonlocal stamped
            path = os.path.join(out_dir, m.group(2))
            if not os.path.isfile(path):
                return m.group(0)
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()[:10]
            stamped += 1
            return '%s="%s?v=%s"' % (m.group(1), m.group(2), digest)

        with open(page, "w", encoding="utf-8") as fh:
            fh.write(REF.sub(sub, html))
    return stamped


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: stamp_assets.py DIR")
    n = stamp(sys.argv[1])
    print("stamp_assets: %d references stamped in %s/*.html" % (n, sys.argv[1]))
    sys.exit(0 if n else 1)
