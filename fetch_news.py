import os
import re
import json
import html
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
import feedparser
import cloudscraper
from dateutil import parser

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

OUTPUT_DIR = "public"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "news.json")
MAX_NEWS_ITEMS = 200

LOCAL_TZ = timezone.utc

EXCLUDED_CATEGORIES = {"Popular News"}

ENG_STOP_WORDS = {
    "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "but", "if", "from", "its", "it", "this", "that", "these", "those",
    "tech", "technology", "news", "today", "update", "mobile", "app"
}

RSS_FEEDS = [
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss"},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "Engadget", "url": "https://www.engadget.com/rss.xml"},
    {"name": "VentureBeat", "url": "https://venturebeat.com/feed/"},
    {"name": "9to5Mac", "url": "https://9to5mac.com/feed/"},
    {"name": "Android Central", "url": "https://www.androidcentral.com/rss.xml"},
    {"name": "Gizmodo", "url": "https://gizmodo.com/rss"},
    {"name": "Techmeme", "url": "https://www.techmeme.com/feed.xml"},
    {"name": "CNET", "url": "https://www.cnet.com/rss/news/"},
    {"name": "ZDNet", "url": "https://www.zdnet.com/news/rss.xml"},
    {"name": "TechRadar", "url": "https://www.techradar.com/rss"},
    {"name": "Digital Trends", "url": "https://www.digitaltrends.com/feed/"},
    {"name": "Mashable", "url": "https://mashable.com/feeds/rss/all"},
    {"name": "The Next Web", "url": "https://thenextweb.com/feed"},
    {"name": "NASA", "url": "https://www.nasa.gov/news-release/feed/"}
]

def extract_domain_name(url):
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.split(':')[0].lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.upper()
    except Exception:
        return ""

def get_resilient_session():
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    scraper.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
        "Cache-Control": "max-age=0",
    })
    return scraper

def parse_date(date_string):
    if not date_string:
        return None
    try:
        dt = parser.parse(str(date_string))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        else:
            dt = dt.astimezone(LOCAL_TZ)
        return dt.isoformat()
    except Exception:
        return None

def extract_entry_date(entry):
    for field in ['published', 'updated', 'created', 'pubDate', 'dc_date', 'date', 'post_date']:
        val = entry.get(field)
        if val:
            parsed = parse_date(val)
            if parsed:
                return parsed

    for parsed_field in ['published_parsed', 'updated_parsed', 'created_parsed']:
        tp = entry.get(parsed_field)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=timezone.utc).astimezone(LOCAL_TZ)
                return dt.isoformat()
            except Exception:
                pass
    return None

def clean_html(text):
    if not text:
        return ""
    clean = html.unescape(html.unescape(text))
    clean = re.sub(r'<[^>]+>', '', clean)
    clean = re.sub(r'\[\s*(?:…|\.\.\.).*', '', clean, flags=re.DOTALL)
    return clean.strip()

def extract_image_from_text(text, base_url):
    if not text:
        return None
    text = html.unescape(html.unescape(text))

    image_tag_match = re.search(r'<image[^>]*>\s*(https?://[^\s<"]+)\s*</image>', text, re.IGNORECASE)
    if image_tag_match:
        return urljoin(base_url, image_tag_match.group(1).strip())

    img_tags = re.findall(r'<img\s+[^>]+>', text, re.IGNORECASE | re.DOTALL)
    for img_tag in img_tags:
        src = None
        for attr in ['data-src', 'data-lazy-src', 'data-original', 'data-full-url', 'src']:
            match = re.search(rf'{attr}=["\']?([^\s"\'>]+)["\']?', img_tag, re.IGNORECASE)
            if match and match.group(1).strip():
                src = match.group(1).strip()
                break

        if not src or any(p in src.lower() for p in ["data:image", "1x1", "blank.gif", "placeholder"]):
            srcset_match = re.search(r'srcset=["\']?([^\s"\'>]+)', img_tag, re.IGNORECASE)
            if srcset_match:
                first_part = srcset_match.group(1).split(',')[0].strip()
                if first_part:
                    src = first_part.split(' ')[0].strip()

        if src and not src.startswith("data:") and not any(p in src.lower() for p in ["1x1", "blank.gif", "placeholder"]):
            clean_url = html.unescape(src)
            return urljoin(base_url, clean_url)

    return None

