"""
KnowledgeOwl → Markdown export.

Pulls all live articles (status=published or review) from a KnowledgeOwl
knowledge base and writes them as .md files organized by category. Images
referenced in articles are downloaded to a local images/ folder and linked
from the Markdown. Re-runnable: clears and rewrites output_dir on each run.
"""

import hashlib
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from markdownify import markdownify
from slugify import slugify


API_BASE = "https://app.knowledgeowl.com/api/head"
PAGE_LIMIT = 100
REQUEST_TIMEOUT = 30

# When KO_SKIP_IMAGE_DOWNLOAD is truthy, rewrite_images skips the per-image network fetch and
# emits the absolute KO image URL directly (alt text and all other attributes are untouched).
# Set in load_config() once .env is loaded. The CI refresh enables this: only *.md is synced into
# docs/ (images are dropped) and the KO file URLs aren't downloadable from CI anyway, so the
# fetches are pure waste. Output is byte-identical to the existing "download failed -> absolute
# URL" fallback, just without the wasted requests.
SKIP_IMAGE_DOWNLOAD = False


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_config():
    load_dotenv()
    global SKIP_IMAGE_DOWNLOAD
    SKIP_IMAGE_DOWNLOAD = os.getenv("KO_SKIP_IMAGE_DOWNLOAD", "").strip().lower() in ("1", "true", "yes", "on")
    cfg = {
        "api_key": os.getenv("KO_API_KEY", "").strip(),
        "project_id": os.getenv("KO_PROJECT_ID", "").strip(),
        "output_dir": Path(os.getenv("KO_OUTPUT_DIR", "./export").strip()).expanduser().resolve(),
        "kb_url": os.getenv("KO_KB_URL", "").strip().rstrip("/"),
    }
    if not cfg["api_key"]:
        die("KO_API_KEY is not set. Copy .env.example to .env and fill it in.")
    if not cfg["project_id"]:
        die("KO_PROJECT_ID is not set. See README for how to find it.")
    return cfg


