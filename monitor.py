#!/usr/bin/env python3
"""Camera West drop monitor: detects new items in /collections/drop and pushes a phone alert via ntfy."""
import json, os, re, sys, time, urllib.request, urllib.error
from html import unescape

BASE = "https://camerawest.com"
COLLECTION = f"{BASE}/collections/drop"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
MAX_INDIVIDUAL = 5
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def http_get(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            print(f"GET {url} failed ({e}), try {i+1}/{retries}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None


def fetch_json_feed():
    items, page = {}, 1
    while page <= 10:
        body = http_get(f"{COLLECTION}/products.json?limit=250&page={page}")
        if body is None:
            return None
        try:
            products = json.loads(body)["products"]
        except Exception:
            return None
        if not products:
            break
        for p in products:
            variants = p.get("variants") or []
            prices = [float(v["price"]) for v in variants if v.get("price")]
            imgs = p.get("images") or []
            items[str(p["id"])] = {
                "title": p.get("title", "").strip(),
                "url": f"{BASE}/products/{p['handle']}",
                "price": min(prices) if prices else None,
                "image": imgs[0]["src"] if imgs else None,
                "available": any(v.get("available") for v in variants),
            }
        if len(products) < 250:
            break
        page += 1
    return items or None


def fetch_html_fallback():
    items = {}
    for page in range(1, 11):
        body = http_get(f"{COLLECTION}?sort_by=created-descending&page={page}")
        if body is None:
            return items or None
        found = re.findall(r'href="(/(?:collections/drop/)?products/([^"?#]+))[^"]*"', body)
        new = 0
        for _, handle in found:
            if handle not in items:
                items[handle] = {"title": handle.replace("-", " ").title(),
                                 "url": f"{BASE}/products/{handle}", "price": None,
                                 "image": None, "available": True}
                new += 1
        if new == 0:
            break
    return items or None


def notify(title, message, click=None, image=None, priority=5, tags="camera,rotating_light"):
    if not NTFY_TOPIC:
        print(f"[no NTFY_TOPIC] {title}\n{message}")
        return
    payload = {"topic": NTFY_TOPIC, "title": title, "message": message,
               "priority": priority, "tags": tags.split(",")}
    if click:
        payload["click"] = click
    if image:
        payload["attach"] = image
    req = urllib.request.Request(NTFY_SERVER, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def fmt(item):
    price = f"${item['price']:,.0f}" if item.get("price") else ""
    return f"{item['title']} {price}".strip()


def main():
    items = fetch_json_feed()
    source = "json"
    if items is None:
        items, source = fetch_html_fallback(), "html"
    if not items:
        print("Could not read the collection (blocked or layout changed).", file=sys.stderr)
        return 1

    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
    seen = set(state.get("seen", []))
    if state.get("source") and state["source"] != source:
        # id scheme changed between json/html; reseed quietly rather than spam
        print("Source type changed, reseeding.")
        seen = set()
        state["initialized"] = False

    new_ids = [i for i in items if i not in seen]
    print(f"source={source} total={len(items)} new={len(new_ids)} initialized={state.get('initialized', False)}")

    if state.get("initialized") and new_ids:
        new_items = [items[i] for i in new_ids]
        # healthy-feed guard: a huge jump likely means the feed glitched, not a real drop
        if len(new_items) > 150:
            print("Suspiciously many new items; skipping alert.", file=sys.stderr)
        else:
            for it in new_items[:MAX_INDIVIDUAL]:
                notify("NEW at Camera West", fmt(it), click=it["url"], image=it["image"])
            rest = new_items[MAX_INDIVIDUAL:]
            if rest:
                lines = "\n".join(fmt(it) for it in rest[:15])
                notify(f"+{len(rest)} more new items", lines, click=COLLECTION + "?sort_by=created-descending")

    # keep everything ever seen, so an item that goes sold-out then returns isn't re-alerted
    state = {"seen": sorted(seen | set(items)), "initialized": True, "source": source,
             "updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
