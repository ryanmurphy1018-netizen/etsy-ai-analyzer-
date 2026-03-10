import os
import json
import re
import requests
import cloudscraper
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, Response, stream_with_context
import anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# ──────────────────────────────────────────────
# Demo listings for instant testing
# ──────────────────────────────────────────────
DEMO_LISTINGS = {
    "demo1": {
        "title": "Handmade Silver Ring",
        "price": "45.00",
        "currency": "USD",
        "shop_name": "SilverSmithsJewelry",
        "category_path": "Jewelry > Rings > Statement Rings",
        "tags": ["silver ring", "handmade ring", "jewelry", "ring", "silver"],
        "rating": "4.2",
        "review_count": "12",
        "description": "Beautiful handmade silver ring. Made with love. Great gift for anyone. Free shipping on orders over $35. Processing time 3-5 business days.",
        "image_url": "https://i.etsystatic.com/isla/c0d3a6/39034097/isla_fullxfull.39034097_evck5k0g.jpg",
        "availability": "InStock",
    },
    "demo2": {
        "title": "Minimalist Gold Layering Necklace Set for Women | Dainty Chain Necklace Gift",
        "price": "28.00",
        "currency": "USD",
        "shop_name": "GoldDaintyJewels",
        "category_path": "Jewelry > Necklaces > Chain Necklaces",
        "tags": [
            "layering necklace", "gold necklace", "dainty necklace", "necklace set",
            "minimalist jewelry", "gift for her", "gold chain", "delicate necklace",
            "birthday gift women", "jewelry gift", "14k gold necklace", "chain necklace"
        ],
        "rating": "4.9",
        "review_count": "2847",
        "description": "Our bestselling minimalist layering necklace set includes two dainty 14K gold-filled chains. Perfect for everyday wear or gifting. Hypoallergenic and tarnish-resistant. Ships in 1-2 business days in a gift box.",
        "image_url": "https://i.etsystatic.com/isla/8e4f4a/29842371/isla_fullxfull.29842371.jpg",
        "availability": "InStock",
    },
}

DEMO_COMPETITORS = [
    {"name": "Dainty Gold Necklace Layering Set", "price": "24.99", "currency": "USD", "rating": "4.8", "reviews": "3421"},
    {"name": "Minimalist Silver Chain Necklace", "price": "19.95", "currency": "USD", "rating": "4.7", "reviews": "1832"},
    {"name": "Gold Filled Layered Necklace Women", "price": "32.00", "currency": "USD", "rating": "4.9", "reviews": "956"},
    {"name": "Simple Gold Chain Necklace Set", "price": "22.00", "currency": "USD", "rating": "4.6", "reviews": "2100"},
    {"name": "14K Gold Dainty Necklace Gift", "price": "38.00", "currency": "USD", "rating": "4.9", "reviews": "4200"},
]


def extract_listing_id(url: str) -> str | None:
    match = re.search(r"/listing/(\d+)", url)
    return match.group(1) if match else None


