#!/usr/bin/env python3
"""Check the static page and integration contracts using only the standard library."""

import argparse
import colorsys
import difflib
import hashlib
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
ALLOWED_HOSTS = {"doi.org", "dl.acm.org", "github.com"}
ATTRIBUTION_HOSTS = {"polyhaven.com", "creativecommons.org", "casual-effects.com", "developer.nvidia.com"}
JS_ATTRIBUTION_URLS = {"https://polyhaven.com/a/chess_set", "https://creativecommons.org/publicdomain/zero/1.0/"}
TEXT_SUFFIXES = {".html", ".css", ".js", ".mjs", ".json", ".md", ".svg", ".txt"}
REIMPLEMENTATION_LABEL = "Re-implementation (2026), not the code used for the paper's experiments."
METHOD_PROVENANCE = ("Diagrams drawn for this page after Figs. 2 and 3 and Sec. 4 of the paper "
                     "(Kim and Lee, ACM TOG 42(4), 2023); schematic, not to scale.")
MIN_PANEL_PX = 300   # narrowest method panel the auto-fit grid produces (minmax(300px, 1fr))
DEMO_NOTICE = "The interactive demo needs JavaScript and WebGL2 with float render targets."
MIN_LABEL_PX = 11
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
    citation = page.by_id("citation").text()
    equal(citation, facts["bibtex"], "bibtex")
    readme = (DOCS.parent / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^```bibtex\n(.*?)^```[ \t]*$", readme, re.M | re.S)
    require(len(blocks) == 1, "README must contain exactly one fenced BibTeX block")
    equal(blocks[0], citation, "README/page BibTeX")
    return "title, authors, affiliation, venue, DOI, abstract and BibTeX match facts.json; README BibTeX matches exactly"


def check_navigation(page, facts):
    rows = [node for node in page.nodes() if node.has_class("paper-links")]
    require(len(rows) == 1, "expected one resource row")
    anchors = [node for node in rows[0].descendants() if node.tag == "a"]
    expected = [
        ("Paper", "https://doi.org/10.1145/3592108"),
        ("Code", "https://github.com/janghunn/pvhv"),
        ("BibTeX", "#bibtex"),
    ]
    equal([(node.text().strip(), node.attrs.get("href")) for node in anchors], expected, "resource buttons")
    require(all(node.has_class("btn") for node in anchors),
            "each resource must be a kit button")
    require(anchors[0].has_class("primary"), "Paper must be the primary resource")
    for node in anchors[:2]:
        require(node.attrs.get("target") == "_blank"
                and set((node.attrs.get("rel") or "").split()) == {"noopener", "noreferrer"},
                f"{node.text()}: external resource must open safely in a new tab")
    for key, index in (("doi_url", 0), ("code_url", 1)):
        equal(facts[key], expected[index][1], key)
    nav = [node for node in page.nodes() if node.tag == "nav" and node.attrs.get("aria-label") == "Sections"]
    require(len(nav) == 1, "expected one section navigation")
    equal([node.text().strip() for node in nav[0].descendants() if node.tag == "a"],
          ["Overview", "Method", "Demo", "Results", "BibTeX"], "section navigation")
    menu = page.by_id("menu-toggle")
    require(menu.tag == "button" and menu.attrs.get("type") == "button" and "hidden" in menu.attrs
            and menu.attrs.get("aria-expanded") == "false" and menu.attrs.get("aria-controls") == "section-nav",
            "menu toggle must be a hidden button[aria-expanded=false][aria-controls=section-nav] until JS enables it")
    require(nav[0].attrs.get("id") == "section-nav" and "hidden" not in nav[0].attrs,
            "section links must stay visible without JavaScript")
    return ("exact Paper/Code/BibTeX resources and Overview/Method/Demo/Results/BibTeX navigation; "
            "Menu is progressive (links visible without JS)")


def metadata_text(value):
    if isinstance(value, list):
        require(len(value) == 1, "expected one metadata value")
        value = value[0]
    return str(value)


def check_work(work, facts, fields, label, journal_names):
    equal(metadata_text(work["title"]), facts["title"], f"{label}/title")
    authors = work["author"]
    require(authors, f"{label} has no authors")
    names = " and ".join(f"{author['given']} {author['family']}" for author in authors)
    equal(names, facts["authors"], f"{label}/ordered authors")
    doi = urlsplit(facts["doi_url"]).path.lstrip("/")
    equal(work["DOI"], doi, f"{label}/DOI")
    equal(fields["doi"], doi, f"{label}/BibTeX DOI")
    journal = metadata_text(work["container-title"])
    require(journal in journal_names, f"{label}: unexpected journal {journal}")
    journal = journal_names[journal]
    equal(journal, "ACM Transactions on Graphics", f"{label}/journal")
    for key, bib_key, expected in (("volume", "volume", "42"), ("issue", "number", "4")):
        equal(str(work[key]), expected, f"{label}/{key}")
        equal(fields[bib_key], expected, f"{label}/BibTeX {bib_key}")
    year = str(work["issued"]["date-parts"][0][0])
    equal(year, "2023", f"{label}/year")
    equal(fields["year"], year, f"{label}/BibTeX year")
    abstract = unescape(re.sub(r"</?jats:[^>]+>", "", work["abstract"]))
    equal(collapse(abstract), collapse(facts["abstract"]), f"{label}/abstract")
    venue = f"{journal} {work['volume']}({work['issue']}), Article {fields['articleno']}, {fields['issue_date']}"
    require(facts["venue"] in (venue, venue + " (SIGGRAPH 2023)"), f"{label}: venue differs from metadata")


def check_sources(args, facts, page):
    checked = []
    fields = dict(re.findall(r"^(\w+) = \{(.*)\},?$", facts["bibtex"], re.M))
    if args.crossref:
        data = json.loads(args.crossref.read_text(encoding="utf-8"))
        work = data.get("message", data)
        check_work(work, facts, fields, "Crossref", {"ACM Transactions on Graphics": "ACM Transactions on Graphics"})
        for index, author in enumerate(work["author"], 1):
            affiliations = author["affiliation"]
            require(affiliations, f"Crossref author {index} has no affiliations")
            for affiliation in affiliations:
                equal(affiliation["name"], facts["affiliation"], f"Crossref author {index} affiliation")
        checked.append("Crossref metadata and every author affiliation match")
    if args.acm:
        items = json.loads(args.acm.read_text(encoding="utf-8"))
        doi = urlsplit(facts["doi_url"]).path.lstrip("/")
        require(len(items) == 1 and set(items[0]) == {doi}, "expected one ACM citation item for the paper DOI")
        work = items[0][doi]
        check_work(work, facts, fields, "ACM", {"ACM Trans. Graph.": "ACM Transactions on Graphics"})
        equal(str(work["collection-number"]), "86", "ACM/article number")
        require(f"Article {work['collection-number']}" in facts["venue"], "ACM article number missing from venue")
        equal(str(work["number-of-pages"]), "11", "ACM/page count")
        for key, bib_key in (("collection-number", "articleno"), ("number-of-pages", "numpages"),
                             ("ISSN", "issn"), ("URL", "url")):
            equal(str(work[key]), fields[bib_key], f"ACM/BibTeX {bib_key}")
        checked.append("ACM metadata, article/page counts, ISSN and URL match")
    if args.acm_bib:
        bibtex = args.acm_bib.read_text(encoding="utf-8")
        require(bibtex.startswith("@article{10.1145/3592108,\n"), "unexpected ACM BibTeX key line")
        expected = bibtex.replace("@article{10.1145/3592108,", "@article{kim2023pvhv,", 1)
        equal(page.by_id("citation").text(), expected, "ACM/BibTeX (only citation key changed)")
        checked.append("ACM BibTeX matches byte-for-byte except the citation key")
    return "; ".join(checked) or "no optional source files supplied"


def check_demo_notice(page):
    boxes = [node for node in page.nodes() if node.has_class("slot-box")]
    require(len(boxes) == 1, "expected exactly one no-JavaScript demo notice")
    box = boxes[0]
    require(box in page.by_id("demo-root").children and box.attrs.get("role") == "note",
            "demo no-JavaScript notice must be a direct child with role=note")
    children = [node for node in box.children if isinstance(node, Node)]
    require(len(children) == 1 and children[0].tag == "p", "demo notice must contain one paragraph")
    equal(box.text(), DEMO_NOTICE, "demo no-JavaScript notice")
    require(re.search(r"[가-힣]", children[0].attrs.get("data-ko") or ""), "demo notice needs Korean text")


def check_slots(page):
    check_demo_notice(page)
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
        sources, aspects = src.split(), aspect.split()
        require(len(sources) == len(aspects), f"{name}: data-src and data-aspect list lengths differ")
        equal(rows[name][1], ", ".join("docs/" + item for item in sources), f"{name} expected file")
        equal(rows[name][2], ", ".join(aspects), f"{name} aspect")
        descendants = list(slot.descendants())
        if name != "demo":
            captions = [node for node in descendants if node.tag == "figcaption"]
            require(len(captions) == 1, f"{name}: expected one figcaption")
            equal(captions[0].text().strip(), rows[name][3], f"{name} caption")
        boxes = [node for node in descendants if node.has_class("slot-box")]
        if boxes:
            require(name == "demo", f"{name}: unexpected placeholder")
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
    check_demo_notice(page)
    demo_nodes = set(page.by_id("demo-root").descendants())
    require(not any(node.has_class("slot-box") and node not in demo_nodes for node in page.nodes()),
            "slot-box outside #demo-root")
    for figure in (node for node in page.nodes() if node.tag == "figure" and "data-slot" in node.attrs):
        name = figure.attrs["data-slot"]
        sources, aspects = figure.attrs.get("data-src", "").split(), figure.attrs.get("data-aspect", "").split()
        images = [node for node in figure.descendants() if node.tag == "img"]
        equal([image.attrs.get("src") for image in images], sources, f"{name} image sources (in order)")
        for image, aspect in zip(images, aspects):
            path = (DOCS / image.attrs["src"]).resolve()
            require(path.is_relative_to(DOCS) and path.is_file(), f"{name}: missing or nonlocal image")
            dimensions = image_size(path)
            actual = tuple(int(image.attrs.get(key, "0")) for key in ("width", "height"))
            equal(actual, dimensions, f"{name} image dimensions")
            ratio = tuple(int(part) for part in aspect.split(":"))
            require(ratio[0] * dimensions[1] == ratio[1] * dimensions[0],
                    f"{name}: aspect {aspect} does not match {dimensions[0]}x{dimensions[1]}")
    return ("all figure images exist in data-src order with exact dimensions and declared aspects; "
            "only the no-JavaScript demo notice remains")


def svg_label_sizes(path):
    """Effective font sizes (viewBox units) of every text run, and the viewBox width."""
    root = ET.fromstring(path.read_bytes())
    width = float(root.attrib["viewBox"].split()[2])
    sizes = []

    def walk(node, inherited):
        size = node.attrib.get("font-size")
        match = re.search(r"font-size\s*:\s*([\d.]+)", node.attrib.get("style", ""))
        size = float(match.group(1)) if match else float(size) if size else inherited
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"text", "tspan"} and (node.text or "").strip():
            sizes.append(size)
        for child in node:
            walk(child, size)
            if tag in {"text", "tspan"} and (child.tail or "").strip():
                sizes.append(size)
    walk(root, 16.0)
    return sizes, width


