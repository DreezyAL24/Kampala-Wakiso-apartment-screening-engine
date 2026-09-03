#!/usr/bin/env python3
"""Multi-source apartment listing scraper for Kampala & Wakiso register.

Tool chain (in order per URL):
  1. urllib — fast HTTP for UPA / PropertyPro
  2. Firecrawl CLI — if `firecrawl` is installed and authenticated
  3. Exa contents API — if EXA_API_KEY is set
  4. Scrapling-style fetch — requests session with browser impersonation headers

Discovery pass uses Exa search for new Kampala/Wakiso apartment block listings.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = ROOT / "scrape-status.json"
PAGES_DIR = ROOT / "_pages"
REGISTER_PATH = ROOT / "_reg_0725.json"
HTML_PATH = ROOT / "Kampala-Wakiso-apartment-screening-engine.html"
FETCH_STATUS_PATH = ROOT / "_fetch_status.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def display_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%-d %B %Y") if os.name != "nt" else dt.strftime("%d %B %Y").lstrip("0")
    except ValueError:
        return iso


def write_status(**fields) -> None:
    current = {}
    if STATUS_PATH.exists():
        current = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    current.update(fields)
    STATUS_PATH.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


def load_targets() -> list[dict]:
    if REGISTER_PATH.exists():
        rows = json.loads(REGISTER_PATH.read_text(encoding="utf-8"))
        return [
            {
                "id": r["ID"],
                "url": r.get("URL", ""),
                "source": r.get("Source", ""),
                "area": r.get("Area", ""),
            }
            for r in rows
            if r.get("URL")
        ]

    if not HTML_PATH.exists():
        raise FileNotFoundError("No register JSON or screening engine HTML found")

    text = HTML_PATH.read_text(encoding="utf-8")
    m = re.search(r"const DATA = (\[.*?\]);", text, re.S)
    if not m:
        raise ValueError("Could not parse DATA array from HTML")
    data = json.loads(m.group(1))
    return [
        {"id": str(x["id"]), "url": x["url"], "source": x["source"], "area": x["area"]}
        for x in data
        if x.get("url")
    ]


def fetch_urllib(url: str) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.status, resp.read(), "urllib"


def fetch_firecrawl(url: str) -> tuple[int, bytes, str]:
    if not shutil.which("firecrawl"):
        raise RuntimeError("firecrawl CLI not installed")
    proc = subprocess.run(
        ["firecrawl", "scrape", url, "--format", "html"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "firecrawl scrape failed")
    body = proc.stdout.encode("utf-8")
    return 200, body, "firecrawl"


def fetch_exa(url: str) -> tuple[int, bytes, str]:
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        raise RuntimeError("EXA_API_KEY not set")
    payload = json.dumps({"urls": [url]}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.exa.ai/contents",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    results = data.get("results") or []
    if not results:
        raise RuntimeError("Exa returned no results")
    text = results[0].get("text") or results[0].get("html") or ""
    return 200, text.encode("utf-8"), "exa"


def fetch_scrapling_style(url: str) -> tuple[int, bytes, str]:
    """Browser-impersonation GET (Scrapling-equivalent when MCP is unavailable)."""
    import requests  # type: ignore

    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(url, timeout=45, allow_redirects=True)
    resp.raise_for_status()
    return resp.status, resp.content, "scrapling"


def fetch_url(url: str) -> tuple[str, int, int, str]:
    errors: list[str] = []
    for fn in (fetch_urllib, fetch_firecrawl, fetch_exa, fetch_scrapling_style):
        try:
            status, body, tool = fn(url)
            if status == 200 and body:
                return tool, status, len(body), ""
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {exc}")
            time.sleep(0.5)
    return "none", 0, 0, "; ".join(errors[-2:])


def discover_with_exa() -> list[str]:
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return []
    queries = [
        "apartment block for sale Kampala Wakiso Uganda site:propertypro.co.ug",
        "block of apartments for sale Najjera Kira Uganda site:ugandapropertyagents.com",
        "apartment block for sale Uganda site:jiji.ug",
    ]
    found: list[str] = []
    for query in queries:
        payload = json.dumps({"query": query, "numResults": 8, "type": "auto"}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.exa.ai/search",
            data=payload,
            headers={"Content-Type": "application/json", "x-api-key": api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("results") or []:
                url = item.get("url")
                if url and url not in found:
                    found.append(url)
        except Exception:
            continue
    return found


def scrape_target(target: dict) -> dict:
    listing_id = target["id"]
    url = target["url"]
    tool, status, size, err = fetch_url(url)
    if status == 200 and size:
        PAGES_DIR.mkdir(exist_ok=True)
        out = PAGES_DIR / f"{listing_id}.html"
        # Re-fetch with winning tool only if we already succeeded
        try:
            if tool == "urllib":
                _, body, _ = fetch_urllib(url)
            elif tool == "firecrawl":
                _, body, _ = fetch_firecrawl(url)
            elif tool == "exa":
                _, body, _ = fetch_exa(url)
            else:
                _, body, _ = fetch_scrapling_style(url)
            out.write_bytes(body)
        except Exception:
            pass
    return {
        "id": listing_id,
        "url": url,
        "source": target.get("source", ""),
        "area": target.get("area", ""),
        "tool": tool,
        "status": status or err or "ERR",
        "bytes": size,
    }


def main() -> int:
    started = utc_now()
    write_status(
        status="running",
        started_at=started,
        message="Scrape in progress…",
    )

    targets = load_targets()
    tools_used: set[str] = set()
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(scrape_target, t): t for t in targets}
        for fut in as_completed(futures):
            row = fut.result()
            results.append(row)
            if row["tool"] != "none":
                tools_used.add(row["tool"])
            print(row["id"], row["tool"], row["status"], row["bytes"], row["url"][:70])

    discoveries = discover_with_exa()
    if discoveries:
        tools_used.add("exa-discovery")

    ok = sum(1 for r in results if r["status"] == 200)
    failed = len(results) - ok
    finished = utc_now()

    FETCH_STATUS_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    write_status(
        last_scrape_at=finished,
        last_scrape_display=display_date(finished),
        status="done",
        started_at=started,
        finished_at=finished,
        sources_scanned=sorted({t["source"] for t in targets if t.get("source")}),
        tools_used=sorted(tools_used) or ["urllib"],
        listings_checked=len(results),
        listings_ok=ok,
        listings_failed=failed,
        new_discoveries=len(discoveries),
        discovery_urls=discoveries[:20],
        message=f"Scrape finished — {ok}/{len(results)} listings fetched OK",
    )
    print(f"Done: {ok}/{len(results)} OK, tools={sorted(tools_used)}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        write_status(status="error", message=str(exc), finished_at=utc_now())
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
