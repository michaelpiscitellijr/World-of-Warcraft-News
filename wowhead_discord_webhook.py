#!/usr/bin/env python3
"""Post new Wowhead news to a Discord webhook, filtered by Wowhead news type.

Setup:
  export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
  python wowhead_discord_webhook.py

Run it on a schedule (cron, GitHub Actions, etc.). The feed's TTL is 30 minutes,
so running every 15-30 minutes is plenty.

First run only records what is already in the feed (so you don't get flooded).
Pass --post-existing to post the current backlog instead.
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

FEED_URL = "https://www.wowhead.com/news/rss/all"
# Wowhead's <category> values: "Live" = Retail WoW, "Forever" = WoW: Forever.
ALLOWED_CATEGORIES = {"Live", "Forever"}
STATE_FILE = "wowhead_seen.json"
MAX_SEEN = 500
UA = "Mozilla/5.0 (compatible; wowhead-discord-webhook/1.0)"
MEDIA_NS = "{http://search.yahoo.com/mrss/}"
COLORS = {"Live": 0x1E90FF, "Forever": 0xF5A623}


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=30)


def fetch_items():
    root = ET.fromstring(http(FEED_URL).read())
    items = []
    for it in root.iter("item"):
        desc = html.unescape(it.findtext("description") or "")
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"Continue reading\s*»", "", desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        media = it.find(f"{MEDIA_NS}content")
        pub = it.findtext("pubDate")
        items.append({
            "guid": it.findtext("guid") or it.findtext("link"),
            "title": it.findtext("title") or "",
            "link": it.findtext("link") or "",
            "category": (it.findtext("category") or "").strip(),
            "description": desc[:400],
            "image": media.get("url") if media is not None else None,
            "published": parsedate_to_datetime(pub).isoformat() if pub else None,
        })
    return items


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(seen):
    with open(STATE_FILE, "w") as f:
        json.dump(seen[-MAX_SEEN:], f)


def post(webhook, item):
    embed = {
        "title": item["title"][:256],
        "url": item["link"],
        "description": item["description"],
        "color": COLORS.get(item["category"], 0x888888),
        "author": {"name": "Wowhead"},
        "footer": {"text": item["category"]},
    }
    if item["image"]:
        embed["image"] = {"url": item["image"]}
    if item["published"]:
        embed["timestamp"] = item["published"]
    body = json.dumps({"embeds": [embed]}).encode()
    while True:
        try:
            http(webhook, body, {"Content-Type": "application/json"})
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", "2")) + 0.5)
                continue
            raise


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        sys.exit("Set DISCORD_WEBHOOK_URL")

    items = fetch_items()
    seen = load_state()

    if "--latest" in sys.argv:
        count = int(sys.argv[sys.argv.index("--latest") + 1])
        matches = [i for i in items if i["category"] in ALLOWED_CATEGORIES]
        matches.sort(key=lambda i: i["published"] or "", reverse=True)
        for item in reversed(matches[:count]):  # oldest of the N first
            post(webhook, item)
            print("Posted:", item["title"])
            time.sleep(1)
        # Mark everything currently in the feed as seen so the
        # scheduled run doesn't post these (or older ones) again.
        seen = seen or []
        seen += [i["guid"] for i in items if i["guid"] not in seen]
        save_state(seen)
        return

    if seen is None and "--post-existing" not in sys.argv:
        save_state([i["guid"] for i in items])
        print(f"First run: recorded {len(items)} existing items, posted none.")
        return

    seen = seen or []
    new = [i for i in items if i["guid"] not in seen]
    # Oldest first so Discord shows them in order.
    for item in reversed(new):
        if item["category"] in ALLOWED_CATEGORIES:
            post(webhook, item)
            print("Posted:", item["title"])
            time.sleep(1)
        seen.append(item["guid"])  # mark filtered-out items as seen too
    save_state(seen)


if __name__ == "__main__":
    main()