def check_method_figure(page):
    figure = next(node for node in page.nodes() if node.attrs.get("data-slot") == "method-overview")
    sources = figure.attrs["data-src"].split()
    require(len(sources) == 3 and all(re.fullmatch(r"assets/img/method-[\w-]+\.svg", src) for src in sources),
            "method-overview must hold three local method-*.svg panels")
    require(not (DOCS / "assets/img/method-overview.svg").exists(), "superseded method-overview.svg still present")
    caps = [node.text().strip() for node in figure.descendants() if node.has_class("panel-cap")]
    require(len(caps) == 3 and [cap[:3] for cap in caps] == ["(a)", "(b)", "(c)"],
            "method panels need sub-captions (a), (b), (c)")
    caption = collapse(next(node for node in figure.descendants() if node.tag == "figcaption").text())
    require(caption.endswith(METHOD_PROVENANCE), "method caption must end with the provenance sentence")
    smallest = []
    for src in sources:
        path = DOCS / src
        root = ET.fromstring(path.read_bytes())
        names = {child.tag.rsplit("}", 1)[-1] for child in root}
        require({"title", "desc"} <= names, f"{path.name}: needs <title> and <desc>")
        sizes, width = svg_label_sizes(path)
        require(sizes, f"{path.name}: no text found")
        px = min(sizes) * MIN_PANEL_PX / width
        print(f"{'OK' if px >= MIN_LABEL_PX else 'FAIL'} method-label.{path.name}: smallest text {min(sizes):g}/{width:g} "
              f"viewBox units = {px:.2f} CSS px at a {MIN_PANEL_PX} px panel (minimum {MIN_LABEL_PX})")
        smallest.append(px)
    require(min(smallest) >= MIN_LABEL_PX, "method label below the legibility minimum")
    return "three titled panels with (a)-(c) sub-captions, provenance, and every label legible at a 300 px panel"


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
    require(len(applications) == 1, "expected exactly one applications paragraph")
    paragraph = applications[0]
    require("Applications of the method, such as depth-of-field rendering, are described in the" in paragraph.text(),
            "applications paragraph missing the depth-of-field pointer")
    require(any(node.tag == "a" and node.attrs.get("href") == "https://doi.org/10.1145/3592108"
                for node in paragraph.descendants()), "applications paragraph must link to the paper DOI")
    return "output captions include provenance and model credits; exactly one applications paragraph links to the paper"


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
            # The kit URL builder is pinned by check_kit; every other resource rule still applies.
            if path.relative_to(DOCS).as_posix() != "assets/js/kit.js":
                require(not re.search(r"['\"]//", source), f"{path.name}: protocol-relative JS URL")
            loads = re.findall(
                r"(?:\b(?:fetch|import|importScripts|Worker|SharedWorker|EventSource|WebSocket)\s*\(\s*"
                r"|\b(?:from|import)\s+|\.(?:src|srcset)\s*=\s*"
                r"|\.setAttribute\(\s*['\"](?:src|srcset)['\"]\s*,\s*)['\"`](https?://[^\s'\"`<>]+)", source)
            require(not loads, f"{path.name}: external JS resource {loads}")
    require(all(url in JS_ATTRIBUTION_URLS for url in js_urls), f"unapproved absolute JS URLs: {js_urls}")
    return "all resources local; only approved attribution URLs allowed in JavaScript"


