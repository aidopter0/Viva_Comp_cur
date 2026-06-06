from __future__ import annotations

import ipaddress
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
ALLOWED_HOST_SUFFIXES = ("talabat.com",)
HTTP_MAX_ATTEMPTS = 10


def _is_private_or_loopback_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def validate_store_url(store_url: str) -> str:
    raw = (store_url or "").strip()
    if not raw:
        raise ValueError("store URL is required")
    u = urllib.parse.urlparse(raw)
    if u.scheme.lower() != "https":
        raise ValueError("store URL must use https")
    host = (u.hostname or "").strip().lower()
    if not host:
        raise ValueError("store URL host missing")
    if host == "localhost" or _is_private_or_loopback_host(host):
        raise ValueError("store URL host is not allowed")
    if not any(host == s or host.endswith(f".{s}") for s in ALLOWED_HOST_SUFFIXES):
        raise ValueError("store URL host must be talabat.com")
    return raw


def validate_item_url(url: str) -> str:
    """Same host/https rules as storefront URLs; use for product or listing item links."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("item URL is required")
    try:
        validate_store_url(raw)
    except ValueError as e:
        raise ValueError(str(e).replace("store URL", "item URL")) from e
    return raw


def is_grocery_product_url(url: str) -> bool:
    """True for talabat grocery PDP paths containing a ``product`` segment, e.g. ``.../product/{slug}/s/{sku}``."""
    try:
        u = urllib.parse.urlparse(validate_item_url(url))
    except ValueError:
        return False
    parts = [p.lower() for p in u.path.strip("/").split("/") if p]
    return "product" in parts


def fetch_product_page_item(url: str) -> dict[str, Any]:
    """Load a single-item grocery PDP; ``initialState.item`` holds id (UUID), sku, prices, etc."""
    html = fetch_html(validate_item_url(url))
    nd = parse_next_data(html)
    item = nd.get("props", {}).get("pageProps", {}).get("initialState", {}).get("item")
    if not isinstance(item, dict):
        raise ValueError("page has no product payload (expected initialState.item)")
    if not str(item.get("id") or "").strip():
        raise ValueError("product payload has no item id")
    return item


def resolve_url_master_from_product_url(url: str) -> dict[str, str]:
    """
    Follow a full Talabat product URL and return fields for item_url_master.
    The path ``.../product/{slug}/s/{sku}`` maps to JSON ``item.id`` (UUID), not the sku token.
    """
    raw = validate_item_url(url)
    item = fetch_product_page_item(raw)
    return {
        "source_url": raw,
        "item_id": str(item["id"]).strip(),
        "slug": str(item.get("slug") or "").strip(),
        "item_title": str(item.get("title") or "").strip(),
    }


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
        },
        method="GET",
    )
    for attempt in range(HTTP_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or 500 <= e.code < 600
            if (not retryable) or attempt >= HTTP_MAX_ATTEMPTS - 1:
                raise
            wait = min(120.0, 2.0 * (2**attempt))
            if e.code == 429 and e.headers:
                ra = e.headers.get("Retry-After")
                if ra:
                    try:
                        wait = max(wait, float(ra))
                    except ValueError:
                        pass
            time.sleep(wait)
        except urllib.error.URLError:
            if attempt >= HTTP_MAX_ATTEMPTS - 1:
                raise
            time.sleep(min(8.0, 1.0 * (2**attempt)))
    raise RuntimeError("unreachable retry loop")


def parse_next_data(html: str) -> dict[str, Any]:
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("page did not contain __NEXT_DATA__")
    return json.loads(m.group(1))


def parse_store_components(store_url: str) -> tuple[str, str, str, str, str]:
    u = urllib.parse.urlparse(validate_store_url(store_url))
    host = u.netloc
    aid = (urllib.parse.parse_qs(u.query).get("aid") or [""])[0]
    parts = [p for p in u.path.strip("/").split("/") if p]
    gi = parts.index("grocery")
    country = parts[gi - 1]
    branch = parts[gi + 1]
    slug = parts[gi + 2]
    return country, branch, slug, aid, host


def _first_non_empty_str(*candidates: object) -> str:
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s:
            return s
    return ""


def _pick_from_mapping(obj: Any, *keys: str) -> str:
    if not isinstance(obj, dict):
        return ""
    for k in keys:
        s = _first_non_empty_str(obj.get(k))
        if s:
            return s
    return ""


def fetch_store_metadata(store_url: str) -> dict[str, str]:
    """Fetch storefront page once; return store label, chain brand name, UUID, and normalized URL."""
    url = validate_store_url(store_url)
    html = fetch_html(url)
    nd = parse_next_data(html)
    init = nd.get("props", {}).get("pageProps", {}).get("initialState", {})
    g = init.get("groceryStore") or {}
    v = init.get("vendor") or {}
    vd = init.get("vendorData") or {}
    if not isinstance(g, dict):
        g = {}
    if not isinstance(v, dict):
        v = {}
    if not isinstance(vd, dict):
        vd = {}

    uuid = _first_non_empty_str(
        g.get("dhVendorId"),
        v.get("id"),
        vd.get("vendorId"),
    )
    if not uuid:
        raise ValueError("could not resolve store UUID from storefront payload")

    store_label = _pick_from_mapping(
        g, "branchName", "name", "englishName", "storeName", "displayName"
    ) or _pick_from_mapping(v, "branchName", "name", "englishName", "storeName", "displayName")
    if not store_label:
        try:
            _, _, slug, _, _ = parse_store_components(url)
            store_label = slug.replace("-", " ").strip().title() or "Store"
        except Exception:  # noqa: BLE001
            store_label = "Store"

    brand_name = _pick_from_mapping(
        g, "chainName", "brandName", "verticalName", "retailerName"
    ) or _pick_from_mapping(v, "chainName", "brandName", "verticalName", "retailerName")
    if not brand_name:
        g_chain = g.get("chain")
        if isinstance(g_chain, dict):
            brand_name = _pick_from_mapping(g_chain, "name", "englishName", "title")
    if not brand_name:
        chain = v.get("chain")
        if isinstance(chain, dict):
            brand_name = _pick_from_mapping(chain, "name", "englishName", "title")
    # Current Talabat grocery SSR often omits vendor/chain; groceryStore.name is the chain (e.g. Carrefour, Gala Supermarket).
    if not brand_name:
        brand_name = _pick_from_mapping(g, "name", "englishName")
    if not brand_name and v:
        brand_name = _pick_from_mapping(v, "name", "englishName")
    if not brand_name:
        try:
            _, _, slug, _, _ = parse_store_components(url)
            brand_name = slug.replace("-", " ").strip().title()
        except Exception:  # noqa: BLE001
            brand_name = ""
    if not brand_name:
        raise ValueError("could not determine chain brand from storefront payload")

    return {
        "store_label": store_label,
        "brand_name": brand_name,
        "store_uuid": uuid,
        "talabat_url": url,
    }


def resolve_store_uuid(store_url: str) -> str:
    return fetch_store_metadata(store_url)["store_uuid"]


def load_store_categories(store_url: str) -> list[dict[str, Any]]:
    """Category tree from storefront ``initialState.categories`` (names + slugs)."""
    html = fetch_html(validate_store_url(store_url))
    nd = parse_next_data(html)
    cats = nd.get("props", {}).get("pageProps", {}).get("initialState", {}).get("categories") or []
    out: list[dict[str, Any]] = []
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        cslug = str(cat.get("slug") or "").strip()
        if not cslug:
            continue
        subs: list[dict[str, str]] = []
        for sub in cat.get("subCategories") or []:
            if not isinstance(sub, dict):
                continue
            sslug = str(sub.get("slug") or "").strip()
            if sslug:
                subs.append(
                    {
                        "name": str(sub.get("name") or sslug).strip(),
                        "slug": sslug,
                    }
                )
        out.append(
            {
                "name": str(cat.get("name") or cslug).strip(),
                "slug": cslug,
                "subcategories": subs,
            }
        )
    return out


def iter_category_pairs(store_url: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for cat in load_store_categories(store_url):
        for sub in cat.get("subcategories") or []:
            pairs.append((str(cat["slug"]), str(sub["slug"])))
    return pairs


def build_grocery_product_url(store_url: str, item: dict[str, Any]) -> str:
    """Full Talabat PDP URL from listing item payload (slug + sku)."""
    pslug = str(item.get("slug") or "").strip()
    sku = str(item.get("sku") or "").strip()
    if not pslug or not sku:
        return ""
    country, branch, slug, aid, host = parse_store_components(store_url)
    base = f"https://{host}/{country}/grocery/{branch}/{slug}/product/{pslug}/s/{sku}"
    return base + ("?" + urllib.parse.urlencode({"aid": aid}) if aid else "")


def category_url(store_url: str, cat_slug: str, sub_slug: str) -> str:
    country, branch, slug, aid, host = parse_store_components(store_url)
    base = f"https://{host}/{country}/grocery/{branch}/{slug}/{cat_slug}/{sub_slug}"
    return base + ("?" + urllib.parse.urlencode({"aid": aid}) if aid else "")


def collect_catalog(store_url: str, *, page_delay_s: float = 0.3) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for cslug, sslug in iter_category_pairs(store_url):
        base_url = category_url(store_url, cslug, sslug)
        page_count = 1
        page = 1
        while page <= page_count:
            url = base_url
            if page > 1:
                sep = "&" if "?" in base_url else "?"
                url = f"{base_url}{sep}page={page}"
            try:
                html = fetch_html(url)
                nd = parse_next_data(html)
                idata = (
                    nd.get("props", {})
                    .get("pageProps", {})
                    .get("initialState", {})
                    .get("itemsData")
                    or {}
                )
                items = idata.get("items") or []
                if page == 1:
                    page_count = max(1, int(idata.get("pageCount") or 1))
                elif not items:
                    break
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    iid = str(it.get("id") or "").strip()
                    if not iid:
                        continue
                    if not it.get("__source_url"):
                        it["__source_url"] = url
                    merged[iid] = it
            except Exception:
                pass
            page += 1
            time.sleep(page_delay_s)
    return merged


class FetchItemsError(Exception):
    """Raised when Talabat item fetch fails for a URL."""


def fetch_items_from_source_url(source_url: str) -> dict[str, dict]:
    """
    Build id -> item for price lookup.
    Supports full **product** URLs (PDP) or **category listing** URLs (itemsData.items).
    Raises FetchItemsError when the page cannot be loaded or parsed.
    """
    url = validate_item_url(source_url)
    if is_grocery_product_url(url):
        try:
            it = fetch_product_page_item(url)
            iid = str(it.get("id") or "").strip()
            if not iid:
                raise FetchItemsError("product page has no item id")
            row = dict(it)
            row["__source_url"] = url
            return {iid: row}
        except FetchItemsError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise FetchItemsError(str(exc)) from exc
    try:
        html = fetch_html(url)
        nd = parse_next_data(html)
    except Exception as exc:  # noqa: BLE001
        raise FetchItemsError(str(exc)) from exc
    items = (
        nd.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("itemsData", {})
        .get("items")
        or []
    )
    out: dict[str, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or "").strip()
        if iid:
            out[iid] = it
    return out