def api_get(session, endpoint, body):
    """Call a KO list endpoint with a JSON body. Raises on error with KO's message when available."""
    url = f"{API_BASE}/{endpoint}.json"
    resp = session.get(url, json=body, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 401:
        die("API returned 401 Unauthorized. Check KO_API_KEY.")
    if resp.status_code == 403:
        die("API returned 403 Forbidden. The API key may lack GET permission for this object.")
    if not resp.ok:
        try:
            err = resp.json()
            die(f"API error {resp.status_code}: {err.get('message') or err}")
        except ValueError:
            die(f"API error {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def fetch_all(session, endpoint, query):
    """Paginate through a list endpoint until all pages are retrieved."""
    items = []
    page = 1
    while True:
        body = {**query, "limit": PAGE_LIMIT, "page": page}
        resp = api_get(session, endpoint, body)
        data = resp.get("data") or []
        if not isinstance(data, list):
            # Single-item response (shouldn't happen for list endpoints, but be tolerant).
            data = [data]
        items.extend(data)
        stats = resp.get("page_stats") or {}
        total_pages = int(stats.get("total_pages") or 1)
        if page >= total_pages:
            break
        page += 1
    return items


def unwrap_localized(value):
    """Categories store name as a language-keyed dict like {'en': 'Title'}. Unwrap to a string."""
    if isinstance(value, dict):
        return value.get("en") or next(iter(value.values()), "") or ""
    return value or ""


def build_category_index(categories):
    """
    Return {id: <category entry>} with fields needed for both folder structure
    and category-index file generation.

    KO returns parent_id as a string for children and `false` (JSON bool) for top-level.
    """
    index = {}
    for cat in categories:
        cid = cat.get("id")
        if not cid:
            continue
        parent_id = cat.get("parent_id")
        if parent_id is False or parent_id == "":
            parent_id = None
        index[cid] = {
            "name": unwrap_localized(cat.get("name")) or "Untitled",
            "parent_id": parent_id if isinstance(parent_id, str) else None,
            "type": cat.get("type") or "basic",
            "url_hash": cat.get("url_hash") or "",
            "description": cat.get("description") or "",
            "meta_description": cat.get("meta_description") or "",
            "date_created": cat.get("date_created") or "",
            "date_modified": cat.get("date_modified") or "",
        }
    return index


def category_path(cat_id, index):
    """Walk from a category up to the root, returning names from root → leaf."""
    path = []
    seen = set()
    cur = cat_id
    while cur and cur in index and cur not in seen:
        seen.add(cur)
        path.insert(0, index[cur]["name"])
        cur = index[cur]["parent_id"]
    return path


def safe_segment(name, *, lowercase=False, max_length=100):
    s = slugify(name, lowercase=lowercase, separator="-", max_length=max_length)
    return s or "untitled"


def article_title(article):
    """Prefer the Full Article Title; fall back to the current version's title."""
    name = article.get("name")
    if name:
        return name
    cv = article.get("current_version") or {}
    en = cv.get("en") or {}
    return en.get("title") or "Untitled"


def article_body_html(article):
    cv = article.get("current_version") or {}
    en = cv.get("en") or {}
    return en.get("text") or ""


def strip_ko_templates(html):
    """Remove KO-only merge codes like [article("action_icons")] and [template("related")]."""
    return re.sub(r'\[(?:article|template)\("[^"]*"\)\]', '', html)


CONTENT_TYPE_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/avif": ".avif",
    "image/heic": ".heic",
}


def download_image(session, img_url, images_dir, cache):
    """
    Download an image once. Return the local filename, or None on failure.
    Skips anything whose Content-Type is not image/* (e.g. 200-OK HTML error pages
    or auth-gated hosts like Google Drive that redirect to a sign-in page).
    """
    if img_url in cache:
        return cache[img_url]
    try:
        resp = session.get(img_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! image failed ({img_url}): {e}")
        cache[img_url] = None
        return None

    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        print(f"  ! image skipped (non-image response, content-type={ctype or 'missing'}): {img_url}")
        cache[img_url] = None
        return None

    ext = CONTENT_TYPE_TO_EXT.get(ctype)
    if not ext:
        subtype = ctype.split("/", 1)[1] if "/" in ctype else "bin"
        ext = f".{subtype}"

    parsed = urlparse(img_url)
    stem, _ = os.path.splitext(os.path.basename(parsed.path) or "image")
    digest = hashlib.sha1(img_url.encode("utf-8")).hexdigest()[:8]
    filename = f"{safe_segment(stem, lowercase=True, max_length=60)}-{digest}{ext}"
    (images_dir / filename).write_bytes(resp.content)
    cache[img_url] = filename
    return filename


def rewrite_images(html, session, images_dir, article_path, cache):
    """
    Find <img src=...> URLs and either (a) download + rewrite to a local relative
    path, or (b) normalize to an absolute URL so the rendered Markdown never
    contains a root-relative `/files/...` path that looks like a broken local file.
    """
    def replace(match):
        prefix, src, suffix = match.group(1), match.group(2), match.group(3)
        if src.startswith("data:"):
            return match.group(0)
        if src.startswith("//"):
            abs_url = "https:" + src
        elif src.startswith("/"):
            abs_url = "https://app.knowledgeowl.com" + src
        elif src.startswith(("http://", "https://")):
            abs_url = src
        else:
            return match.group(0)

        filename = None if SKIP_IMAGE_DOWNLOAD else download_image(session, abs_url, images_dir, cache)
        if filename:
            rel = os.path.relpath(images_dir / filename, article_path.parent)
            return f"{prefix}{rel}{suffix}"
        # Download skipped (KO_SKIP_IMAGE_DOWNLOAD) or failed — keep the tag but force an absolute
        # URL so the Markdown doesn't render as a broken local path. Only `src` is rewritten; the
        # alt text (and any other <img> attributes) are left untouched.
        return f"{prefix}{abs_url}{suffix}"

    return re.sub(
        r'(<img\b[^>]*?\bsrc=["\'])([^"\']+)(["\'])',
        replace,
        html,
        flags=re.IGNORECASE,
    )


def yaml_escape(value):
    if value is None or value == "":
        return '""'
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def build_frontmatter(article, cat_path, kb_url):
    title = article_title(article)
    url_hash = article.get("url_hash") or ""
    article_url = f"{kb_url}/help/{url_hash}" if kb_url and url_hash else ""

    lines = [
        "---",
        f"title: {yaml_escape(title)}",
        f"category: {yaml_escape(' / '.join(cat_path) if cat_path else 'Uncategorized')}",
        f"url_hash: {yaml_escape(url_hash)}",
    ]
    if article_url:
        lines.append(f"url: {yaml_escape(article_url)}")
    if article.get("date_created"):
        lines.append(f"date_created: {yaml_escape(article['date_created'])}")
    if article.get("date_modified"):
        lines.append(f"date_modified: {yaml_escape(article['date_modified'])}")
    if article.get("meta_description"):
        lines.append(f"summary: {yaml_escape(article['meta_description'])}")
    lines.append(f"id: {yaml_escape(article.get('id') or '')}")
    lines.append("---")
    return "\n".join(lines)


def unique_path(path, taken):
    """If `path` is already in `taken`, append -2, -3, ... to the stem until unique."""
    if path not in taken:
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        candidate = path.with_name(f"{stem}-{i}{suffix}")
        if candidate not in taken:
            return candidate
        i += 1


def category_folder(cat_id, cat_index, output_root):
    """Resolve the on-disk folder for a category, creating parent segments as needed."""
    segments = category_path(cat_id, cat_index)
    folder = output_root
    for seg in segments:
        folder = folder / safe_segment(seg)
    return folder


def write_category_index(cat_id, cat_index, cat_view_articles, output_root, images_dir, session, kb_url, image_cache):
    """
    Write an `_index.md` for this category if it has any content:
    - its `description` field, and/or
    - the body of its category-view article (for Topic Display and Custom Content categories).
    Returns the written path, or None if the category has no content to export.
    """
    entry = cat_index[cat_id]
    description_html = entry.get("description") or ""
    cv_article = cat_view_articles.get(cat_id)
    cv_body_html = article_body_html(cv_article) if cv_article else ""

    if not description_html.strip() and not cv_body_html.strip():
        return None

    folder = category_folder(cat_id, cat_index, output_root)
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / "_index.md"

    # Combine description and category-view article body into one HTML blob,
    # so existing strip/rewrite helpers work the same as for articles.
    parts = []
    if description_html.strip():
        parts.append(description_html)
    if cv_body_html.strip():
        parts.append(cv_body_html)
    combined_html = "\n\n".join(parts)
    combined_html = strip_ko_templates(combined_html)
    combined_html = rewrite_images(combined_html, session, images_dir, index_path, image_cache)
    body_md = markdownify(combined_html, heading_style="ATX", bullets="-").strip()

    cat_path = category_path(cat_id, cat_index)
    name = entry["name"]
    url_hash = entry.get("url_hash") or ""
    category_url = f"{kb_url}/help/{url_hash}" if kb_url and url_hash else ""

    lines = [
        "---",
        f"title: {yaml_escape(name)}",
        f"type: {yaml_escape('category_index')}",
        f"category_type: {yaml_escape(entry.get('type') or 'basic')}",
        f"category: {yaml_escape(' / '.join(cat_path) if cat_path else name)}",
        f"url_hash: {yaml_escape(url_hash)}",
    ]
    if category_url:
        lines.append(f"url: {yaml_escape(category_url)}")
    if entry.get("date_created"):
        lines.append(f"date_created: {yaml_escape(entry['date_created'])}")
    if entry.get("date_modified"):
        lines.append(f"date_modified: {yaml_escape(entry['date_modified'])}")
    if entry.get("meta_description"):
        lines.append(f"summary: {yaml_escape(entry['meta_description'])}")
    lines.append(f"id: {yaml_escape(cat_id)}")
    lines.append("---")
    frontmatter = "\n".join(lines)

    index_path.write_text(f"{frontmatter}\n\n# {name}\n\n{body_md}\n", encoding="utf-8")
    return index_path


def write_article(article, cat_index, output_root, images_dir, session, kb_url, image_cache, written_paths):
    title = article_title(article)
    cat_path = category_path(article.get("category"), cat_index)

    folder = output_root
    for segment in cat_path:
        folder = folder / safe_segment(segment)
    folder.mkdir(parents=True, exist_ok=True)

    # Resolve a unique path BEFORE writing so two articles that slugify to the
    # same filename in the same folder don't silently overwrite each other.
    article_path = unique_path(
        folder / f"{safe_segment(title, lowercase=True, max_length=80)}.md",
        written_paths,
    )

    html = strip_ko_templates(article_body_html(article))
    html = rewrite_images(html, session, images_dir, article_path, image_cache)
    body_md = markdownify(html, heading_style="ATX", bullets="-").strip()

    frontmatter = build_frontmatter(article, cat_path, kb_url)
    article_path.write_text(f"{frontmatter}\n\n# {title}\n\n{body_md}\n", encoding="utf-8")
    return article_path


def main():
    cfg = load_config()

    session = requests.Session()
    session.auth = (cfg["api_key"], "x")
    session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    output_root = cfg["output_dir"]
    if output_root.exists():
        print(f"Clearing existing output folder: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    images_dir = output_root / "images"
    images_dir.mkdir(exist_ok=True)

    print("Fetching categories...")
    categories = fetch_all(session, "category", {"project_id": cfg["project_id"]})
    print(f"  {len(categories)} categories")
    cat_index = build_category_index(categories)

    print("Fetching articles (status: published or review)...")
    articles = fetch_all(
        session,
        "article",
        {"project_id": cfg["project_id"], "status": {"$in": ["published", "review"]}},
    )
    print(f"  {len(articles)} articles")

    # Articles with `category_view` set are KO-generated pseudo-articles that hold
    # the body content for Topic Display and Custom Content categories. Route those
    # into the category-index pipeline instead of writing them as regular articles.
    cat_view_articles = {}
    regular_articles = []
    for art in articles:
        cv_id = art.get("category_view")
        if cv_id and cv_id in cat_index:
            cat_view_articles[cv_id] = art
        else:
            regular_articles.append(art)

    print("Writing Markdown files...")
    image_cache = {}
    written_paths = set()
    for i, art in enumerate(regular_articles, 1):
        try:
            path = write_article(
                art, cat_index, output_root, images_dir, session, cfg["kb_url"], image_cache, written_paths
            )
            written_paths.add(path)
            print(f"  [{i}/{len(regular_articles)}] {path.relative_to(output_root)}")
        except Exception as e:
            print(f"  ! failed to write '{article_title(art)}': {e}")

    print("Writing category index files...")
    cat_indexes_written = 0
    for cat_id in cat_index:
        try:
            path = write_category_index(
                cat_id, cat_index, cat_view_articles, output_root, images_dir, session, cfg["kb_url"], image_cache
            )
            if path:
                cat_indexes_written += 1
                print(f"  {path.relative_to(output_root)}")
        except Exception as e:
            print(f"  ! failed to write index for category '{cat_index[cat_id]['name']}': {e}")

    if not any(images_dir.iterdir()):
        images_dir.rmdir()

    print(f"\nDone. Output: {output_root}")
    print(f"Articles written:   {len(written_paths)}")
    print(f"Category indexes:   {cat_indexes_written}")
    print(f"Images downloaded:  {sum(1 for v in image_cache.values() if v)}")


if __name__ == "__main__":
    main()
