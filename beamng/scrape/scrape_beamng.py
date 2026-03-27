import csv
import re
import time
import io
import os
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
OUTPUT_DIR = (
    r"C:\Users\Administrator\Dropbox\__BeamNG__\____directory-extract____\scrape\output"
)
SAVE_EVERY = 5


def load_input_rows(csv_url):
    import csv
    import io
    import requests

    r = requests.get(csv_url, timeout=30)
    r.raise_for_status()

    rows = []
    reader = csv.DictReader(io.StringIO(r.text))

    for row in reader:
        row_type = (row.get("type") or "").strip().lower()
        url = (row.get("url") or "").strip()
        enabled = (row.get("enabled") or "").strip().lower()

        type_map = {
            "resource": "resource",
            "resources": "resource",
            "author": "author",
            "authors": "author",
            "thread": "thread",
            "threads": "thread",
        }
        row_type = type_map.get(row_type, "")

        if enabled not in {"true", "1", "yes", "y"}:
            continue
        if row_type not in {"resource", "author", "thread"}:
            continue
        if not url:
            continue

        rows.append(
            {
                "type": row_type,
                "url": url,
            }
        )

    return rows


def load_input_rows_from_drive(csv_path):
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_type = (row.get("type") or "").strip().lower()
            url = (row.get("url") or "").strip()
            enabled = (row.get("enabled") or "").strip().lower()

            # allow both singular and plural
            type_map = {
                "resource": "resource",
                "resources": "resource",
                "author": "author",
                "authors": "author",
                "thread": "thread",
                "threads": "thread",
            }
            row_type = type_map.get(row_type, "")

            if enabled not in {"true", "1", "yes", "y"}:
                continue
            if row_type not in {"resource", "author", "thread"}:
                continue
            if not url:
                continue

            rows.append(
                {
                    "type": row_type,
                    "url": url,
                    "notes": (row.get("notes") or "").strip(),
                }
            )
    return rows


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


