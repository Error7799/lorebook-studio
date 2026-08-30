"""
Social media handle detection and stat scraping.
All data from public pages — no API keys required.
Instagram/TikTok/YouTube scraping is best-effort; sites may change their HTML.
"""

import re
import time
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BROWSER_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(BROWSER_HEADERS)
_retry = Retry(total=2, backoff_factor=0.4,
               status_forcelist=(429, 500, 502, 503, 504),
               allowed_methods=("GET",))
SESSION.mount("https://", HTTPAdapter(max_retries=_retry, pool_maxsize=8))

# Regex patterns to extract handles from URLs
PLATFORM_RE = {
    "instagram":  re.compile(r"instagram\.com/([A-Za-z0-9_.]+)/?(?:\?|$)"),
    "twitter":    re.compile(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)/?(?:\?|$)"),
    "tiktok":     re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)/?(?:\?|$)"),
    "youtube":    re.compile(r"youtube\.com/(?:c/|channel/|user/|@)([A-Za-z0-9_.@-]+)/?"),
    "facebook":   re.compile(r"facebook\.com/([A-Za-z0-9_.]+)/?(?:\?|$)"),
    "spotify":    re.compile(r"open\.spotify\.com/artist/([A-Za-z0-9]+)"),
    "soundcloud": re.compile(r"soundcloud\.com/([A-Za-z0-9_-]+)/?(?:\?|$)"),
    "twitch":     re.compile(r"twitch\.tv/([A-Za-z0-9_]+)/?(?:\?|$)"),
}

_SKIP_HANDLES = {
    "wiki", "wikipedia", "commons", "help", "about", "contact",
    "support", "blog", "news", "press", "home", "index", "login",
    "signup", "explore", "search", "intent", "share", "hashtag",
    "watch", "playlist", "events", "groups", "pages", "profile",
}


def find_social_handles(external_links):
    """
    Given a list of external URLs (from Wikipedia), extract social handles.
    Returns { platform: { "handle": str, "url": str } }
    """
    handles = {}
    for url in external_links:
        url = url.strip()
        for platform, pat in PLATFORM_RE.items():
            if platform in handles:
                continue
            m = pat.search(url)
            if not m:
                continue
            handle = m.group(1).strip("/").lstrip("@")
            if handle.lower() in _SKIP_HANDLES or len(handle) < 2:
                continue
            handles[platform] = {
                "handle": handle,
                "url":    url.split("?")[0],
            }
    return handles


def _get(url, extra=None, timeout=14):
    """Safe GET — returns Response or None on error."""
    try:
        return SESSION.get(url, headers=extra or None, timeout=timeout)
    except Exception:
        return None


# ── per-platform scrapers ─────────────────────────────────────────────────────

def _scrape_instagram(username):
    r = _get(f"https://www.instagram.com/{username}/")
    if not r or r.status_code != 200:
        return {"scraped": False, "note": "HTTP error"}

    for pat in (
        r'"edge_followed_by":\{"count":(\d+)\}',
        r'"follower_count":(\d+)',
        r'"followers":(\d+)',
    ):
        m = re.search(pat, r.text)
        if m:
            return {"scraped": True, "followers": int(m.group(1))}

    return {"scraped": False, "note": "Blocked or format changed"}


def _scrape_tiktok(username):
    r = _get(f"https://www.tiktok.com/@{username}")
    if not r or r.status_code != 200:
        return {"scraped": False, "note": "HTTP error"}

    # Try __NEXT_DATA__ JSON blob first
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL
    )
    if m:
        try:
            data  = json.loads(m.group(1))
            stats = (
                data.get("props", {})
                    .get("pageProps", {})
                    .get("userInfo", {})
                    .get("stats", {})
            )
            if stats:
                return {
                    "scraped":   True,
                    "followers": stats.get("followerCount"),
                    "following": stats.get("followingCount"),
                    "hearts":    stats.get("heartCount"),
                    "videos":    stats.get("videoCount"),
                }
        except Exception:
            pass

    # Fallback: raw pattern search
    m = re.search(r'"followerCount":(\d+)', r.text)
    if m:
        return {"scraped": True, "followers": int(m.group(1))}

    return {"scraped": False, "note": "Blocked or format changed"}


def _scrape_youtube(handle):
    if handle.startswith("UC") and len(handle) > 20:
        url = f"https://www.youtube.com/channel/{handle}"
    else:
        url = f"https://www.youtube.com/@{handle}"

    r = _get(url)
    if not r or r.status_code != 200:
        return {"scraped": False, "note": "HTTP error"}

    for pat in (
        r'"subscriberCountText":\{"simpleText":"([^"]+)"\}',
        r'"subscriberCountText":\{[^}]*"runs":\[.*?"text":"([^"]+)"',
    ):
        m = re.search(pat, r.text)
        if m:
            return {"scraped": True, "subscribers": m.group(1)}

    return {"scraped": False, "note": "Could not extract subscriber count"}


_SCRAPERS = {
    "instagram": _scrape_instagram,
    "tiktok":    _scrape_tiktok,
    "youtube":   _scrape_youtube,
}


STATS_BUDGET = 15.0     # seconds spent on live stats for one entity


def gather_social_stats(handles, budget=STATS_BUDGET):
    """
    Given { platform: { handle, url } }, attempt to fetch live follower/sub counts.
    Returns the same dict augmented with scraped stats.

    A wall-clock budget caps the whole attempt. Instagram and X answer an
    unauthenticated request by stalling rather than refusing, and with retries
    on top a single entity could otherwise spend a minute on follower counts
    that were never going to arrive — while the user watches a spinner. Handles
    still come back either way; only the live numbers are given up on.
    """
    stats = {}
    started = time.monotonic()
    for platform, info in handles.items():
        result = dict(info)
        scraper = _SCRAPERS.get(platform)
        if not scraper:
            result["scraped"] = False
        elif time.monotonic() - started > budget:
            result["scraped"] = False
            result["note"] = "skipped — live stats were taking too long"
        else:
            try:
                result.update(scraper(info["handle"]))
            except Exception as exc:
                result["scraped"] = False
                result["note"] = f"could not scrape ({exc})"
        stats[platform] = result
    return stats