def parse_val_to_url(val, base_url):
    if not val:
        return None
    if isinstance(val, str):
        v = val.strip()
        if v.startswith("http") and not v.startswith("data:"):
            return urljoin(base_url, html.unescape(v))
    elif isinstance(val, dict):
        for k in ['href', 'url', 'src', 'link', 'value', 'content']:
            res = parse_val_to_url(val.get(k), base_url)
            if res:
                return res
    elif isinstance(val, list):
        for item in val:
            res = parse_val_to_url(item, base_url)
            if res:
                return res
    return None

def extract_image_from_entry(entry, base_url):
    if 'media_content' in entry and entry.media_content:
        for media in entry.media_content:
            res = parse_val_to_url(media, base_url)
            if res:
                return res

    if 'media_thumbnail' in entry and entry.media_thumbnail:
        for thumb in entry.media_thumbnail:
            res = parse_val_to_url(thumb, base_url)
            if res:
                return res

    if 'enclosures' in entry and entry.enclosures:
        for enc in entry.enclosures:
            if isinstance(enc, dict):
                href = enc.get('href', '').strip()
                enc_type = enc.get('type', '').lower()
                if href and (enc_type.startswith('image/') or re.search(r'\.(jpg|jpeg|png|webp|gif|svg)(\?.*)?$', href, re.I)):
                    res = parse_val_to_url(href, base_url)
                    if res:
                        return res

    for key in entry.keys():
        if any(k in key.lower() for k in ['image', 'photo', 'thumb', 'media', 'cover', 'picture']):
            res = parse_val_to_url(entry[key], base_url)
            if res:
                return res

    if 'content_encoded' in entry and entry.content_encoded:
        img = extract_image_from_text(entry.content_encoded, base_url)
        if img:
            return img

    for field in ['content', 'summary_detail', 'description_detail']:
        if field in entry:
            val = entry[field]
            if isinstance(val, list):
                for v in val:
                    if isinstance(v, dict) and v.get('value'):
                        img = extract_image_from_text(v.get('value'), base_url)
                        if img:
                            return img
            elif isinstance(val, dict) and val.get('value'):
                img = extract_image_from_text(val.get('value'), base_url)
                if img:
                    return img

    for field in ['summary', 'description', 'story']:
        if field in entry and entry[field]:
            if isinstance(entry[field], str):
                img = extract_image_from_text(entry[field], base_url)
                if img:
                    return img

    base_domain = extract_domain_name(base_url)
    for k, v in entry.items():
        v_str = str(v)
        img_matches = re.findall(r'https?://[^\s\'"<>]+\.(?:jpg|jpeg|png|webp|gif|svg)(?:\?[^\s\'"<>]*)?', v_str, re.IGNORECASE)
        for img_match in img_matches:
            clean_img = html.unescape(img_match.strip())
            if not any(p in clean_img.lower() for p in ["1x1", "blank.gif", "placeholder", "data:image"]):
                if base_domain and extract_domain_name(clean_img) == base_domain:
                    return urljoin(base_url, clean_img)

    for k, v in entry.items():
        v_str = str(v)
        img_matches = re.findall(r'https?://[^\s\'"<>]+\.(?:jpg|jpeg|png|webp|gif|svg)(?:\?[^\s\'"<>]*)?', v_str, re.IGNORECASE)
        for img_match in img_matches:
            clean_img = html.unescape(img_match.strip())
            if not any(p in clean_img.lower() for p in ["1x1", "blank.gif", "placeholder", "data:image"]):
                return urljoin(base_url, clean_img)

    return None