def out_path(filename: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


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
    author_name = find_info_value(soup, "Author:")
    author_url = ""

    # initial attempt: look for any links in the page that look like
    # author links, without trying to match the name
    # author_candidates = []
    # for a in soup.select("a[href]"):
    #     href = (a.get("href") or "").strip()
    #     name = clean_text(a.get_text(" ", strip=True))
    #     if not href or not name:
    #         continue

    #     href_l = href.lower()
    #     if "resources/authors/" not in href_l:
    #         continue

    #     author_candidates.append((name, abs_url("/" + href.lstrip("/"))))

    # more defensive parsing of author links, looking for matches to the
    # parsed author_name and falling back to single candidate if only one found
    author_name = find_info_value(soup, "Author:")
    author_url = ""

    author_candidates = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        name = clean_text(a.get_text(" ", strip=True))
        if not href or not name:
            continue

        href_l = href.lower()
        if "resources/authors/" not in href_l:
            continue

        full_href = abs_url("/" + href.lstrip("/"))
        author_candidates.append((name, full_href))

    for name, href in author_candidates:
        if author_name and name.casefold() == author_name.casefold():
            author_url = href
            break

    if not author_url and author_candidates:
        author_url = author_candidates[0][1]

    # Prefer an anchor whose visible text matches the parsed author_name
    for name, href in author_candidates:
        if author_name and name.casefold() == author_name.casefold():
            author_url = href
            break

    # Fallback: if there is only one author link on the page, use it
    if not author_url and len(author_candidates) == 1:
        author_url = author_candidates[0][1]

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

    rows = []

    # Find all version blocks (each version row)
    version_blocks = soup.select("li, tr")

    for block in version_blocks:
        text = clean_text(block.get_text(" ", strip=True))

        # Basic guard: skip junk rows
        if not text or len(text) < 10:
            continue

        # Extract version (usually first token)
        version_match = re.match(r"^([^\s]+)", text)
        version = version_match.group(1) if version_match else ""

        # Extract date (Jan 3, 2025)
        date_match = re.search(r"[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}", text)
        release_date = date_match.group(0) if date_match else ""

        # Extract downloads (7,190)
        downloads_match = re.search(r"(\d{1,3}(?:,\d{3})+)$", text)
        downloads = downloads_match.group(1) if downloads_match else ""

        # Detect current version
        is_current = "true" if "[current]" in text.lower() else "false"

        # Find download link inside this block
        download_url = ""
        link = block.find("a", string=re.compile(r"Download", re.I))
        if link and link.get("href"):
            download_url = abs_url(link["href"])

        rows.append(
            {
                "resource_url": resource_url,
                "resource_numeric_id": resource_row.get("resource_numeric_id", ""),
                "beamng_unique_id": resource_row.get("beamng_unique_id", ""),
                "mod_name": resource_row.get("mod_name", ""),
                "version": version,
                "is_current": is_current,
                "compatible_version": "",  # can refine later
                "release_date": release_date,
                "downloads": downloads,
                "download_url": download_url,
                "scraped_at": now_iso(),
            }
        )

    return rows


def scrape_author_page(author_url: str) -> tuple[list[dict], list[str]]:
    page = 1
    all_rows = []
    found_resource_urls = []

    while True:
        url = author_url if page == 1 else author_url.rstrip("/") + f"page-{page}/"
        soup = get_soup(url)
        text = soup.get_text("\n", strip=True)

        author_name = find_info_value(soup, "Author:")
        author_mod_count = find_info_value(soup, "Mods:")
        author_last_update = find_info_value(soup, "Last Update:")

        seen_on_page = set()

        for a in soup.select("a[href]"):
            href = a.get("href", "")
            full = abs_url(href)

            if "/resources/authors/" in full or "/resources/categories/" in full:
                continue

            if not re.search(r"/resources/.+\.\d+/?$", full):
                continue

            if full in seen_on_page:
                continue

            seen_on_page.add(full)
            found_resource_urls.append(full)

            slug, resource_numeric_id = parse_resource_url(full)
            mod_name = clean_text(a.get_text(" ", strip=True))

            all_rows.append(
                {
                    "author_name": author_name,
                    "author_url": author_url,
                    "author_mod_count": author_mod_count,
                    "author_last_update": author_last_update,
                    "resource_url": full,
                    "resource_numeric_id": resource_numeric_id,
                    "beamng_unique_id": "",
                    "mod_name": mod_name,
                    "listed_version": "",
                    "listed_release_date": "",
                    "listed_category": "",
                    "listed_description": "",
                    "listed_downloads": "",
                    "listed_subscriptions": "",
                    "listed_updated": "",
                    "scraped_at": now_iso(),
                }
            )

        # more forgiving next-page check
        next_link = soup.find("a", href=re.compile(r"/page-\d+/?$"))
        if not next_link:
            break

        page += 1
        time.sleep(1)

    return all_rows, dedupe_keep_order(found_resource_urls)


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
    started_match = re.search(
        r"Discussion in '.+?' started by\s+(.+?),\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})",
        text,
    )
    thread_start_date = started_match.group(2) if started_match else ""

    # first post author from #1 block
    m = re.search(
        r"#1\s+(.+?),\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s+Last edited:\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})",
        text,
    )
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


def dedupe_keep_order(values):
    seen = set()
    out = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def save_progress(
    resource_rows,
    version_rows,
    author_rows,
    thread_rows,
    discovered_author_urls,
    resource_fieldnames,
    version_fieldnames,
    author_fieldnames,
    thread_fieldnames,
):
    write_csv(out_path("beamng_resources.csv"), resource_rows, resource_fieldnames)
    write_csv(
        out_path("beamng_resource_versions.csv"),
        version_rows,
        version_fieldnames,
    )
    write_csv(out_path("beamng_authors.csv"), author_rows, author_fieldnames)
    write_csv(out_path("beamng_threads.csv"), thread_rows, thread_fieldnames)

    discovered_author_rows = [
        {"author_url": u} for u in dedupe_keep_order(discovered_author_urls)
    ]
    if discovered_author_rows:
        write_csv(
            out_path("beamng_discovered_author_urls.csv"),
            discovered_author_rows,
            ["author_url"],
        )


def enrich_author_rows_from_resources(author_rows, resource_rows):
    resource_by_url = {}
    for r in resource_rows:
        key = (r.get("resource_url") or "").rstrip("/")
        if key:
            resource_by_url[key] = r

    for row in author_rows:
        key = (row.get("resource_url") or "").rstrip("/")
        resource = resource_by_url.get(key)
        if not resource:
            continue

        row["beamng_unique_id"] = resource.get("beamng_unique_id", "")
        row["mod_name"] = resource.get("mod_name", "") or row.get("mod_name", "")
        row["listed_version"] = resource.get("latest_version", "")
        row["listed_release_date"] = resource.get("first_release", "")
        row["listed_category"] = resource.get("category", "")
        row["listed_description"] = resource.get("tagline", "")
        row["listed_downloads"] = resource.get("total_downloads", "")
        row["listed_subscriptions"] = resource.get("subscriptions", "")
        row["listed_updated"] = resource.get("last_update", "")
        row["author_name"] = resource.get("author_name", "") or row.get(
            "author_name", ""
        )
        row["author_url"] = resource.get("author_url", "") or row.get("author_url", "")


def scrape_missing_resources(
    found_resource_urls, resource_rows, version_rows, discovered_author_urls
):
    existing = {
        (r.get("resource_url") or "").rstrip("/")
        for r in resource_rows
        if r.get("resource_url")
    }

    for resource_url in found_resource_urls:
        key = resource_url.rstrip("/")
        if key in existing:
            continue

        try:
            print(f"  [discovered resource] {resource_url}")
            resource_row = scrape_resource(resource_url)
            resource_rows.append(resource_row)
            existing.add(key)

            if resource_row.get("author_url"):
                discovered_author_urls.append(resource_row["author_url"])

            try:
                history_rows = scrape_resource_history(resource_url, resource_row)
                version_rows.extend(history_rows)
            except Exception as e:
                print(f"    history failed: {type(e).__name__}: {e}")

            time.sleep(1)

        except Exception as e:
            print(f"    discovered resource failed: {type(e).__name__}: {e}")


def main():
    input_csv = "https://script.google.com/macros/s/AKfycbwP_rEclRk0mzKOonK2gMLn76XHItKTcf3RPUEInK4uJSzxKtx8vRfdvEQVOvejXGWM/exec"
    rows = load_input_rows(input_csv)

    # INPUT_CSV = r"C:\Users\Administrator\Dropbox\__BeamNG__\____directory-extract____\scrape\beamng_scrape_input_urls.csv"
    # rows = load_input_rows_from_drive(INPUT_CSV)

    resource_urls = dedupe_keep_order(
        [r["url"] for r in rows if r["type"] == "resource"]
    )
    author_urls = dedupe_keep_order([r["url"] for r in rows if r["type"] == "author"])
    thread_urls = dedupe_keep_order([r["url"] for r in rows if r["type"] == "thread"])

    resource_rows = []
    version_rows = []
    author_rows = []
    thread_rows = []

    discovered_author_urls = []

    resource_fieldnames = [
        "resource_url",
        "resource_slug",
        "resource_numeric_id",
        "beamng_unique_id",
        "mod_name",
        "latest_version",
        "tagline",
        "author_name",
        "author_url",
        "total_downloads",
        "subscriptions",
        "first_release",
        "last_update",
        "category",
        "tags",
        "download_size_text",
        "current_version",
        "current_version_release_date",
        "current_version_downloads",
        "discussion_url",
        "external_info_url",
        "scraped_at",
    ]

    version_fieldnames = [
        "resource_url",
        "resource_numeric_id",
        "beamng_unique_id",
        "mod_name",
        "version",
        "is_current",
        "compatible_version",
        "release_date",
        "downloads",
        "download_url",
        "scraped_at",
    ]

    author_fieldnames = [
        "author_name",
        "author_url",
        "author_mod_count",
        "author_last_update",
        "resource_url",
        "resource_numeric_id",
        "beamng_unique_id",
        "mod_name",
        "listed_version",
        "listed_release_date",
        "listed_category",
        "listed_description",
        "listed_downloads",
        "listed_subscriptions",
        "listed_updated",
        "scraped_at",
    ]

    thread_fieldnames = [
        "thread_url",
        "thread_id",
        "thread_title",
        "category",
        "author_name",
        "author_url",
        "thread_start_date",
        "first_post_last_edited",
        "page_count",
        "download_url",
        "download_size_text",
        "scraped_at",
    ]

    print(f"Loaded {len(resource_urls)} resource URLs")
    print(f"Loaded {len(author_urls)} author URLs")
    print(f"Loaded {len(thread_urls)} thread URLs")

    for i, resource_url in enumerate(resource_urls, start=1):
        try:
            print(f"[resource {i}/{len(resource_urls)}] {resource_url}")
            resource_row = scrape_resource(resource_url)
            resource_rows.append(resource_row)

            if resource_row.get("author_url"):
                discovered_author_urls.append(resource_row["author_url"])

            try:
                history_rows = scrape_resource_history(resource_url, resource_row)
                version_rows.extend(history_rows)
            except Exception as e:
                print(f"  history failed: {type(e).__name__}: {e}")

        except Exception as e:
            print(f"  resource failed: {type(e).__name__}: {e}")

        if i % SAVE_EVERY == 0:
            save_progress(
                resource_rows,
                version_rows,
                author_rows,
                thread_rows,
                discovered_author_urls,
                resource_fieldnames,
                version_fieldnames,
                author_fieldnames,
                thread_fieldnames,
            )
            print("  progress saved")

        time.sleep(1)

    # combine manually supplied + discovered author URLs
    author_urls = dedupe_keep_order(author_urls + discovered_author_urls)

    for i, author_url in enumerate(author_urls, start=1):
        try:
            print(f"[author {i}/{len(author_urls)}] {author_url}")
            rows_for_author, found_resource_urls = scrape_author_page(author_url)
            author_rows.extend(rows_for_author)

            scrape_missing_resources(
                found_resource_urls,
                resource_rows,
                version_rows,
                discovered_author_urls,
            )

        except Exception as e:
            print(f"  author failed: {type(e).__name__}: {e}")

        enrich_author_rows_from_resources(author_rows, resource_rows)
        if i % SAVE_EVERY == 0:
            save_progress(
                resource_rows,
                version_rows,
                author_rows,
                thread_rows,
                discovered_author_urls,
                resource_fieldnames,
                version_fieldnames,
                author_fieldnames,
                thread_fieldnames,
            )
            print("  progress saved")

        time.sleep(1)

    for i, thread_url in enumerate(thread_urls, start=1):
        try:
            print(f"[thread {i}/{len(thread_urls)}] {thread_url}")
            thread_row = scrape_thread(thread_url)
            thread_rows.append(thread_row)
        except Exception as e:
            print(f"  thread failed: {type(e).__name__}: {e}")

        if i % SAVE_EVERY == 0:
            save_progress(
                resource_rows,
                version_rows,
                author_rows,
                thread_rows,
                discovered_author_urls,
                resource_fieldnames,
                version_fieldnames,
                author_fieldnames,
                thread_fieldnames,
            )
            print("  progress saved")

        time.sleep(1)

    save_progress(
        resource_rows,
        version_rows,
        author_rows,
        thread_rows,
        discovered_author_urls,
        resource_fieldnames,
        version_fieldnames,
        author_fieldnames,
        thread_fieldnames,
    )

    print()
    print("Done.")
    print(f"Resources: {len(resource_rows)}")
    print(f"Versions:  {len(version_rows)}")
    print(f"Authors:   {len(author_rows)}")
    print(f"Threads:   {len(thread_rows)}")
    print(f"Discovered author URLs: {len(dedupe_keep_order(discovered_author_urls))}")


if __name__ == "__main__":
    main()