def fetch_via_etsy_api(listing_id: str, api_key: str, shared_secret: str = "") -> dict:
    """Fetch listing via official Etsy Open API v3.
    x-api-key format: keystring:sharedsecret (per Etsy docs)
    """
    base = "https://openapi.etsy.com/v3/application"
    # Build the x-api-key value — Etsy requires keystring:sharedsecret
    if shared_secret and ":" not in api_key:
        key_header = f"{api_key}:{shared_secret}"
    else:
        key_header = api_key  # user may have already included the colon+secret
    headers = {
        "x-api-key": key_header,
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        r = requests.get(f"{base}/listings/{listing_id}", headers=headers, timeout=10)
        r.raise_for_status()
        ld = r.json()

        data = {
            "title": ld.get("title", ""),
            "description": ld.get("description", "")[:600],
            "price": str(ld.get("price", {}).get("amount", "") / 100) if isinstance(ld.get("price"), dict) else "",
            "currency": ld.get("price", {}).get("currency_code", "USD") if isinstance(ld.get("price"), dict) else "USD",
            "tags": ld.get("tags", []),
            "category_path": " > ".join(ld.get("taxonomy_path", [])),
            "shop_name": "",
            "rating": "",
            "review_count": str(ld.get("num_favorers", "")),
            "availability": "InStock" if ld.get("state") == "active" else "",
        }

        # Fetch images
        img_r = requests.get(f"{base}/listings/{listing_id}/images", headers=headers, timeout=8)
        if img_r.ok:
            imgs = img_r.json().get("results", [])
            if imgs:
                data["image_url"] = imgs[0].get("url_fullxfull", "")

        # Fetch shop name
        shop_id = ld.get("shop_id")
        if shop_id:
            shop_r = requests.get(f"{base}/shops/{shop_id}", headers=headers, timeout=8)
            if shop_r.ok:
                data["shop_name"] = shop_r.json().get("shop_name", "")

        return data
    except Exception as e:
        return {"error": str(e)}


def fetch_via_scraping(url: str) -> dict:
    """Scrape public Etsy listing page using cloudscraper to bypass Cloudflare."""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "darwin", "mobile": False}
    )
    scraper.headers.update(HEADERS)

    try:
        resp = scraper.get(url, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except requests.HTTPError as e:
        return {"error": f"Etsy returned {e.response.status_code}. Try the demo mode or add an Etsy API key."}
    except Exception as e:
        return {"error": f"Could not fetch listing: {e}"}

    soup = BeautifulSoup(resp.text, "html.parser")
    data = {}

    def meta(prop=None, name=None):
        tag = soup.find("meta", property=prop) if prop else soup.find("meta", attrs={"name": name})
        return tag["content"].strip() if tag and tag.get("content") else None

    data["title"] = meta(prop="og:title") or ""
    data["description"] = meta(prop="og:description") or ""
    data["image_url"] = meta(prop="og:image") or ""
    data["url"] = url

    # JSON-LD Product schema
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string)
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if item.get("@type") == "Product":
                    data["title"] = data["title"] or item.get("name", "")
                    data["description"] = data["description"] or item.get("description", "")
                    offers = item.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    data["price"] = str(offers.get("price", ""))
                    data["currency"] = offers.get("priceCurrency", "USD")
                    data["availability"] = offers.get("availability", "")
                    data["rating"] = item.get("aggregateRating", {}).get("ratingValue", "")
                    data["review_count"] = item.get("aggregateRating", {}).get("reviewCount", "")
                    break
        except Exception:
            pass

    # Breadcrumb
    breadcrumb = soup.find("ol", {"aria-label": "breadcrumb"})
    if breadcrumb:
        crumbs = [a.get_text(strip=True) for a in breadcrumb.find_all("a")]
        data["category_path"] = " > ".join(crumbs)

    # Shop name
    shop_link = soup.find("a", href=re.compile(r"/shop/"))
    if shop_link:
        data["shop_name"] = shop_link.get_text(strip=True)

    # Tags from page source
    for script in soup.find_all("script"):
        if script.string and '"tags"' in script.string:
            tag_match = re.search(r'"tags":\s*(\[[^\]]+\])', script.string)
            if tag_match:
                try:
                    data["tags"] = json.loads(tag_match.group(1))
                    break
                except Exception:
                    pass

    if data.get("title") and " | " in data["title"]:
        data["title"] = data["title"].split(" | ")[0].strip()

    return data


def fetch_etsy_listing(url: str, etsy_api_key: str = "", etsy_shared_secret: str = "") -> dict:
    listing_id = extract_listing_id(url)
    if not listing_id:
        return {"error": "Could not extract listing ID from URL"}

    # Try official API first if key provided
    if etsy_api_key:
        result = fetch_via_etsy_api(listing_id, etsy_api_key, etsy_shared_secret)
        if "error" not in result:
            return result

    # Fall back to scraping
    return fetch_via_scraping(url)


