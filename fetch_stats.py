#!/usr/bin/env python3
"""
Qarar live stats: pulls views from Instagram, Facebook, TikTok and LinkedIn
and writes stats.json for qararmarketing.com to read.

- Pure Python standard library (no pip install needed) so it runs in
  GitHub Actions, on cPanel cron, or on any server with python3.
- Each platform is optional: leave its secrets empty and it is skipped.
- If a platform fails, the last good number from the previous stats.json
  is kept, so the website never shows a broken or zero value.

Run:   python3 fetch_stats.py                 (writes ./stats.json)
       python3 fetch_stats.py --out public/stats.json
       python3 fetch_stats.py --demo          (fake numbers, to test the site)
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

WINDOW_DAYS = int(os.getenv("STATS_WINDOW_DAYS", "90"))
GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v25.0")
LINKEDIN_VERSION = os.getenv("LINKEDIN_VERSION", "202610")  # YYYYMM; bump about once a year
CHUNK_DAYS = 30  # Meta & TikTok cap one request to ~30 days, so we page through the window


# ---------- helpers ----------
def env(name):
    v = os.getenv(name, "").strip()
    return v or None


def http_get(url, params=None, headers=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe="(),:")
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code} from {url.split('?')[0]}: {body}") from None


def http_post(url, payload, headers=None):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def chunks(start, end, size=CHUNK_DAYS):
    """Yield (chunk_start, chunk_end) date pairs covering [start, end)."""
    cur = start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=size), end)
        yield cur, nxt
        cur = nxt


def unix(d):
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


# ---------- platforms ----------
def instagram_views(start, end):
    """Instagram Business/Creator account: 'views' metric (replaced impressions in 2025)."""
    ig_id, token = env("IG_USER_ID"), env("META_ACCESS_TOKEN")
    if not (ig_id and token):
        return None
    total = 0
    for a, b in chunks(start, end):
        res = http_get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{ig_id}/insights",
            {"metric": "views", "period": "day", "metric_type": "total_value",
             "since": unix(a), "until": unix(b), "access_token": token},
        )
        for m in res.get("data", []):
            if m.get("name") == "views":
                total += int(m.get("total_value", {}).get("value", 0))
    return total


def facebook_views(start, end):
    """Facebook Page: 'page_media_view' (replaced page_impressions in Nov 2025)."""
    page_id, token = env("FB_PAGE_ID"), env("FB_PAGE_TOKEN") or env("META_ACCESS_TOKEN")
    if not (page_id and token):
        return None
    total = 0
    for a, b in chunks(start, end):
        res = http_get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}/insights",
            {"metric": "page_media_view", "period": "day",
             "since": unix(a), "until": unix(b), "access_token": token},
        )
        for m in res.get("data", []):
            if m.get("period") == "day":
                total += sum(int(v.get("value") or 0) for v in m.get("values", []))
    return total


def tiktok_access_token():
    """TikTok Business access tokens expire after 24h; refresh from the long-lived refresh token."""
    if env("TIKTOK_REFRESH_TOKEN") and env("TIKTOK_APP_ID") and env("TIKTOK_APP_SECRET"):
        res = http_post(
            "https://business-api.tiktok.com/open_api/v1.3/tt_user/oauth2/refresh_token/",
            {"client_id": env("TIKTOK_APP_ID"), "client_secret": env("TIKTOK_APP_SECRET"),
             "grant_type": "refresh_token", "refresh_token": env("TIKTOK_REFRESH_TOKEN")},
        )
        tok = (res.get("data") or {}).get("access_token")
        if tok:
            return tok
        raise RuntimeError(f"TikTok token refresh failed: {res.get('message')}")
    return env("TIKTOK_ACCESS_TOKEN")


def tiktok_views(start, end):
    """TikTok Business account: daily 'video_views' (TikTok has no data for today, only up to yesterday)."""
    biz_id = env("TIKTOK_BUSINESS_ID")
    if not biz_id or not (env("TIKTOK_ACCESS_TOKEN") or env("TIKTOK_REFRESH_TOKEN")):
        return None
    token = tiktok_access_token()
    total = 0
    for a, b in chunks(start, end):
        res = http_get(
            "https://business-api.tiktok.com/open_api/v1.3/business/get/",
            {"business_id": biz_id, "start_date": a.isoformat(),
             "end_date": (b - dt.timedelta(days=1)).isoformat(),
             "fields": json.dumps(["video_views"])},
            headers={"Access-Token": token},
        )
        if res.get("code") not in (0, None):
            raise RuntimeError(f"TikTok: {res.get('message')}")
        for day in (res.get("data") or {}).get("metrics", []):
            total += int(day.get("video_views") or 0)
    return total


def linkedin_views(start, end):
    """LinkedIn Company Page: post impressions (LinkedIn's closest equivalent to 'views')."""
    org_id, token = env("LINKEDIN_ORG_ID"), env("LINKEDIN_ACCESS_TOKEN")
    if not (org_id and token):
        return None
    urn = urllib.parse.quote(f"urn:li:organization:{org_id}", safe="")
    ti = f"(timeRange:(start:{unix(start)*1000},end:{unix(end)*1000}),timeGranularityType:DAY)"
    url = (f"https://api.linkedin.com/rest/organizationalEntityShareStatistics"
           f"?q=organizationalEntity&organizationalEntity={urn}&timeIntervals={ti}")
    res = http_get(url, headers={
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    })
    return sum(int(e.get("totalShareStatistics", {}).get("impressionCount", 0))
               for e in res.get("elements", []))


PLATFORMS = {
    "instagram": instagram_views,
    "facebook": facebook_views,
    "tiktok": tiktok_views,
    "linkedin": linkedin_views,
}


# ---------- main ----------
def compact(n):
    """191234 -> '191K', 1250000 -> '1.3M' (same style as the website)."""
    if n >= 1_000_000:
        s = f"{n/1_000_000:.1f}".rstrip("0").rstrip(".")
        return s + "M"
    if n >= 10_000:
        return f"{round(n/1000)}K"
    if n >= 1_000:
        return f"{n/1000:.1f}".rstrip("0").rstrip(".") + "K"
    return str(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="stats.json")
    ap.add_argument("--demo", action="store_true", help="write fake numbers to test the website")
    args = ap.parse_args()

    try:
        with open(args.out) as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = {}
    prev_platforms = previous.get("platforms", {})

    today = dt.datetime.now(dt.timezone.utc).date()
    end = today  # exclusive: data up to and including yesterday
    start = end - dt.timedelta(days=WINDOW_DAYS)

    platforms, errors = {}, {}
    for name, fn in PLATFORMS.items():
        if args.demo:
            platforms[name] = {"views": {"instagram": 121400, "facebook": 38900,
                                         "tiktok": 27600, "linkedin": 5300}[name],
                               "updated": today.isoformat(), "live": True}
            continue
        try:
            v = fn(start, end)
            if v is None:
                print(f"- {name}: skipped (no secrets set)")
                continue
            platforms[name] = {"views": v, "updated": today.isoformat(), "live": True}
            print(f"✓ {name}: {v:,} views")
        except Exception as e:  # keep last good value so the site never breaks
            errors[name] = str(e)[:300]
            print(f"✗ {name}: {e}", file=sys.stderr)
            if name in prev_platforms:
                platforms[name] = {**prev_platforms[name], "live": False}

    total = sum(p["views"] for p in platforms.values())
    out = {
        "window_days": WINDOW_DAYS,
        "period": {"from": start.isoformat(), "to": (end - dt.timedelta(days=1)).isoformat()},
        "total_views": total,
        "total_views_compact": compact(total),
        "platforms": platforms,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "errors": errors,
    }
    if not platforms:
        if not errors:  # nothing configured yet: skip quietly (no daily failure emails)
            print("No API keys set yet; nothing to update.")
            sys.exit(0)
        print("No platform returned data; keeping the old stats.json.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nTotal: {total:,} views ({out['total_views_compact']}) in the last {WINDOW_DAYS} days -> {args.out}")
    # Fail the run (so GitHub emails you) only if every configured platform failed
    if errors and not any(p.get("live") for p in platforms.values()):
        sys.exit(2)


if __name__ == "__main__":
    main()
