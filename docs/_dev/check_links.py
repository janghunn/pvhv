#!/usr/bin/env python3
"""Check outgoing paper-page links without downloading linked publications."""

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href", "")
        if tag == "a" and href.startswith(("https://", "http://", "//")):
            self.urls.add("https:" + href if href.startswith("//") else href)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(url, published=False):
    host = urlsplit(url).hostname
    handlers = []
    if host == "doi.org":
        handlers.append(NoRedirect())
    opener = build_opener(*handlers)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}, method="GET")
    try:
        try:
            response = opener.open(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            status = response.code
            if host == "dl.acm.org" and status == 403:
                body = response.read(65536).lower()
                challenge = (b"/cdn-cgi/challenge-platform/" in body
                             or (b"cloudflare" in body and (b"challenge" in body or b"just a moment" in body)))
                if challenge:
                    return True, "CHALLENGE (verify in browser)", "403 Cloudflare challenge"
            else:
                response.read(1)
            if host == "doi.org":
                location = response.headers.get("Location", "")
                resolved = 300 <= status < 400 and urlsplit(location).hostname == "dl.acm.org"
                return resolved, f"{'OK' if resolved else 'FAIL'} ({status})", f"redirect to {location}"
            if url == "https://github.com/janghunn/pvhv":
                if status == 404 and not published:
                    return True, "PENDING", "(repo not yet created)"
                if published:
                    resolved = status == 200
                    return resolved, f"{'OK' if resolved else 'FAIL'} ({status})", "published repository must return 200"
            resolved = status < 400
            return resolved, f"{'OK' if resolved else 'FAIL'} ({status})", ""
    except (URLError, OSError, ValueError) as error:
        return False, "FAIL", str(error)


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--published", action="store_true", help="require the published GitHub repository to return 200")
    args = arguments.parse_args()
    parser = Links()
    parser.feed((Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8"))
    print("STATUS URL NOTE")
    failures = 0
    for url in sorted(parser.urls):
        passed, status, note = check(url, published=args.published)
        print(f"{status} {url} {note}".rstrip())
        if not passed:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
