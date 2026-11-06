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
        ("PDF", "https://dl.acm.org/doi/pdf/10.1145/3592108"),
        ("Code", "https://github.com/janghunn/pvhv"),
        ("BibTeX", "#bibtex"),
    ]
    equal([(node.text().strip(), node.attrs.get("href")) for node in anchors], expected, "resource buttons")
    require(all(node.has_class("btn") and node.has_class("btn-pill") for node in anchors),
            "each resource must be a kit link pill")
    equal(anchors[1].attrs.get("title"), "PDF from the ACM Digital Library (open access)", "PDF title")
    for node in anchors[:3]:
        require(node.attrs.get("target") == "_blank"
                and set((node.attrs.get("rel") or "").split()) == {"noopener", "noreferrer"},
                f"{node.text()}: external resource must open safely in a new tab")
    for key, index in (("doi_url", 0), ("pdf_url", 1), ("code_url", 2)):
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
    return ("exact Paper/PDF/Code/BibTeX resources and Overview/Method/Demo/Results/BibTeX navigation; "
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
            "only the runtime demo placeholder remains")


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
        r"@media\s*\(prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme\]\)\s*\{([^}]+)\}",
    )]
    require(all(blocks), "tokens must define light, dark and OS dark palettes")
    light, dark, media = [block.group(1) for block in blocks]
    for block, expected in ((light, "light"), (dark, "dark"), (media, "dark")):
        scheme = re.search(r"color-scheme\s*:\s*([^;]+);", block)
        require(scheme and scheme.group(1).strip() == expected, f"palette must declare color-scheme: {expected}")
    return declarations(light), declarations(dark), declarations(media)


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
    light, dark, media = palettes()
    require(dark.keys() <= light.keys(), "dark palette defines a key absent from light")
    palette_roles = set("bg bg2 card line line-strong fg muted accent-a accent-b accent-c warn on-accent "
                        "btn-bg btn-fg code-bg code-fg focus selection glow-a glow-b glow-opacity grid-dot "
                        "tint shadow-1 shadow-2".split())
    require(dark.keys() <= palette_roles, "dark block must override palette roles only")
    equal(media, dark, "OS dark and explicit dark palettes")
    pairs = [(fg, bg, 4.5) for fg in ("fg", "muted", "accent-a", "accent-b", "accent-c")
             for bg in ("bg", "bg2", "card")]
    pairs += [("btn-fg", "btn-bg", 4.5), ("code-fg", "code-bg", 4.5), ("muted", "code-bg", 4.5)]
    pairs += [(fg, bg, 3.0) for fg in ("line-strong", "focus") for bg in ("bg", "bg2", "card")]
    required = {role for fg, bg, _ in pairs for role in (fg, bg)} | {"glow-a", "glow-b", "glow-opacity"}
    require(required <= dark.keys(), "dark block lacks a tested palette role")
    failures = []
    for theme, tokens in (("light", light), ("dark", dark)):
        def measure(fg, bg, minimum, label):
            first, second = sorted([luminance(fg), luminance(bg)])
            ratio = (second + .05) / (first + .05)
            print(f"{'OK' if ratio >= minimum else 'FAIL'} contrast.{theme}.{label}: "
                  f"{ratio:.2f}:1 (minimum {minimum:.1f}:1)")
            if ratio < minimum:
                failures.append(f"{theme}.{label}")
        for fg, bg, minimum in pairs:
            measure(colour(tokens[fg]), colour(tokens[bg]), minimum, f"{fg}/{bg}")
        for glow in ("glow-a", "glow-b"):
            wash, bg = colour(tokens[glow]), colour(tokens["bg"])
            alpha = wash[3] * float(tokens["glow-opacity"]) * .7
            back = [wash[index] * alpha + bg[index] * (1 - alpha) for index in range(3)]
            for fg in ("fg", "muted", "accent-c"):
                measure(colour(tokens[fg]), back, 4.5, f"{fg}/{glow}")
        for accent, low, high in (("accent-b", 90, 170), ("accent-a", 0, 60)):
            hue = colorsys.rgb_to_hls(*colour(tokens[accent])[:3])[0] * 360
            require(low <= hue <= high, f"{theme}.{accent} hue {hue:.1f} outside {low}-{high}")
            print(f"OK {theme}.{accent}-hue: {hue:.1f} degrees ({low}-{high})")
    require(not failures, "contrast below threshold: " + ", ".join(failures))
    return "both palettes and masthead glows meet AA; control borders and focus meet 3:1; accent hues match the kit"


