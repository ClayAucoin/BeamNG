import csv
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.beamng.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_soup(url: str) -> BeautifulSoup:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def abs_url(href: str) -> str:
    return urljoin(BASE, href)


def parse_resource_url(url: str):
    """
    https://www.beamng.com/resources/crash-hard-map-final-version-maybe.325/
    -> slug=crash-hard-map-final-version-maybe
    -> resource_numeric_id=325
    """
    path = urlparse(url).path.rstrip("/")
    last = path.split("/")[-1]
    m = re.match(r"(.+)\.(\d+)$", last)
    if not m:
        return "", ""
    return m.group(1), m.group(2)


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def find_info_value(soup: BeautifulSoup, label: str) -> str:
    """
    Works against the resource page info box:
    Author:
    Total Downloads:
    Unique ID:
    etc.
    """
    text = soup.get_text("\n", strip=True)
    pattern = rf"{re.escape(label)}\s*\n\s*(.+)"
    m = re.search(pattern, text)
    return clean_text(m.group(1)) if m else ""


def scrape_resource(resource_url: str) -> dict:
    soup = get_soup(resource_url)
    slug, resource_numeric_id = parse_resource_url(resource_url)

    title_el = soup.select_one("h1")
    title_text = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

    # "Beta Crash Hard Map ... 1.1.3" -> split latest version off the end when possible
    latest_version = ""
    mod_name = title_text
    m = re.match(r"(.+?)\s+(\d+(?:\.\d+)*(?:[A-Za-z0-9._-]*)?)$", title_text)
    if m:
        mod_name = clean_text(m.group(1))
        latest_version = clean_text(m.group(2))

    # tagline usually right below h1
    tagline = ""
    if title_el:
        nxt = title_el.find_next(string=False)
    tagline_el = soup.select_one("h1 + div, h1 + p")
    if tagline_el:
        tagline = clean_text(tagline_el.get_text(" ", strip=True))

    # tags
    tags = []
    for a in soup.select("a[href*='/tags/']"):
        t = clean_text(a.get_text(" ", strip=True))
        if t:
            tags.append(t)

    # author link from info box is often /resources/authors/...
    author_link = soup.select_one("a[href*='/resources/authors/']")
    author_name = clean_text(author_link.get_text(" ", strip=True)) if author_link else ""
    author_url = abs_url(author_link["href"]) if author_link and author_link.get("href") else ""

    info_text = soup.get_text("\n", strip=True)

    total_downloads = find_info_value(soup, "Total Downloads:")
    beamng_unique_id = find_info_value(soup, "Unique ID:")
    subscriptions = find_info_value(soup, "Subscriptions:")
    first_release = find_info_value(soup, "First Release:")
    last_update = find_info_value(soup, "Last Update:")
    category = find_info_value(soup, "Category:")

    # current version block
    current_version = ""
    current_version_release_date = ""
    current_version_downloads = ""
    version_heading = soup.find(string=re.compile(r"^Version\s+", re.I))
    if version_heading:
        current_version = clean_text(str(version_heading)).replace("Version ", "", 1)

    # fallback from parsed latest version
    if not current_version:
        current_version = latest_version

    # release/download fields near current version block
    current_version_release_date = find_info_value(soup, "Released:")
    current_version_downloads = find_info_value(soup, "Downloads:")

    # direct main download button
    download_button = soup.find("a", string=re.compile(r"Download Now", re.I))
    download_size_text = ""
    if download_button:
        download_size_text = clean_text(download_button.get_text(" ", strip=True))

    discussion_link = ""
    discussion_a = soup.find("a", string=re.compile(r"Discuss This Resource", re.I))
    if discussion_a and discussion_a.get("href"):
        discussion_link = abs_url(discussion_a["href"])

    external_info_url = ""
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if href.startswith("http") and "beamng.com" not in href:
            external_info_url = href
            break

    return {
        "resource_url": resource_url,
        "resource_slug": slug,
        "resource_numeric_id": resource_numeric_id,
        "beamng_unique_id": beamng_unique_id,
        "mod_name": mod_name,
        "latest_version": latest_version,
        "tagline": tagline,
        "author_name": author_name,
        "author_url": author_url,
        "total_downloads": total_downloads,
        "subscriptions": subscriptions,
        "first_release": first_release,
        "last_update": last_update,
        "category": category,
        "tags": " | ".join(dict.fromkeys(tags)),
        "download_size_text": download_size_text,
        "current_version": current_version,
        "current_version_release_date": current_version_release_date,
        "current_version_downloads": current_version_downloads,
        "discussion_url": discussion_link,
        "external_info_url": external_info_url,
        "scraped_at": now_iso(),
    }