def fetch_competitor_data(title: str) -> list[dict]:
    if not title:
        return []

    words = [w for w in title.split() if len(w) > 2][:4]
    query = " ".join(words)

    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "darwin", "mobile": False}
        )
        scraper.headers.update(HEADERS)
        search_url = f"https://www.etsy.com/search?q={requests.utils.quote(query)}&explicit=1"
        resp = scraper.get(search_url, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        competitors = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string)
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if item.get("@type") == "ItemList":
                        for el in item.get("itemListElement", [])[:8]:
                            listing = el.get("item", {})
                            offers = listing.get("offers", {})
                            if isinstance(offers, list):
                                offers = offers[0] if offers else {}
                            competitors.append({
                                "name": listing.get("name", "")[:80],
                                "price": offers.get("price", ""),
                                "currency": offers.get("priceCurrency", "USD"),
                                "rating": listing.get("aggregateRating", {}).get("ratingValue", ""),
                                "reviews": listing.get("aggregateRating", {}).get("reviewCount", ""),
                            })
                        break
            except Exception:
                pass

        return competitors[:8]
    except Exception:
        return []


def parse_pasted_text(text: str, url: str = "") -> dict:
    """Extract listing data from text pasted directly from an Etsy listing page."""
    data = {"url": url, "tags": [], "currency": "USD"}

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Price — look for $ pattern
    for line in lines:
        price_match = re.search(r"\$\s*([\d,]+\.?\d*)", line)
        if price_match:
            data["price"] = price_match.group(1).replace(",", "")
            break

    # Title — usually first substantial line or after "Etsy" header
    for line in lines[:10]:
        if len(line) > 15 and not line.startswith("http") and "$" not in line:
            if not any(skip in line.lower() for skip in ["etsy", "cart", "save", "share", "similar"]):
                data["title"] = line
                break

    # Shop name — look for "by ShopName" or "Visit shop"
    for line in lines:
        m = re.search(r"(?:by|from|shop[:\s]+)\s+([A-Za-z0-9_]+)", line, re.I)
        if m and len(m.group(1)) > 2:
            data["shop_name"] = m.group(1)
            break

    # Reviews / rating
    for line in lines:
        rating_m = re.search(r"(\d+\.?\d*)\s*(?:out of 5|stars?|★)", line, re.I)
        review_m = re.search(r"([\d,]+)\s*(?:reviews?|ratings?|sales?)", line, re.I)
        if rating_m:
            data["rating"] = rating_m.group(1)
        if review_m:
            data["review_count"] = review_m.group(1).replace(",", "")

    # Description — look for multi-sentence paragraph
    full_text = " ".join(lines)
    # Find the longest coherent paragraph as the description
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 80]
    if paragraphs:
        data["description"] = max(paragraphs, key=len)[:600]

    # If we got very little, just dump the raw text for Claude to work with
    if not data.get("title"):
        data["raw_text"] = text[:2000]
        data["title"] = "Etsy Listing (from pasted text)"

    return data