def check_forbidden_references():
    pattern = re.compile("|".join([
        "cg" + r"\.skku", "cg" + "skku", "you" + "tube", "youtu" + r"\.be",
        "slide" + "s",
        "graphics" + r"\s+lab",
    ]), re.I)
    paths = sorted(path for path in DOCS.rglob("*") if path.is_file()
                   and "__pycache__" not in path.relative_to(DOCS).parts)
    paths.append(DOCS.parent / "README.md")
    for path in paths:
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        require(not pattern.search(source), f"forbidden resource reference in {path.relative_to(DOCS.parent)}")
    return "served files, development checks and README contain no forbidden resource references"


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


def declarations(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", source))


def palettes():
    source = (DOCS / "assets/css/tokens.css").read_text(encoding="utf-8")
    blocks = [re.search(pattern, source) for pattern in (
        r":root\s*\{([^}]+)\}",
        r':root\[data-theme="dark"\]\s*\{([^}]+)\}',
    )]
    require(all(blocks), "tokens must define light and dark palettes")
    for block, expected in zip(blocks, ("light", "dark")):
        scheme = re.search(r"color-scheme\s*:\s*([^;]+);", block.group(1))
        require(scheme and scheme.group(1).strip() == expected, f"palette must declare color-scheme: {expected}")
    light, dark = [declarations(block.group(1)) for block in blocks]
    return light, {**light, **dark}


def colour(value):
    if value.startswith("#"):
        value = value[1:]
        if len(value) == 3:
            value = "".join(char * 2 for char in value)
        return [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)] + [1.0]
    require(bool(re.fullmatch(r"rgba?\([\d.,\s]+\)", value)), f"unsupported colour {value}")
    channels = [float(item) for item in re.findall(r"[\d.]+", value)]
    return [item / 255 for item in channels[:3]] + [channels[3] if len(channels) == 4 else 1.0]