def safe_parse_dt(iso_str):
    if not iso_str:
        return datetime.min.replace(tzinfo=LOCAL_TZ)
    try:
        dt = parser.parse(iso_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return datetime.min.replace(tzinfo=LOCAL_TZ)

def check_keywords(full_text, keywords):
    if not full_text or not keywords:
        return False

    full_text_lower = full_text.lower()
    words = set(re.findall(r'\w+', full_text_lower))

    for k in keywords:
        k_clean = k.strip().lower()
        if not k_clean:
            continue
        if ' ' in k_clean or '-' in k_clean:
            if k_clean in full_text_lower:
                return True
        else:
            if k_clean in words:
                return True

    return False

def get_explicit_categories(entry, link, source_name):
    categories = set()
    feed_tags = []

    if 'tags' in entry:
        for t in entry.tags:
            if isinstance(t, dict) and 'term' in t:
                feed_tags.append(str(t['term']).lower().strip())
            elif hasattr(t, 'term'):
                feed_tags.append(str(t.term).lower().strip())
    if 'category' in entry and entry.category:
        feed_tags.append(str(entry.category).lower().strip())

    url_path = urlparse(link).path.lower()

    mappings = [
        ("AI & Machine Learning", [
            "/ai/", "/artificial-intelligence/", "/machine-learning/",
            "ai", "artificial intelligence", "chatgpt", "openai", "llm", "machine learning", "claude", "gemini", "ai apps", "google gemini", "anthropic", "hugging face", "instinct", "perceptron"
        ]),
        ("Cybersecurity & Privacy", [
            "/security/", "/cybersecurity/", "/privacy/",
            "security", "cybersecurity", "privacy", "hack", "hacker", "malware", "vulnerability", "ransomware", "botnets", "encryption", "cisa", "cyberattacks"
        ]),
        ("Gadgets & Hardware", [
            "/gadgets/", "/hardware/", "/reviews/", "/smartphones/", "/laptops/",
            "gadget", "gadgets", "hardware", "smartphone", "laptop", "iphone", "android", "macbook", "samsung", "apple", "nvidia", "ring cameras", "medical device"
        ]),
        ("Software & Apps", [
            "/software/", "/apps/", "/mobile-apps/",
            "software", "app", "apps", "windows", "ios", "linux", "developer", "programming"
        ]),
        ("Mobile & Telecom", [
            "/mobile/", "/telecom/", "/5g/",
            "mobile", "telecom", "5g", "network"
        ]),
        ("Business & Startups", [
            "/startups/", "/business/", "/tech-business/", "/venture/",
            "startup", "startups", "business", "big tech", "funding", "vc", "silicon valley", "venture", "tc", "enterprise", "nscale", "spacex", "lovable", "legora", "neko health", "european market", "nordic market"
        ]),
        ("Gaming", [
            "/gaming/", "/games/", "/esports/",
            "gaming", "games", "esports", "playstation", "xbox", "nintendo", "pc gaming"
        ]),
        ("Science & Environment", [
            "/science/", "/space/", "/robotics/", "/climate/", "/environment/",
            "science", "space", "robotics", "innovation", "biotech", "biotech & health", "climate", "environment", "water"
        ]),
        ("Social & Media", [
            "/social/", "/media/",
            "social", "social media", "facebook", "instagram", "meta", "bluesky", "flipboard", "open web", "media & entertainment", "podcasts", "particle"
        ]),
        ("Government & Policy", [
            "/policy/", "/politics/", "/government/",
            "government & policy", "policy", "politics", "us government", "china", "iran", "social media child safety", "critical infrastructure"
        ]),
        ("Crypto & Web3", [
            "/crypto/", "/blockchain/", "/web3/",
            "crypto", "bitcoin", "ethereum", "blockchain", "web3", "nft"
        ])
    ]

    for cat_name, patterns in mappings:
        for pat in patterns:
            if pat.startswith("/") and pat.endswith("/"):
                if pat in url_path:
                    categories.add(cat_name)
                    break

    for cat_name, patterns in mappings:
        for tag in feed_tags:
            clean_tag = tag.strip().lower()
            for pat in patterns:
                if not pat.startswith("/") and clean_tag == pat:
                    categories.add(cat_name)
                    break

    return categories

def determine_categories(entry, title, link, clean_desc, source_name, pub_date=None):
    categories = set()
    explicit_cats = get_explicit_categories(entry, link, source_name)

    if explicit_cats:
        categories.update(explicit_cats)
    else:
        categories.add("Tech News")

    is_recent = False
    if pub_date:
        parsed_dt = safe_parse_dt(pub_date)
        now_dt = datetime.now(LOCAL_TZ)
        if parsed_dt != datetime.min.replace(tzinfo=LOCAL_TZ):
            diff = now_dt - parsed_dt
            if timedelta(hours=-1) <= diff <= timedelta(hours=4):
                is_recent = True

    breaking_kw = [
        "breaking", "urgent", "update", "live", "alert", "flash", "latest", "leaked", "launched", "TC", "Tech", "Science", "Space", "NASA", "AI"
    ]

    link_lower = link.lower()
    title_lower = title.lower()
    desc_lower = clean_desc.lower()

    tag_terms = []
    if hasattr(entry, 'tags') and entry.tags:
        for t in entry.tags:
            if isinstance(t, dict) and 'term' in t:
                tag_terms.append(str(t['term']).lower())
            elif hasattr(t, 'term'):
                tag_terms.append(str(t.term).lower())

    feed_cat_str = " ".join(tag_terms)

    has_breaking_kw = check_keywords(title_lower, breaking_kw) or \
                      check_keywords(desc_lower, breaking_kw) or \
                      check_keywords(feed_cat_str, breaking_kw) or \
                      any(slug in link_lower for slug in ['/breaking/', '/breaking-news/'])

    if is_recent and has_breaking_kw:
        categories.add("Breaking News")

    return sorted(list(categories))

def detect_multi_source_breaking_news(items):
    now_dt = datetime.now(LOCAL_TZ)
    recent_items = []

    for item in items:
        pdate = item.get("pub_date")
        if pdate:
            dt = safe_parse_dt(pdate)
            if dt != datetime.min.replace(tzinfo=LOCAL_TZ):
                diff = now_dt - dt
                if timedelta(hours=-1) <= diff <= timedelta(hours=6):
                    recent_items.append(item)

    def get_tokens(text):
        tokens = set(re.findall(r'\b\w{3,}\b', text.lower()))
        return tokens - ENG_STOP_WORDS

    source_matches = {id(item): {item.get("source_name")} for item in recent_items}

    for i in range(len(recent_items)):
        item_a = recent_items[i]
        tokens_a = get_tokens(item_a.get("title", ""))
        if not tokens_a:
            continue

        for j in range(i + 1, len(recent_items)):
            item_b = recent_items[j]
            if item_a.get("source_name") == item_b.get("source_name"):
                continue

            tokens_b = get_tokens(item_b.get("title", ""))
            if not tokens_b:
                continue

            intersection = tokens_a.intersection(tokens_b)
            union = tokens_a.union(tokens_b)

            if union:
                jaccard_score = len(intersection) / len(union)
                if jaccard_score >= 0.30 or len(intersection) >= 3:
                    source_matches[id(item_a)].add(item_b.get("source_name"))
                    source_matches[id(item_b)].add(item_a.get("source_name"))

    for item in recent_items:
        distinct_sources = source_matches[id(item)]
        if len(distinct_sources) >= 2:
            if "categories" not in item or not isinstance(item["categories"], list):
                item["categories"] = []
            if "Breaking News" not in item["categories"]:
                item["categories"].append("Breaking News")
                item["categories"].sort()

def titles_are_duplicate(title1, title2):
    if not title1 or not title2:
        return False

    norm1 = re.sub(r'[^\w]', '', title1.lower())
    norm2 = re.sub(r'[^\w]', '', title2.lower())
    if norm1 == norm2:
        return True

    tokens1 = set(re.findall(r'\b\w{2,}\b', title1.lower())) - ENG_STOP_WORDS
    tokens2 = set(re.findall(r'\b\w{2,}\b', title2.lower())) - ENG_STOP_WORDS

    if not tokens1 or not tokens2:
        return False

    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)

    jaccard = len(intersection) / len(union)
    return jaccard >= 0.70