def build_analysis_prompt(listing: dict, competitors: list[dict]) -> str:
    comp_text = ""
    if competitors:
        comp_lines = []
        prices = []
        for c in competitors:
            line = f"  - \"{c['name'][:70]}\""
            if c.get("price"):
                line += f" — ${c['price']} {c.get('currency','USD')}"
                try:
                    prices.append(float(c["price"]))
                except Exception:
                    pass
            if c.get("reviews"):
                line += f" — {c['reviews']} reviews"
            comp_lines.append(line)
        comp_text = "\n".join(comp_lines)
        if prices:
            avg = sum(prices) / len(prices)
            comp_text += f"\n  Price range: ${min(prices):.2f}–${max(prices):.2f} | Avg: ${avg:.2f}"

    listing_price = listing.get("price", "unknown")

    return f"""You are a world-class Etsy shop consultant and SEO specialist with deep expertise in Etsy's algorithm, product photography, and e-commerce conversion optimization.

Analyze this Etsy listing and produce a detailed, honest, actionable report. Be specific — name real keywords, give exact price recommendations, rewrite the title, call out specific problems. Avoid generic advice.

## LISTING DATA
- **Title:** {listing.get('title', 'N/A')}
- **Price:** ${listing_price} {listing.get('currency','USD')}
- **Shop:** {listing.get('shop_name', 'Unknown')}
- **Category:** {listing.get('category_path', 'Unknown')}
- **Tags ({len(listing.get('tags',[]))} of 13):** {', '.join(listing.get('tags',[])) or 'None detected'}
- **Reviews:** {listing.get('review_count','?')} reviews | Rating: {listing.get('rating','?')}
- **Description snippet:** {(listing.get('description','') or '')[:500]}
- **Image:** {listing.get('image_url','N/A')}
{f"- **Raw page text:** {listing['raw_text']}" if listing.get('raw_text') else ''}

## TOP COMPETITOR LISTINGS
{comp_text or 'Could not load competitor data — provide analysis based on listing data alone.'}

---

Write a full report with these exact 6 sections using markdown. Be direct and specific. Include real examples, rewritten copy, and precise numbers.

## 🔍 Why This Listing Isn't Getting Sales
Top 3 specific diagnoses based on the data. Be honest and direct.

## 🔎 SEO Analysis
- Title score (X/10) with specific reasons
- Missing high-intent keywords buyers search for
- Tag analysis — what's working, what's missing, what to replace
- **Rewritten title:** provide a new optimized title in backticks
- **5 tag suggestions** with reasoning

## 💰 Pricing Analysis
- Price vs competitors (specific comparison)
- Is the price justified, too high, or too low?
- What changes would justify the current price OR what price is optimal
- **Recommended price:** give a specific number or range

## 🏆 Competition Analysis
- Who dominates this niche and why (specific observations)
- What sellers with 1000+ reviews do differently
- Is the market saturated or beatable?
- 2–3 specific differentiation strategies

## 📸 Image Quality Assessment
- Assess the likely image quality based on shop maturity and category
- Specific issues common to this product category
- 3 concrete improvements (lighting, composition, lifestyle shot, etc.)
- What the #1 thumbnail should look like

## ✅ Top 5 Priority Actions
Ranked by impact. Each action:
**Action:** [specific action]
**Why:** [why this matters for sales]
**How:** [exactly how to do it]

End with one motivating sentence for the seller.
"""


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    body = request.json or {}
    url = body.get("url", "").strip()
    etsy_api_key = body.get("etsy_api_key", "").strip()
    etsy_shared_secret = body.get("etsy_shared_secret", "").strip()
    demo_key = body.get("demo", "").strip()

    pasted_text = body.get("pasted_text", "").strip()

    # Demo mode
    if demo_key in DEMO_LISTINGS:
        listing = DEMO_LISTINGS[demo_key]
        competitors = DEMO_COMPETITORS
    elif pasted_text:
        # Paste mode — parse raw text the user copied from the Etsy page
        listing = parse_pasted_text(pasted_text, url)
        competitors = fetch_competitor_data(listing.get("title", ""))
    else:
        if not url:
            return {"error": "No URL provided"}, 400
        if "etsy.com/listing" not in url:
            return {"error": "Please enter a valid Etsy listing URL"}, 400

        listing = fetch_etsy_listing(url, etsy_api_key, etsy_shared_secret)
        if "error" in listing and not listing.get("title"):
            return {"error": listing["error"]}, 400

        competitors = fetch_competitor_data(listing.get("title", ""))

    prompt = build_analysis_prompt(listing, competitors)

    def generate():
        yield f"data: {json.dumps({'type': 'listing', 'data': listing})}\n\n"
        yield f"data: {json.dumps({'type': 'competitors', 'count': len(competitors)})}\n\n"

        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield f"data: {json.dumps({'type': 'text', 'content': text})}\n\n"

        yield 'data: {"type": "done"}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