def check_kit(page, kit):
    pins = {
        "assets/css/tokens.css": "f5311c5bd69ade3fa5a30a2981d8858a21a3edaf3cf978ea7cd874735b195e1d",
        "assets/css/base.css": "6767a81361a610685cc3276dc43b1c6e8397f91897058f4c3a5cab595613594c",
        "assets/js/kit.js": "47c025656f00148bb58235d6d6172b1303021d3d7d7fcaddc380c211e3a3157f",
    }
    for name, digest in pins.items():
        data = (DOCS / name).read_bytes()
        equal(hashlib.sha256(data).hexdigest(), digest, f"kit hash/{name}")
        if kit:
            equal(data, (kit / Path(name).name).read_bytes(), f"kit bytes/{name}")
    html = next(node for node in page.nodes() if node.tag == "html")
    require(html.attrs.get("data-theme-default") == "light" and html.attrs.get("data-theme") == "light",
            "paper must declare light as its initial theme and default")
    head = next(node for node in page.nodes() if node.tag == "head")
    children = [node for node in head.children if isinstance(node, Node)]
    require(len(children) >= 2 and children[0].tag == "meta" and "charset" in children[0].attrs
            and children[1].tag == "script" and children[1].attrs == {"data-ppk-variant": "light"},
            "the classic pre-paint light script must immediately follow the charset")
    equal(hashlib.sha256(children[1].text().encode()).hexdigest(),
          "f75861806c94a78e5ee35c96dc0fe9610075d7692aeeb9b6fd1c09f81d12e3d3", "pre-paint script hash")
    require(sum("data-theme-toggle" in node.attrs for node in page.nodes()) == 1, "expected one theme toggle")
    return "kit assets and pre-paint script match pinned hashes; light shell and single theme toggle present"


def check_masthead(page, facts):
    top = page.by_id("top")
    nodes = list(top.descendants())
    require(any(node.has_class("hero-glow") for node in nodes), "masthead needs its hero glow")
    headings = [node for node in nodes if node.tag == "h1"]
    emphasis = [node for node in page.nodes() if node.tag == "em"]
    require(len(headings) == 1 and len(emphasis) == 1 and emphasis[0] in set(headings[0].descendants())
            and emphasis[0].text() == "PVHV", "only h1 may hold the PVHV emphasis")
    badges = [node for node in page.nodes() if node.has_class("venue")]
    require(len(badges) == 1 and badges[0] in nodes, "expected one venue pill in the masthead")
    def visible_text(node):
        if node.has_class("vh") or node.attrs.get("aria-hidden") == "true" or "hidden" in node.attrs:
            return ""
        return "".join(visible_text(child) if isinstance(child, Node) else child for child in node.children)
    equal(visible_text(badges[0]), facts["venue"], "visible venue pill")
    require(page.by_id("venue") in set(badges[0].descendants()), "venue fact must sit inside the single pill")
    require(all(phrase in facts["venue"] for phrase in ("ACM Transactions on Graphics 42(4)", "SIGGRAPH 2023")),
            "venue must name both journal and proceedings as one publication")
    require(not any(node is not badges[0] and any(node.has_class(name) for name in ("venue", "eyebrow", "chip"))
                    for node in nodes), "unexpected masthead badge")
    flags = [node for node in nodes if node.has_class("reimpl-flag")]
    require(len(flags) == 1, "expected one re-implementation flag")
    equal(flags[0].text(), "Code, figures and demo on this page: independent 2026 re-implementation — details.",
          "re-implementation flag")
    require(any(node.tag == "a" and node.attrs.get("href") == "#reimplementation"
                for node in flags[0].descendants()), "flag must link to the re-implementation note")
    return "hero glow, large PVHV emphasis, one complete publication pill and exact re-implementation flag present"


def check_style_files():
    require(not (DOCS / "assets/fonts").exists(), "obsolete bundled fonts directory remains")
    files = site_files()
    readme = DOCS.parent / "README.md"
    for path in [*files, readme]:
        require(not re.search(r"Lato", path.read_text(encoding="utf-8"), re.I),
                f"obsolete font reference in {path.relative_to(DOCS.parent)}")
    tokens = declarations((DOCS / "assets/css/tokens.css").read_text(encoding="utf-8"))
    for name in ("assets/css/components.css", "assets/css/page.css", "assets/js/demo.js"):
        source = (DOCS / name).read_text(encoding="utf-8")
        defined = set(tokens) | set(declarations(source))
        used = set(re.findall(r"var\(\s*--([\w-]+)", source))
        require(used <= defined, f"{name}: undefined tokens {sorted(used - defined)}")
    demo = (DOCS / "assets/js/demo.js").read_text(encoding="utf-8")
    require(not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", demo), "demo CSS contains raw colours")
    require(not re.search(r"var\(\s*--[\w-]+\s*,", demo), "demo CSS contains token fallbacks")
    page_css = (DOCS / "assets/css/page.css").read_text(encoding="utf-8")
    require(all(value == "#fff" for value in re.findall(r"#[0-9a-fA-F]+\b", page_css)),
            "page CSS raw hex colours are limited to the white SVG mat")
    old_names = r"--(?:ink|surface|control|paint|accent-ink|card-r|maxw)(?![\w-])"
    harness = DOCS.parent / "demo/harness.html"
    for path in [*[path for path in files if path.suffix in {".css", ".js", ".mjs"}], harness]:
        require(not re.search(old_names, path.read_text(encoding="utf-8")),
                f"obsolete token in {path.relative_to(DOCS.parent)}")
    light, _, _ = palettes()
    harness_tokens = declarations(harness.read_text(encoding="utf-8"))
    equal(set(harness_tokens), set("bg bg2 card line line-strong fg muted accent-c focus radius-s radius-m wrap".split()),
          "harness token roles")
    for name, value in harness_tokens.items():
        equal(value, light[name], f"harness light token/{name}")
    return "system fonts, resolved kit tokens, demo colours without fallbacks and matching light harness values"


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