def luminance(channels):
    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
              for channel in channels[:3]]
    return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))


def check_contrast():
    light, dark = palettes()
    pairs = [(fg, bg, 4.5) for fg in ("ink", "muted") for bg in ("bg", "surface", "code-bg")]
    pairs += [("accent", bg, 4.5) for bg in ("bg", "surface")]
    pairs += [("on-accent", "accent", 4.5), ("on-band", "band", 4.5)]
    pairs += [(fg, bg, 3.0) for fg in ("control", "focus", "accent") for bg in ("bg", "surface")]
    failures = []
    for theme, tokens in (("light", light), ("dark", dark)):
        for fg, bg, minimum in pairs:
            first, second = sorted([luminance(colour(tokens[fg])), luminance(colour(tokens[bg]))])
            ratio = (second + .05) / (first + .05)
            print(f"{'OK' if ratio >= minimum else 'FAIL'} contrast.{theme}.{fg}/{bg}: "
                  f"{ratio:.2f}:1 (minimum {minimum:.1f}:1)")
            if ratio < minimum:
                failures.append(f"{theme}.{fg}/{bg}")
        hue = colorsys.rgb_to_hls(*colour(tokens["accent"])[:3])[0] * 360
        require(80 <= hue <= 150, f"{theme}.accent hue {hue:.1f} outside 80-150")
        print(f"OK {theme}.accent-hue: {hue:.1f} degrees (80-150)")
    require(not failures, "contrast below threshold: " + ", ".join(failures))
    return "both Paper & Lamp palettes meet AA for page text; controls and focus meet 3:1; accent is pine green"


