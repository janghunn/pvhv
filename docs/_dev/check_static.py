#!/usr/bin/env python3
"""Check the static page and integration contracts using only the standard library."""

import argparse
import difflib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import struct
import subprocess
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET


DOCS = Path(__file__).resolve().parents[1]
REQUIRED_SLOTS = {
    "teaser", "method-overview", "demo", "results-keep-cull", "results-comparison"
}
ALLOWED_HOSTS = {"doi.org", "cg.skku.edu", "github.com", "www.youtube.com"}
ATTRIBUTION_HOSTS = {"polyhaven.com", "creativecommons.org", "casual-effects.com", "developer.nvidia.com"}
JS_ATTRIBUTION_URLS = {"https://polyhaven.com/a/chess_set", "https://creativecommons.org/publicdomain/zero/1.0/"}
TEXT_SUFFIXES = {".html", ".css", ".js", ".mjs", ".json", ".md", ".svg", ".txt"}
REIMPLEMENTATION_LABEL = "Re-implementation (2026), not the code used for the paper's experiments."
VIDEO_URL = "https://www.youtube-nocookie.com/embed/cnR8iujBu7A"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def descendants(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()

    def text(self):
        return "".join(child.text() if isinstance(child, Node) else child for child in self.children)

    def has_class(self, name):
        return name in (self.attrs.get("class") or "").split()


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def nodes(self):
        return list(self.root.descendants())

    def by_id(self, identity):
        found = [node for node in self.nodes() if node.attrs.get("id") == identity]
        require(len(found) == 1, f"expected one #{identity}, found {len(found)}")
        return found[0]


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def equal(actual, expected, label):
    if actual != expected:
        print("\n".join(difflib.unified_diff(
            str(expected).splitlines(), str(actual).splitlines(),
            fromfile=f"expected/{label}", tofile=f"actual/{label}", lineterm=""
        )))
        raise AssertionError(f"{label} differs (see diff)")


def collapse(value):
    return " ".join(value.split())


def check_facts(page, facts):
    for key, identity in [("title", "title"), ("authors", "authors"),
                          ("affiliation", "affiliation"), ("venue", "venue")]:
        equal(page.by_id(identity).text().strip(), facts[key], key)
    equal(page.by_id("venue").attrs.get("href"), facts["doi_url"], "doi_url")
    abstract = page.by_id("abstract-text").text()
    equal(collapse(abstract), collapse(facts["abstract"]), "abstract (whitespace collapsed)")
    equal(abstract.strip(), facts["abstract"], "abstract (exact)")
    citation = page.by_id("citation").text().removeprefix("\n").rstrip()
    equal(citation, facts["bibtex"], "bibtex")
    return "title, authors, affiliation, venue, DOI, abstract and BibTeX match facts.json exactly"


def check_sources(args, facts):
    checked = []
    if args.lab:
        source = args.lab.read_text(encoding="utf-8")
        patterns = {
            "title": r"class=['\"]gtitle-detail['\"]>(.*?)<",
            "authors": r"class=['\"]author-detail['\"]>(.*?)<",
            "venue": r'<a\s+href=[\"]https://doi.org/10\.1145/3592108[\"]>(.*?)</a>',
            "abstract": r"class=['\"]abstract-detail['\"]>(.*?)</div>",
            "bibtex": r"class=['\"]bibtex-detail['\"]>(.*?)</div>",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, source, re.S)
            require(match, f"lab {key} not found")
            equal(unescape(match.group(1)).strip(), facts[key], f"lab/{key}")
        checked.append("lab facts match verbatim")
    if args.crossref:
        authors = json.loads(args.crossref.read_text(encoding="utf-8"))["message"]["author"]
        require(authors, "Crossref has no authors")
        for index, author in enumerate(authors):
            equal(author["affiliation"][0]["name"], facts["affiliation"], f"Crossref author {index + 1}")
        checked.append("all Crossref affiliations match")
    return "; ".join(checked) or "no optional source files supplied"


def check_slots(page):
    slots = [node for node in page.nodes() if "data-slot" in node.attrs]
    names = [node.attrs["data-slot"] for node in slots]
    equal(set(names), REQUIRED_SLOTS, "required HTML slots")
    require(len(names) == len(set(names)), "duplicate HTML slots")
    rows = {}
    for line in (DOCS / "SLOTS.md").read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        fields = [field.strip() for field in line.strip("|").split("|")]
        if fields[0].startswith("`"):
            require(len(fields) == 6, f"malformed slot table row: {line}")
            name = fields[0].strip("`")
            require(name not in rows, f"duplicate table slot: {name}")
            rows[name] = fields
    equal(set(rows), REQUIRED_SLOTS, "SLOTS.md slots")
    for slot in slots:
        name = slot.attrs["data-slot"]
        src, aspect = slot.attrs.get("data-src"), slot.attrs.get("data-aspect")
        require(src and aspect, f"{name}: missing data-src/data-aspect")
        equal(rows[name][1], "docs/" + src, f"{name} expected file")
        equal(rows[name][2], aspect, f"{name} aspect")
        descendants = list(slot.descendants())
        if name != "demo":
            captions = [node for node in descendants if node.tag == "figcaption"]
            require(len(captions) == 1, f"{name}: expected one figcaption")
            equal(captions[0].text().strip(), rows[name][3], f"{name} caption")
        boxes = [node for node in descendants if node.has_class("slot-box")]
        if boxes:
            require(all(node.attrs.get("role") == "img" and (node.attrs.get("aria-label") or "").strip()
                        for node in boxes), f"{name}: placeholder needs role=img and aria-label")
        elif name == "demo":
            require(descendants, "demo: empty mount point")
        else:
            require(any(node.tag == "img" and (node.attrs.get("alt") or "").strip() for node in descendants),
                    f"{name}: needs an accessible placeholder or image")
    return "all 5 required slots and table paths, aspects, captions and accessible contents agree"


def check_a11y(page):
    nodes = page.nodes()
    require(all((node.attrs.get("alt") or "").strip() for node in nodes if node.tag == "img"), "image missing alt text")
    copy = page.by_id("copy")
    require(copy.tag == "button" and copy.attrs.get("type") == "button", "copy must be button[type=button]")
    require(page.by_id("copy-status").attrs.get("role") == "status", "copy-status needs role=status")
    require(any(node.tag == "html" and (node.attrs.get("lang") or "").strip() for node in nodes), "html needs lang")
    require(sum(node.tag == "h1" for node in nodes) == 1, "expected exactly one h1")
    return "image alternatives, copy button, live status, language and single h1 present"


def image_size(path):
    """Read the shipped raster headers or schematic viewBox without dependencies."""
    data = path.read_bytes()
    if path.suffix.lower() == ".svg":
        try:
            bounds = ET.fromstring(data).attrib["viewBox"].split()
            require(len(bounds) == 4, f"{path.name}: invalid SVG viewBox")
            width, height = map(float, bounds[2:])
            require(width.is_integer() and height.is_integer() and min(width, height) > 0,
                    f"{path.name}: expected positive integral SVG dimensions")
            return int(width), int(height)
        except ET.ParseError as error:
            raise ValueError(f"{path.name}: invalid SVG: {error}") from error
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        offset = 12
        while offset + 8 <= len(data):
            kind = data[offset:offset + 4]
            length = int.from_bytes(data[offset + 4:offset + 8], "little")
            chunk = data[offset + 8:offset + 8 + length]
            if kind == b"VP8X" and len(chunk) >= 10:
                return (1 + int.from_bytes(chunk[4:7], "little"),
                        1 + int.from_bytes(chunk[7:10], "little"))
            if kind == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", chunk[6:10])
                return width & 0x3fff, height & 0x3fff
            if kind == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2f:
                bits = int.from_bytes(chunk[1:5], "little")
                return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
            offset += 8 + length + (length & 1)
    raise ValueError(f"{path.name}: unsupported or invalid image header")


def check_slots_filled(page):
    demo_nodes = set(page.by_id("demo-root").descendants())
    require(not any(node.has_class("slot-box") and node not in demo_nodes for node in page.nodes()),
            "slot-box outside #demo-root")
    for figure in (node for node in page.nodes() if node.tag == "figure" and "data-slot" in node.attrs):
        name = figure.attrs["data-slot"]
        images = [node for node in figure.descendants() if node.tag == "img"]
        require(len(images) == 1, f"{name}: expected one image")
        image = images[0]
        equal(image.attrs.get("src"), figure.attrs.get("data-src"), f"{name} image source")
        path = (DOCS / image.attrs["src"]).resolve()
        require(path.is_relative_to(DOCS) and path.is_file(), f"{name}: missing or nonlocal image")
        dimensions = image_size(path)
        actual = tuple(int(image.attrs.get(key, "0")) for key in ("width", "height"))
        equal(actual, dimensions, f"{name} image dimensions")
    return "all figure images exist with exact dimensions; only the runtime demo placeholder remains"


def check_captions(page, source):
    for figure in (node for node in page.nodes() if node.tag == "figure" and "data-slot" in node.attrs):
        name = figure.attrs["data-slot"]
        if name == "method-overview":
            continue
        captions = [node for node in figure.descendants() if node.tag == "figcaption"]
        require(len(captions) == 1, f"{name}: expected one caption")
        caption = captions[0]
        require(REIMPLEMENTATION_LABEL in caption.text(), f"{name}: missing exact re-implementation label")
        links = [node for node in caption.descendants() if node.tag == "a"]
        require(any(model in caption.text() for model in ("San Miguel 2.0", "Amazon Lumberyard Bistro", "Chess Set"))
                and any(urlsplit(node.attrs.get("href", "")).hostname in ATTRIBUTION_HOSTS for node in links),
                f"{name}: missing linked model attribution")
    require(not re.search(r"Coming soon", source, re.I), "page still says Coming soon")
    require(not re.search(r"identical", source, re.I), "page contains identical")
    applications = [node for node in page.nodes() if node.tag == "p"
                    and re.search(r"depth." r"of.field|\bd" r"of\b", node.text(), re.I)]
    require(len(applications) <= 1, "more than one applications paragraph")
    return "output captions include provenance and model credits; applications appear in at most one paragraph"


def site_files():
    return sorted(path for path in DOCS.rglob("*") if path.is_file() and "_dev" not in path.relative_to(DOCS).parts
                  and path.suffix.lower() in {".html", ".css", ".js", ".mjs", ".svg"})


def external(url):
    return bool(re.match(r"^(?:https?:)?//", url.strip(), re.I))


def check_resources():
    resources = {"script": "src", "link": "href", "img": "src", "iframe": "src", "source": "src"}
    js_urls = []
    for path in site_files():
        source = path.read_text(encoding="utf-8")
        for node in Document(source).nodes():
            attr = resources.get(node.tag)
            if attr:
                require(not external(node.attrs.get(attr) or ""), f"{path.name}: external {node.tag} {attr}")
            if node.tag == "a":
                href = node.attrs.get("href") or ""
                if external(href):
                    require(urlsplit(href).hostname in ALLOWED_HOSTS | ATTRIBUTION_HOSTS,
                            f"{path.name}: unapproved link {href}")
        css_urls = re.findall(r"url\(\s*['\"]?([^'\"\s)]+)", source, re.I)
        css_urls += re.findall(r"@import\s+['\"]([^'\"]+)", source, re.I)
        require(not any(external(url) for url in css_urls), f"{path.name}: external CSS URL/import")
        if path.suffix.lower() in {".js", ".mjs"}:
            js_urls.extend(re.findall(r"https?://[^\s'\"`<>]+", source))
            require(not re.search(r"['\"]//", source), f"{path.name}: protocol-relative JS URL")
            loads = re.findall(
                r"(?:\b(?:fetch|import|importScripts|Worker|SharedWorker|EventSource|WebSocket)\s*\(\s*"
                r"|\b(?:from|import)\s+|\.(?:src|srcset)\s*=\s*"
                r"|\.setAttribute\(\s*['\"](?:src|srcset)['\"]\s*,\s*)['\"`](https?://[^\s'\"`<>]+)", source)
            require(all(url.split("?", 1)[0] == VIDEO_URL for url in loads),
                    f"{path.name}: external JS resource {loads}")
    require(js_urls and all(url.split("?", 1)[0] == VIDEO_URL or url in JS_ATTRIBUTION_URLS
                            for url in js_urls), f"unapproved absolute JS URLs: {js_urls}")
    return "all initial resources local; attribution anchor hosts and click-to-load video allowed"


def forbidden(path):
    name = Path(path).name.lower()
    return (bool(re.search(r"\.(pdf|mp4|mov|webm|m4v|ppt.*|key)$", name))
            or name.startswith("2023-kim-siggraph-pvhv") or name in {"teaser.jpg", ".nojekyll"})


def check_files():
    paths = [str(path.relative_to(DOCS.parent)) for path in DOCS.rglob("*") if path.is_file()]
    tracked = subprocess.run(["git", "ls-files", "-z", "docs"], cwd=DOCS.parent, capture_output=True, check=False)
    if tracked.returncode == 0:
        paths.extend(tracked.stdout.decode("utf-8").split("\0"))
    require(not any(forbidden(path) for path in paths), f"forbidden files: {[path for path in paths if forbidden(path)]}")
    return "filesystem and git index contain no forbidden paper/media files or .nojekyll"


def check_note(page):
    text = collapse(page.by_id("reimplementation").text())
    for phrase in ["independent re-implementation", "2026", "not the code used for the paper's experiments"]:
        require(phrase in text, f"re-implementation note missing: {phrase}")
    return "independent 2026 re-implementation and experiment-code distinction stated"


def luminance(value):
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    channels = [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
    return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))


