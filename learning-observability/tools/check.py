#!/usr/bin/env python3
"""Check the course before it is built or committed.

    python3 learning-observability/tools/check.py

  1. site/data/course.json and the markdown agree: every lesson it lists exists, every
     lesson file is listed, and each starts with a "# " title.
  2. Every relative link in the markdown resolves: to a lesson, to another file of the
     repository, and, for "#anchor" links into markdown, to a real heading.
  3. The site uses relative URLs only, so it works under a sub-path (GitHub Pages serves it
     at /observability/): no href, src, fetch or url() that starts with "/".

Exits 1 and lists every problem it found. Stdlib only.
"""
import glob
import json
import os
import re
import sys

COURSE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(COURSE)
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^(```|~~~)")
ROOT_ABS = [re.compile(p) for p in (r'(?:href|src)\s*=\s*"/(?!/)', r"(?:href|src)\s*=\s*'/(?!/)",
                                    r"fetch\(\s*['\"`]/(?!/)", r"url\(\s*['\"]?/(?!/)")]


def prose_lines(text):
    """The lines outside fenced code blocks, with inline code removed."""
    inside = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line.strip()):
            inside = not inside
            continue
        if not inside:
            yield n, re.sub(r"`[^`]*`", "", line)


def slugs(path):
    """GitHub-style heading anchors of a markdown file."""
    seen, out = {}, set()
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for _, line in prose_lines(text):
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        s = m.group(1).lower()
        s = re.sub(r"<[^>]+>", "", s)
        s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).replace(" ", "-")
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if n == 0 else "%s-%d" % (s, n))
    return out


def check_manifest(problems):
    with open(os.path.join(COURSE, "site", "data", "course.json"), encoding="utf-8") as fh:
        course = json.load(fh)
    listed = set()
    for track in course["tracks"]:
        base = track.get("dir") or os.path.join("content", track["id"])
        for lesson in track["lessons"]:
            rel = os.path.join(base, lesson["id"] + ".md")
            listed.add(os.path.normpath(rel))
            path = os.path.join(COURSE, rel)
            if not os.path.isfile(path):
                problems.append("course.json lists %s, which does not exist" % rel)
                continue
            with open(path, encoding="utf-8") as fh:
                if not fh.readline().startswith("# "):
                    problems.append("%s does not start with a '# ' title" % rel)
    for path in glob.glob(os.path.join(COURSE, "content", "*", "*.md")) + \
            glob.glob(os.path.join(COURSE, "exercises", "*.md")):
        rel = os.path.normpath(os.path.relpath(path, COURSE))
        if rel not in listed:
            problems.append("%s is not in course.json" % rel)
    return len(listed)


def check_links(problems):
    count = 0
    files = glob.glob(os.path.join(COURSE, "**", "*.md"), recursive=True)
    for path in sorted(f for f in files if "/_site/" not in f and "/site/" not in f):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        rel_path = os.path.relpath(path, REPO)
        for n, line in prose_lines(text):
            for target in LINK.findall(line):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                    continue                       # http:, https:, mailto:
                count += 1
                if target.startswith("/"):
                    problems.append("%s:%d: root-absolute link %s" % (rel_path, n, target))
                    continue
                file_part, _, anchor = target.partition("#")
                dest = os.path.normpath(os.path.join(os.path.dirname(path), file_part)) if file_part else path
                if not dest.startswith(REPO) or not os.path.exists(dest):
                    problems.append("%s:%d: broken link %s" % (rel_path, n, target))
                    continue
                if anchor and dest.endswith(".md") and anchor not in slugs(dest):
                    problems.append("%s:%d: no heading #%s in %s" % (rel_path, n, anchor,
                                                                    os.path.relpath(dest, REPO)))
    return count


def check_relative_urls(problems):
    for path in sorted(glob.glob(os.path.join(COURSE, "site", "*.html")) +
                       glob.glob(os.path.join(COURSE, "site", "js", "*.js")) +
                       glob.glob(os.path.join(COURSE, "site", "css", "*.css"))):
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                for pat in ROOT_ABS:
                    if pat.search(line):
                        problems.append("%s:%d: root-absolute URL: %s"
                                        % (os.path.relpath(path, REPO), n, line.strip()[:100]))


def main():
    problems = []
    lessons = check_manifest(problems)
    links = check_links(problems)
    check_relative_urls(problems)
    for p in problems:
        print("check: " + p)
    print("check: %d lessons, %d relative links, %s" % (lessons, links,
                                                       "%d problem(s)" % len(problems) if problems else "all good"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