def deduplicate_cross_source(items):
    unique_items = []

    for item in items:
        is_dup = False
        dt_item = safe_parse_dt(item.get("pub_date"))

        for u_item in unique_items:
            dt_u = safe_parse_dt(u_item.get("pub_date"))

            if dt_item != datetime.min.replace(tzinfo=LOCAL_TZ) and dt_u != datetime.min.replace(tzinfo=LOCAL_TZ):
                if abs((dt_item - dt_u).total_seconds()) > 172800:
                    continue

            if titles_are_duplicate(item.get("title", ""), u_item.get("title", "")):
                is_dup = True

                item_cats = item.get("categories") or []
                u_item_cats = u_item.get("categories") or []

                if "Breaking News" in item_cats and "Breaking News" not in u_item_cats:
                    u_item_cats.append("Breaking News")
                    u_item_cats.sort()
                    u_item["categories"] = u_item_cats

                if not u_item.get("image_url") and item.get("image_url"):
                    u_domain = extract_domain_name(u_item.get("link"))
                    item_img_domain = extract_domain_name(item.get("image_url"))
                    if u_domain and item_img_domain and u_domain == item_img_domain:
                        u_item["image_url"] = item["image_url"]

                break

        if not is_dup:
            unique_items.append(item)

    return unique_items