def check_contrast():
    tokens = dict(re.findall(r"--([\w-]+)\s*:\s*#([0-9a-fA-F]{3,6})\b", (DOCS / "assets/css/tokens.css").read_text()))
    pairs = [(fg, bg, 4.5) for fg in ("ink", "muted", "accent") for bg in ("bg", "surface", "note-tint")]
    pairs += [("accent-ink", "accent", 4.5), ("control", "surface", 3.0)]
    # Slot labels use ink/muted on surface, and their stripe also uses the checked bg.
    failures = []
    for fg, bg, minimum in pairs:
        first, second = sorted([luminance(tokens[fg]), luminance(tokens[bg])])
        ratio = (second + .05) / (first + .05)
        print(f"{'OK' if ratio >= minimum else 'FAIL'} contrast.{fg}/{bg}: {ratio:.2f}:1 (minimum {minimum:.1f}:1)")
        if ratio < minimum:
            failures.append(f"{fg}/{bg}")
    require(not failures, "contrast below threshold: " + ", ".join(failures))
    return "all text pairs including slot labels meet AA; control border meets 3:1"


def check_leftovers():
    pattern = re.compile(r"TODO|console\.log|debugger|elephant|QUASAR", re.I)
    paths = (path for path in DOCS.rglob("*") if path.is_file()
             and "_dev" not in path.relative_to(DOCS).parts
             and "fonts" not in path.relative_to(DOCS).parts
             and path.suffix.lower() in TEXT_SUFFIXES)
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if path.relative_to(DOCS).as_posix() == "assets/demo/ATTRIBUTION.md":
            source = source.replace("No model of the paper's own scenes (including the elephant) is used or redistributed here.", "")
        require(not pattern.search(source), f"leftover text in {path.relative_to(DOCS)}")
    return "no placeholder instructions, debug statements or unrelated project content"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", type=Path)
    parser.add_argument("--crossref", type=Path)
    args = parser.parse_args()
    try:
        source = (DOCS / "index.html").read_text(encoding="utf-8")
        page = Document(source)
        facts = json.loads((DOCS / "_dev/facts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"FAIL input: {error}\nSTATIC FAIL (1)")
        return 1
    checks = [
        ("facts", lambda: check_facts(page, facts)),
        ("sources", lambda: check_sources(args, facts)),
        ("slots", lambda: check_slots(page)),
        ("slots-filled", lambda: check_slots_filled(page)),
        ("captions", lambda: check_captions(page, source)),
        ("a11y", lambda: check_a11y(page)),
        ("external-resources", check_resources),
        ("forbidden-files", check_files),
        ("reimplementation", lambda: check_note(page)),
        ("contrast", check_contrast),
        ("leftovers", check_leftovers),
    ]
    failures = 0
    for name, check in checks:
        try:
            print(f"OK {name}: {check()}")
        except (AssertionError, OSError, ValueError, KeyError, IndexError) as error:
            failures += 1
            print(f"FAIL {name}: {error}")
    print(f"STATIC FAIL ({failures})" if failures else "STATIC OK")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