def check_kit(page, kit):
    # Kit revision 208aa74; only the two accent blocks differ in this project.
    pins = {
        "assets/css/base.css": "858e7596fd4b392a0fb55ebd01e1b29c73dd5dc1a58e0f3ad8fff4c466ff6a8e",
        "assets/css/components.css": "3ed8014941e4e8218e62746bf6fc15fcce40972c97dc14d75b3311f62cc43a9e",
        "assets/css/accents.css": "502dc3dc7fb3f0debb0764bb1711410ed057a35daf4689efde7d8fa13ae170f1",
        "assets/js/kit.js": "7bed21735f2833fbbd2c261c5460e6ac5f1c85945f5995efae9326cb10838b1d",
        "assets/fonts/LICENSE-S-Core-Dream.png": "6b706afa8929b2b63b46276ac862da5ab7a3f79da1256b73057cd6f50baf2257",
        "assets/fonts/LICENSE-S-Core-Dream.txt": "f7513c3c137e13393bc87110670b9b2cec8e0e9d3ff57ad4f8fa06122b2dc2f8",
        "assets/fonts/Lato-400.woff2": "277c2401b516d71dbd053a084246260194d8f22939fb7b63e99b1e6fb28a249b",
        "assets/fonts/Lato-600.woff2": "391e7f9587ae937e5edf4c5d71f8f4f561d28165500e056c45fd952eade36c8e",
        "assets/fonts/Lato-700.woff2": "30c9de7848e9d05f432bff7fef4920486ac3ed0708ecd40f3a14cc6049ca9af2",
        "assets/fonts/Lato-900.woff2": "17571df6ac98e36ad8f4af4739fe63f8225ad244e016d0c8b81f9f4e4f52d6dc",
        "assets/fonts/OFL-Lato.txt": "d9f3a36641a31c7a69dec329b119bb9c80542cfe6eec356008c81c0416f5b5cd",
        "assets/fonts/SCDream4.otf": "1dc288e8cfdf96eb083c0b016fb5f107bc83208e748b6a61422404b9e42a95a4",
        "assets/fonts/SCDream6.otf": "7dd3e0240002feedd37d0f33033a44e17b1ab11f84a93acf8482184adc33aa1a",
        "assets/fonts/SHA256SUMS": "7505e09c87e723fe2907ee6f80f0feeb44aaf6cda22a026f9fd50acae1c6dec3",
        "assets/fonts/fonts.css": "3248dd0d49b3246c4a63fe13e6558c6b742c834162e3ce4c22f38bd02150d6b5",
    }
    for name, digest in pins.items():
        data = (DOCS / name).read_bytes()
        equal(hashlib.sha256(data).hexdigest(), digest, f"kit hash/{name}")
        if kit:
            equal(data, (kit / name).read_bytes(), f"kit bytes/{name}")
    fonts = DOCS / "assets/fonts"
    for line in (fonts / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split()
        equal(hashlib.sha256((fonts / name).read_bytes()).hexdigest(), digest, f"font checksum/{name}")
    tokens = (DOCS / "assets/css/tokens.css").read_text(encoding="utf-8")
    equal(hashlib.sha256(tokens.encode()).hexdigest(), "511c4ae16e7347da498556c0b75a6e2fc1374ae914b9644063cbf8e88069dba5", "pine tokens hash")
    if kit:
        expected = (kit / "assets/css/tokens.css").read_text(encoding="utf-8")
        accents = (kit / "assets/css/accents.css").read_text(encoding="utf-8")
        pine = re.findall(r'^.*?\[data-accent="pine"\]\{([^}]+)\}', accents, re.M)
        require(len(pine) == 2, "kit must define both pine palettes")
        for block, values in zip(("ACCENT", "ACCENT-DARK"), pine):
            roles = declarations(values)
            pattern = rf"(/\* === {block}:.*?\*/)(.*?)(/\* === /{block} === \*/)"
            def set_values(match):
                body = re.sub(r"--([\w-]+):([^;]+);", lambda m: "--" + m[1] + ":" + roles[m[1]] + ";", match[2])
                return match[1] + body + match[3]
            expected = re.sub(pattern, set_values, expected, flags=re.S)
        equal(tokens, expected, "kit tokens with pine accent blocks")
    html = next(node for node in page.nodes() if node.tag == "html")
    require(html.attrs.get("lang") == "en" and not any(key.startswith("data-theme") for key in html.attrs),
            "paper must start in English without data-theme attributes")
    head = next(node for node in page.nodes() if node.tag == "head")
    children = [node for node in head.children if isinstance(node, Node)]
    require(len(children) >= 2 and children[0].tag == "meta" and children[0].attrs == {"charset": "utf-8"}
            and children[1].tag == "script" and not children[1].attrs,
            "the kit pre-paint script must immediately follow the charset")
    equal(hashlib.sha256(children[1].text().encode()).hexdigest(),
          "8ee7c3efad15f477b10620ee1a9407a2808536d5aa0ce1e990ae605c42da4f27", "pre-paint script hash")
    if kit:
        template = (kit / "project.html").read_text(encoding="utf-8")
        equal(children[1].text(), re.search(r"<script>(.*?)</script>", template, re.S)[1], "kit pre-paint script")
    for attr in ("data-theme-toggle", "data-lang-toggle"):
        require(sum(node.tag == "button" and attr in node.attrs for node in page.nodes()) == 1,
                f"expected one button[{attr}]")
    require(any(node.tag == "p" and "data-ko-only" in node.attrs and "hidden" in node.attrs
                and node.attrs.get("lang") == "ko"
                and node.text() == "이 페이지에는 에스코어가 제공한 에스코어 드림 폰트가 적용되어 있습니다."
                for node in page.nodes()), "exact Korean font credit missing")
    equal([node.attrs.get("href") for node in children if node.tag == "link" and node.attrs.get("rel") == "stylesheet"],
          [f"assets/css/{name}.css" for name in ("tokens", "accents", "base", "components", "site")], "stylesheet order")
    scripts = [node for node in children if node.tag == "script" and "src" in node.attrs]
    equal([node.attrs["src"] for node in scripts], [f"assets/js/{name}.js" for name in ("kit", "main", "demo")], "script order")
    require(all("defer" in node.attrs for node in scripts), "external scripts must be deferred")
    return "Paper & Lamp assets match the kit (pine accent); kit pre-paint script, theme and language toggles and Korean font credit present"


def check_masthead(page, facts):
    top = page.by_id("top")
    require(top.has_class("masthead"), "#top must be a kit masthead")
    nodes = list(top.descendants())
    headings = [node for node in page.nodes() if node.tag == "h1"]
    require(len(headings) == 1 and headings[0] in nodes, "masthead must hold the one h1")
    title_nodes = [headings[0], *headings[0].descendants()]
    brands = [node for node in title_nodes if node.has_class("t-brand")]
    require(len(brands) == 1 and brands[0].text() == "PVHV", "h1 must hold one PVHV brand")
    require(page.by_id("title") in title_nodes and page.by_id("title").has_class("t-sub"), "title must be the kit subtitle")
    require(all("data-ko" not in node.attrs and "data-html" not in node.attrs
                for node in [*title_nodes, page.by_id("authors")]), "title and author names must stay English")
    badges = [node for node in page.nodes() if node.has_class("venue")]
    brands = [node for node in nodes if node.has_class("brand-block")]
    require(len(badges) == len(brands) == 1 and badges[0] in list(brands[0].descendants()),
            "expected one venue line in the masthead brand block")
    def visible_text(node):
        if node.has_class("vh") or node.has_class("visually-hidden") or node.attrs.get("aria-hidden") == "true" or "hidden" in node.attrs:
            return ""
        return "".join(visible_text(child) if isinstance(child, Node) else child for child in node.children)
    equal(visible_text(badges[0]), facts["venue"], "visible venue line")
    equal(page.by_id("venue").attrs.get("data-ko"),
          "ACM Transactions on Graphics 42(4), Article 86, 2023년 8월 (SIGGRAPH 2023)", "Korean venue line")
    require(page.by_id("venue") in set(badges[0].descendants()), "venue fact must sit inside the single venue line")
    require(all(phrase in facts["venue"] for phrase in ("ACM Transactions on Graphics 42(4)", "SIGGRAPH 2023")),
            "venue must name both journal and proceedings as one publication")
    require(not any(any(node.has_class(name) for name in ("hero-glow", "org-logo", "mascot", "ph"))
                    for node in page.nodes()), "obsolete masthead decoration or placeholders")
    flags = [node for node in nodes if node.has_class("reimpl-flag")]
    require(len(flags) == 1, "expected one re-implementation flag")
    equal(flags[0].text(), "Code, figures and demo on this page: independent 2026 re-implementation — details.",
          "re-implementation flag")
    require(any(node.tag == "a" and node.attrs.get("href") == "#reimplementation"
                for node in flags[0].descendants()), "flag must link to the re-implementation note")
    require("재구현" in (flags[0].attrs.get("data-ko") or ""), "flag needs Korean re-implementation text")
    return "PVHV brand title, one complete venue line in the brand block, exact re-implementation flag; no placeholders"


def check_no_logos(page):
    top = page.by_id("top")
    nodes = list(top.descendants())
    require(not any(node.tag == "img" for node in nodes), "masthead must contain no image elements")
    require(not any("logo" in (node.attrs.get("class") or "").lower() for node in page.nodes()),
            "page must contain no element with a logo class")
    require(not any("logo" in (node.attrs.get(attr) or "").lower()
                    for node in page.nodes() if node.tag in {"img", "source"}
                    for attr in ("src", "srcset")), "page image sources must contain no logo references")
    bylines = [node for node in nodes if node.has_class("byline")]
    resources = [node for node in nodes if node.has_class("paper-links")]
    parent = next((node for node in [top, *nodes] if bylines and bylines[0] in node.children), None)
    siblings = [node for node in parent.children if isinstance(node, Node)] if parent else []
    require(len(bylines) == len(resources) == 1
            and siblings.index(bylines[0]) + 1 < len(siblings)
            and siblings[siblings.index(bylines[0]) + 1] is resources[0],
            "masthead byline must be followed directly by the resource row")
    require(not any(path.is_file() and "logo" in path.name.lower()
                    for path in (DOCS / "assets/img").rglob("*")), "image assets must contain no logo files")
    require(not re.search(r"\.logo", (DOCS / "assets/css/site.css").read_text(encoding="utf-8"), re.I),
            "page CSS must contain no logo selectors")
    return "masthead has no logo row or logo image; byline is followed directly by the resource row; no logo files or styles"


def check_zoom(page):
    expected = {"teaser": (960, 24), "results-keep-cull": (1560, 15), "results-comparison": (1560, 15)}
    buttons = [node for node in page.nodes() if "data-zoom" in node.attrs]
    require(len(buttons) == 3, "expected exactly three raster zoom buttons")
    for slot in (node for node in page.nodes() if "data-slot" in node.attrs):
        name = slot.attrs["data-slot"]
        controls = [node for node in slot.descendants() if "data-zoom" in node.attrs]
        require(len(controls) == (1 if name in expected else 0), f"{name}: unexpected zoom controls")
        if name not in expected:
            continue
        button = controls[0]
        require(button.tag == "button" and button.attrs.get("type") == "button"
                and "hidden" in button.attrs and button.attrs.get("aria-pressed") == "false"
                and button.has_class("zoom-toggle"), f"{name}: zoom must start hidden and unpressed")
        equal(button.text(), "Zoom to read labels", f"{name} zoom label")
        width, label = expected[name]
        equal((int(button.attrs.get("data-zoom-width", "0")), int(button.attrs.get("data-label-px", "0"))),
              (width, label), f"{name} zoom scale")
        children = [node for node in slot.children if isinstance(node, Node)]
        index = children.index(button)
        require(index > 0 and index + 1 < len(children) and children[index - 1].has_class("figure-frame")
                and children[index + 1].tag == "figcaption", f"{name}: zoom button must follow frame before caption")
        image = next(node for node in children[index - 1].descendants() if node.tag == "img")
        natural_width = image_size(DOCS / image.attrs["src"])[0]
        require(width / natural_width * label >= 11, f"{name}: enlarged raster labels fall below 11 CSS px")
    return "only the three raster figures have progressive zoom controls; labels reach at least 11 CSS px"


def check_i18n(page):
    nodes = page.nodes()
    for node in nodes:
        for attr in ("data-ko", "data-ko-label", "data-ko-title", "data-ko-alt"):
            if attr in node.attrs:
                require(re.search(r"[가-힣]", node.attrs[attr] or ""), f"{node.tag}[{attr}] needs nonempty Korean text")
        if node.tag == "img":
            require("data-ko-alt" in node.attrs, f"{node.attrs.get('src')}: missing data-ko-alt")
        if "aria-label" in node.attrs:
            require("data-ko-label" in node.attrs, f"{node.tag}: missing data-ko-label")
        if "title" in node.attrs:
            require("data-ko-title" in node.attrs, f"{node.tag}: missing data-ko-title")
    required = set()
    for node in nodes:
        if node.tag == "nav":
            required.update(n for n in node.descendants() if n.tag == "a" and n.text() != "BibTeX")
        if node.tag in {"figcaption", "li"} or node.has_class("panel-cap") or (node.tag == "h2" and node.text() != "BibTeX"):
            required.add(node)
        if node.has_class("band") or node.attrs.get("id") == "reimplementation" or node.tag == "footer":
            required.update(n for n in node.descendants() if n.tag == "p" and "data-ko-only" not in n.attrs)
        if "data-zoom" in node.attrs:
            parent = next(n for n in nodes if node in n.children)
            required.update(n for n in parent.children if isinstance(n, Node) and n.tag == "figcaption")
    for node in required:
        require("data-ko" in node.attrs, f"{node.tag} {node.text()[:60]!r}: missing data-ko")
    abstract = page.by_id("abstract-text")
    require(abstract.attrs.get("lang") == "en" and "data-ko" not in abstract.attrs, "abstract must stay English")
    korean = [node for node in page.by_id("abstract").descendants()
              if node.tag == "p" and "data-ko-only" in node.attrs and "hidden" in node.attrs and node.attrs.get("lang") == "ko"]
    require(any(node.has_class("note") and "번역" in node.text() for node in korean), "Korean abstract needs a translation note")
    require(any(not node.has_class("note") and re.search(r"[가-힣]", node.text()) for node in korean), "Korean abstract missing")
    untranslated = [page.by_id("authors"), page.by_id("citation"), next(n for n in nodes if n.tag == "h1")]
    for node in untranslated:
        require(all("data-ko" not in n.attrs and "data-html" not in n.attrs for n in [node, *node.descendants()]),
                "title, authors and BibTeX must stay English")
    return "every page string has a Korean counterpart; title, authors, abstract and BibTeX stay English; Korean abstract labelled as a translation"


def check_style_files():
    fonts = DOCS / "assets/fonts"
    expected_fonts = {"Lato-400.woff2", "Lato-600.woff2", "Lato-700.woff2", "Lato-900.woff2",
                      "SCDream4.otf", "SCDream6.otf", "OFL-Lato.txt", "LICENSE-S-Core-Dream.txt",
                      "LICENSE-S-Core-Dream.png", "SHA256SUMS", "fonts.css"}
    require(fonts.is_dir(), "bundled fonts directory missing")
    equal({path.name for path in fonts.iterdir()}, expected_fonts, "bundled font files")
    font_css = (fonts / "fonts.css").read_text(encoding="utf-8")
    urls = re.findall(r"url\(['\"]?([^'\")]+)", font_css)
    require(urls and all((fonts / url).is_file() and (fonts / url).resolve().is_relative_to(fonts)
                         for url in urls), "fonts.css must reference local font files only")
    readme = (DOCS.parent / "README.md").read_text(encoding="utf-8")
    require("Lato" in readme and "S-Core Dream" in readme, "README must name both bundled fonts")
    tokens = declarations((DOCS / "assets/css/tokens.css").read_text(encoding="utf-8"))
    for name in ("assets/css/components.css", "assets/css/site.css", "assets/js/demo.js", "assets/js/demo/app.js"):
        source = (DOCS / name).read_text(encoding="utf-8")
        defined = set(tokens) | set(declarations(source))
        used = set(re.findall(r"var\(\s*--([\w-]+)", source))
        require(used <= defined, f"{name}: undefined tokens {sorted(used - defined)}")
    demo = (DOCS / "assets/js/demo.js").read_text(encoding="utf-8")
    require(not re.search(r"var\(\s*--[\w-]+\s*,", demo), "demo CSS contains token fallbacks")
    demo_readme = (DOCS.parent / "demo/README.md").read_text(encoding="utf-8")
    used_demo_tokens = set(re.findall(r"var\(\s*(--[\w-]+)", demo))
    documented_tokens = set(re.findall(r"--[\w-]+", demo_readme))
    require(used_demo_tokens <= documented_tokens,
            f"demo README lacks host tokens: {sorted(used_demo_tokens - documented_tokens)}")
    for name in ("css/site.css", "js/main.js", "js/demo.js", "js/demo/app.js"):
        source = (DOCS / "assets" / name).read_text(encoding="utf-8")
        require(not re.search(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(", source), f"{name}: raw colour literal")
    old_names = r"--(?:fg|card|bg2|line-strong|accent-[abc]|radius-[sm]|glow-[\w-]+|sp-[\w-]+|fs-[\w-]+)(?![\w-])"
    harness = DOCS.parent / "demo/harness.html"
    copies = {"assets/css/tokens.css", "assets/css/base.css", "assets/css/components.css", "assets/css/accents.css", "assets/js/kit.js"}
    files = [path for path in site_files() if path.suffix in {".css", ".js", ".mjs"}
             and path.relative_to(DOCS).as_posix() not in copies]
    for path in [*files, harness]:
        require(not re.search(old_names, path.read_text(encoding="utf-8")),
                f"obsolete token in {path.relative_to(DOCS.parent)}")
    light, _ = palettes()
    harness_tokens = declarations(harness.read_text(encoding="utf-8"))
    equal(set(harness_tokens), set("bg surface ink muted line control accent focus r-s card-r maxw".split()),
          "harness token roles")
    for name, value in harness_tokens.items():
        equal(value, light[name], f"harness light token/{name}")
    return "bundled Lato and S-Core Dream fonts, resolved Paper & Lamp tokens, demo colours without fallbacks and matching light harness values"


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
    parser.add_argument("--crossref", type=Path)
    parser.add_argument("--acm", type=Path)
    parser.add_argument("--acm-bib", type=Path)
    parser.add_argument("--kit", type=Path)
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
        ("navigation", lambda: check_navigation(page, facts)),
        ("sources", lambda: check_sources(args, facts, page)),
        ("slots", lambda: check_slots(page)),
        ("slots-filled", lambda: check_slots_filled(page)),
        ("captions", lambda: check_captions(page, source)),
        ("method-figure", lambda: check_method_figure(page)),
        ("a11y", lambda: check_a11y(page)),
        ("external-resources", check_resources),
        ("forbidden-references", check_forbidden_references),
        ("forbidden-files", check_files),
        ("reimplementation", lambda: check_note(page)),
        ("kit", lambda: check_kit(page, args.kit)),
        ("masthead", lambda: check_masthead(page, facts)),
        ("no-logos", lambda: check_no_logos(page)),
        ("zoom", lambda: check_zoom(page)),
        ("i18n", lambda: check_i18n(page)),
        ("style-files", check_style_files),
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