def scrape_resource_history(resource_url: str, resource_row: dict) -> list[dict]:
    history_url = resource_url.rstrip("/") + "/historyImproved"
    soup = get_soup(history_url)
    text = soup.get_text("\n", strip=True)

    rows = []
    # simple text-based extraction, robust enough to start
    # Example line pattern from current public page:
    # 1.1.3 [current] visible ok ok Jan 3, 2025 7,190
    for line in text.splitlines():
        line = clean_text(line)
        if not line:
            continue

        m = re.match(
            r"^(?P<version>\S+)(?P<current>\s+\[current\])?\s+.*?\s+"
            r"(?P<release_date>[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s+"
            r"(?P<downloads>[\d,]+)$",
            line
        )
        if not m:
            continue

        rows.append({
            "resource_url": resource_url,
            "resource_numeric_id": resource_row.get("resource_numeric_id", ""),
            "beamng_unique_id": resource_row.get("beamng_unique_id", ""),
            "mod_name": resource_row.get("mod_name", ""),
            "version": m.group("version"),
            "is_current": "true" if m.group("current") else "false",
            "compatible_version": "",
            "release_date": m.group("release_date"),
            "downloads": m.group("downloads"),
            "download_url": "",
            "scraped_at": now_iso(),
        })

    # map visible "Download" links to rows in order
    download_links = [
        abs_url(a["href"]) for a in soup.select("a[href]") if clean_text(a.get_text()) == "Download"
    ]
    for i, dl in enumerate(download_links):
        if i < len(rows):
            rows[i]["download_url"] = dl

    return rows


def scrape_author_page(author_url: str) -> tuple[list[dict], list[str]]:
    page = 1
    all_rows = []
    found_resource_urls = []

    while True:
        url = author_url if page == 1 else author_url.rstrip("/") + f"/page-{page}"
        soup = get_soup(url)
        text = soup.get_text("\n", strip=True)

        author_name = find_info_value(soup, "Author:")
        author_mod_count = find_info_value(soup, "Mods:")
        author_last_update = find_info_value(soup, "Last Update:")

        resource_links = []
        for a in soup.select("a[href*='/resources/']"):
            href = a.get("href", "")
            full = abs_url(href)
            if "/resources/authors/" in full or "/resources/categories/" in full:
                continue
            if re.search(r"/resources/.+\.\d+/?$", full):
                resource_links.append((clean_text(a.get_text(" ", strip=True)), full))

        seen_on_page = set()
        for name, full_url in resource_links:
            if full_url in seen_on_page:
                continue
            seen_on_page.add(full_url)
            slug, resource_numeric_id = parse_resource_url(full_url)

            all_rows.append({
                "author_name": author_name,
                "author_url": author_url,
                "author_mod_count": author_mod_count,
                "author_last_update": author_last_update,
                "resource_url": full_url,
                "resource_numeric_id": resource_numeric_id,
                "beamng_unique_id": "",
                "mod_name": name,
                "listed_version": "",
                "listed_release_date": "",
                "listed_category": "",
                "listed_description": "",
                "listed_downloads": "",
                "listed_subscriptions": "",
                "listed_updated": "",
                "scraped_at": now_iso(),
            })
            found_resource_urls.append(full_url)

        # stop when no "Next >"
        next_link = soup.find("a", string=re.compile(r"Next\s*>", re.I))
        if not next_link:
            break
        page += 1
        time.sleep(1)

    return all_rows, found_resource_urls


def scrape_thread(thread_url: str) -> dict:
    soup = get_soup(thread_url)
    text = soup.get_text("\n", strip=True)

    title_el = soup.select_one("h1")
    thread_title = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

    category = ""
    cat_match = re.search(r"Discussion in '(.+?)' started by", text)
    if cat_match:
        category = clean_text(cat_match.group(1))

    author_name = ""
    author_url = ""
    started_match = re.search(r"Discussion in '.+?' started by\s+(.+?),\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", text)
    thread_start_date = started_match.group(2) if started_match else ""

    # first post author from #1 block
    m = re.search(r"#1\s+(.+?),\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s+Last edited:\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", text)
    first_post_last_edited = m.group(3) if m else ""
    if m:
        author_name = clean_text(m.group(1))

    # page count
    page_count = 1
    page_match = re.search(r"Page 1 of (\d+)", text)
    if page_match:
        page_count = int(page_match.group(1))

    # first external non-beamng link in post body, basic version
    download_url = ""
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if href.startswith("http") and "beamng.com" not in href:
            download_url = href
            break

    # look for "Download (873 MB):"
    size_match = re.search(r"Download\s*\(([^)]+)\)", text)
    download_size_text = size_match.group(1) if size_match else ""

    thread_id = ""
    path = urlparse(thread_url).path.rstrip("/")
    m2 = re.match(r".*\.([0-9]+)$", path.split("/")[-1])
    if m2:
        thread_id = m2.group(1)

    return {
        "thread_url": thread_url,
        "thread_id": thread_id,
        "thread_title": thread_title,
        "category": category,
        "author_name": author_name,
        "author_url": author_url,
        "thread_start_date": thread_start_date,
        "first_post_last_edited": first_post_last_edited,
        "page_count": page_count,
        "download_url": download_url,
        "download_size_text": download_size_text,
        "scraped_at": now_iso(),
    }