def fetch_and_store_news():
    session = get_resilient_session()
    fetched_items = []

    for feed in RSS_FEEDS:
        logging.info(f"Fetching feed: {feed['name']}")
        try:
            response = session.get(feed['url'], timeout=12)
            if response.status_code != 200:
                logging.warning(f"Skipped {feed['name']} (HTTP Status: {response.status_code})")
                continue

            raw_xml = response.text
            item_images = {}
            for item_match in re.finditer(r'<item[^>]*>(.*?)</item>', raw_xml, re.IGNORECASE | re.DOTALL):
                item_xml = item_match.group(1)
                link_match = re.search(r'<link[^>]*>(.*?)</link>', item_xml, re.IGNORECASE | re.DOTALL)
                img_match = re.search(r'<image[^>]*>(.*?)</image>', item_xml, re.IGNORECASE | re.DOTALL)

                if link_match and img_match:
                    l_val = link_match.group(1).strip()
                    i_val = img_match.group(1).strip()
                    l_val = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', l_val, flags=re.IGNORECASE).strip()
                    i_val = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', i_val, flags=re.IGNORECASE).strip()
                    item_images[l_val] = i_val

            parsed_feed = feedparser.parse(response.content)

            for entry in parsed_feed.entries[:15]:
                link = entry.get('link')
                title = entry.get('title')

                if not link or not title:
                    continue

                link = link.strip()
                title = title.strip()
                raw_description = entry.get('summary', entry.get('description', ''))
                clean_desc = clean_html(raw_description)

                if len(clean_desc.split()) < 10:
                    continue

                pub_date = extract_entry_date(entry)
                image_url = extract_image_from_entry(entry, link)

                if not image_url and link in item_images:
                    image_url = parse_val_to_url(item_images[link], link)

                source_domain = extract_domain_name(link) or extract_domain_name(feed['url'])

                categories = determine_categories(
                    entry,
                    title,
                    link,
                    clean_desc,
                    source_domain,
                    pub_date
                )

                fetched_items.append({
                    "link": link,
                    "title": title,
                    "description": clean_desc,
                    "categories": categories,
                    "pub_date": pub_date,
                    "image_url": image_url,
                    "source_name": source_domain
                })

        except Exception as e:
            logging.error(f"Failed to fetch {feed['name']}: {e}")

    existing_items = []
    existing_map = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    existing_items = data
                    for ex in existing_items:
                        if isinstance(ex, dict) and ex.get("link"):
                            existing_map[ex["link"]] = ex
        except Exception as err:
            logging.warning(f"Could not read existing news file: {err}")

    seen_links = set()
    combined_items = []

    for item in fetched_items:
        link = item.get("link")
        desc = item.get("description", "")

        if len(desc.split()) < 10 or not link or link in seen_links:
            continue

        seen_links.add(link)

        if link in existing_map:
            ex_date = existing_map[link].get("pub_date")
            if ex_date and not item.get("pub_date"):
                item["pub_date"] = ex_date

        combined_items.append(item)

    for ex in existing_items:
        if not isinstance(ex, dict):
            continue
        link = ex.get("link")
        desc = ex.get("description", "")
        if link and link not in seen_links and len(desc.split()) >= 10:
            seen_links.add(link)

            ex["source_name"] = extract_domain_name(link)

            if "categories" not in ex or not isinstance(ex["categories"], list):
                if "category" in ex:
                    cat_val = ex.pop("category")
                    ex["categories"] = cat_val if isinstance(cat_val, list) else ([cat_val] if cat_val else [])
                else:
                    ex["categories"] = []

            ex["categories"] = [c for c in ex["categories"] if c and c not in EXCLUDED_CATEGORIES]

            combined_items.append(ex)

    detect_multi_source_breaking_news(combined_items)

    combined_items.sort(key=lambda x: safe_parse_dt(x.get("pub_date")), reverse=True)

    deduplicated_items = deduplicate_cross_source(combined_items)

    final_news = deduplicated_items[:MAX_NEWS_ITEMS]

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    temp_file = OUTPUT_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(final_news, f, ensure_ascii=False, indent=2)

    os.replace(temp_file, OUTPUT_FILE)

    logging.info(f"Successfully generated {OUTPUT_FILE} with {len(final_news)} news items.")

if __name__ == "__main__":
    fetch_and_store_news()
