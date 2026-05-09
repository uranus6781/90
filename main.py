import os
import re
import time
import json
import datetime
import requests

from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG
# =========================================================

CHANNELS = [
    {
        "id": "buncha",
        "name": "Bún Chả TV",
        "url": "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv",
        "base_url": "https://bunchatv4.net"
    },
    {
        "id": "hoiquan",
        "name": "Hội Quán TV",
        "url": "https://sv2.hoiquan3.live/lich-thi-dau/bong-da",
        "base_url": "https://sv2.hoiquan3.live"
    }
]

FILE_PATH = "bongda.json"

WAITING_VIDEO_URL = "https://example.com/waiting.mp4"

LIMIT_MATCHES = 5

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGO_CACHE = {}

# =========================================================
# LOGO
# =========================================================

def slugify_team(name):

    name = name.lower()

    replacements = {
        "fc": "",
        "football club": "",
        "club": "",
        ".": "",
        "'": "",
    }

    for k, v in replacements.items():
        name = name.replace(k, v)

    name = re.sub(r"[^a-z0-9\s-]", "", name)

    name = re.sub(r"\s+", "-", name)

    name = re.sub(r"-+", "-", name)

    return name.strip("-")


def get_team_logo(team_name):

    if not team_name or team_name == "Unknown":
        return ""

    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]

    slug = slugify_team(team_name)

    urls = [
        f"https://football-logos.cc/{slug}/",
        f"https://football-logos.cc/{slug}-fc/",
    ]

    for url in urls:

        try:

            r = requests.get(
                url,
                headers=_HEADERS,
                timeout=8
            )

            found = re.findall(
                r'https://football-logos.cc/logos/[^"]+\.png',
                r.text
            )

            if found:

                logo = found[0]

                LOGO_CACHE[team_name] = logo

                print(f"🏷 LOGO: {team_name}")

                return logo

        except:
            pass

    fallback = (
        "https://ui-avatars.com/api/"
        f"?name={requests.utils.quote(team_name[:2])}"
        "&size=200"
        "&background=1565C0"
        "&color=ffffff"
    )

    return fallback

# =========================================================
# PARSE MATCH
# =========================================================

def parse_url_to_info(url):

    try:

        parts = url.rstrip("/").split("/")

        slug = ""

        for p in reversed(parts):

            if "-vs-" in p:

                slug = p.split("?")[0].split("#")[0]

                break

        if not slug:
            return "Unknown", "Unknown", "Unknown"

        slug = re.sub(r"-\d{6,}$", "", slug)

        time_match = re.search(
            r"-(\d{4}-\d{2}-\d{2}-\d{4})$",
            slug
        )

        if time_match:

            t = time_match.group(1)

            thoi_gian = (
                f"{t[0:2]}:{t[2:4]} "
                f"{t[5:7]}/{t[8:10]}/{t[11:15]}"
            )

            teams_slug = slug[:slug.rfind("-" + t)]

        else:

            thoi_gian = "Unknown"

            teams_slug = slug

        teams = teams_slug.split("-vs-", 1)

        doi_nha = (
            teams[0]
            .replace("-", " ")
            .title()
            .strip()
        )

        doi_khach = (
            teams[1]
            .replace("-", " ")
            .title()
            .strip()
            if len(teams) > 1 else "Unknown"
        )

        return doi_nha, doi_khach, thoi_gian

    except:

        return "Unknown", "Unknown", "Unknown"

# =========================================================
# VALIDATE M3U8
# =========================================================

def validate_m3u8(url):

    try:

        r = requests.get(
            url,
            headers=_HEADERS,
            timeout=10
        )

        if r.status_code != 200:
            return False

        text = r.text

        if "#EXTM3U" not in text:
            return False

        if (
            "#EXTINF" not in text
            and "#EXT-X-STREAM-INF" not in text
        ):
            return False

        if "404" in text.lower():
            return False

        return True

    except:

        return False

# =========================================================
# CAPTURE STREAM
# =========================================================

def capture_stream(context, match_url):

    page = context.new_page()

    page.set_default_timeout(30000)

    Stealth().apply_stealth_sync(page)

    streams = set()

    def handle_console(msg):

        try:

            txt = msg.text

            if ".m3u8" in txt:

                found = re.findall(
                    r'https?://[^\s"\']+\.m3u8[^\s"\']*',
                    txt
                )

                for f in found:

                    streams.add(f)

                    print(f"🎯 CONSOLE: {f[:80]}")

        except:
            pass

    def handle_response(res):

        try:

            url = res.url

            ct = res.headers.get(
                "content-type",
                ""
            ).lower()

            lower = url.lower()

            if (
                ".m3u8" in lower
                or "mpegurl" in ct
            ):

                if any(
                    bad in lower
                    for bad in [
                        "/ad/",
                        "/ads/",
                        "/vast/",
                        "banner",
                        "preroll",
                    ]
                ):
                    return

                streams.add(url)

                print(f"🎯 FOUND: {url[:80]}")

        except:
            pass

    page.on("console", handle_console)

    page.on("response", handle_response)

    page.on(
        "websocket",
        lambda ws: print("WS:", ws.url)
    )

    try:

        page.add_init_script("""
        (() => {

            const origFetch = window.fetch;

            window.fetch = async (...args) => {

                if (
                    typeof args[0] === 'string'
                    &&
                    (
                        args[0].includes('.m3u8')
                        ||
                        args[0].includes('.flv')
                    )
                ) {
                    console.log(args[0]);
                }

                return origFetch(...args);
            };

            const origOpen = XMLHttpRequest.prototype.open;

            XMLHttpRequest.prototype.open = function(method, url) {

                if (
                    url.includes('.m3u8')
                    ||
                    url.includes('.flv')
                ) {
                    console.log(url);
                }

                return origOpen.apply(this, arguments);
            };

        })();
        """)

        page.goto(
            match_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        try:
            page.wait_for_load_state(
                "networkidle",
                timeout=10000
            )
        except:
            pass

        page.wait_for_timeout(4000)

        # remove overlays

        try:

            page.evaluate("""
            document.querySelectorAll('*').forEach(el => {

                const s = window.getComputedStyle(el);

                if (
                    s.position === 'fixed'
                    &&
                    parseInt(s.zIndex) > 999
                ) {
                    el.remove();
                }

            });
            """)

        except:
            pass

        # click player

        try:

            vp = page.viewport_size

            cx = vp["width"] // 2

            cy = vp["height"] // 2

            page.mouse.click(cx, cy)

            page.wait_for_timeout(1500)

            page.mouse.click(cx, cy)

        except:
            pass

        # autoplay all videos

        for frame in page.frames:

            try:

                frame.evaluate("""
                document.querySelectorAll('video').forEach(v => {

                    v.muted = true;

                    const p = v.play();

                    if (p !== undefined) {
                        p.catch(()=>{});
                    }

                });
                """)

            except:
                pass

        deadline = time.time() + 40

        while time.time() < deadline:

            if streams:
                break

            time.sleep(1)

    except PWTimeout:

        print("⚠️ TIMEOUT")

    except Exception as e:

        print("❌ ERROR:", e)

    finally:

        try:
            page.remove_listener("response", handle_response)
        except:
            pass

        page.close()

    # =====================================================
    # PRIORITY STREAM
    # =====================================================

    if streams:

        priority = []

        for s in streams:

            score = 0

            lower = s.lower()

            if "taoxanh" in lower:
                score += 10000

            if "cdn-hls" in lower:
                score += 5000

            if (
                "expire=" in lower
                or "sign=" in lower
                or "token=" in lower
            ):
                score += 1000

            if "index.m3u8" in lower:
                score += 500

            if "playlist.m3u8" in lower:
                score += 300

            priority.append((score, s))

        priority.sort(
            reverse=True,
            key=lambda x: x[0]
        )

        best = priority[0][1]

        print(f"\n✅ FINAL STREAM\n{best}")

        if validate_m3u8(best):
            print("✅ VALID")

        return best

    return None

# =========================================================
# JSON
# =========================================================

def create_json(all_channel_data):

    total_live = 0

    total_streams = 0

    for matches in all_channel_data.values():

        total_live += sum(
            1 for m in matches
            if m.get("is_live")
        )

        total_streams += sum(
            1 for m in matches
            if (
                m.get("stream_url")
                and m["stream_url"] != WAITING_VIDEO_URL
            )
        )

    data = {
        "playlist_name": "Sáng TV",
        "last_updated": datetime.datetime.now(
            VN_TZ
        ).strftime("%H:%M %d/%m/%Y"),
        "total_live": total_live,
        "total_streams": total_streams,
    }

    data.update(all_channel_data)

    return json.dumps(
        data,
        indent=2,
        ensure_ascii=False
    )

# =========================================================
# PUSH GITHUB
# =========================================================

def push_to_github(content):

    if not GITHUB_TOKEN:

        with open(
            FILE_PATH,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(content)

        print("💾 SAVED LOCAL")

        return

    g = Github(GITHUB_TOKEN)

    repo = g.get_repo(REPO_NAME)

    msg = (
        "⚽ Auto Update "
        +
        datetime.datetime.now(VN_TZ).strftime(
            "%H:%M %d/%m/%Y"
        )
    )

    try:

        existing = repo.get_contents(FILE_PATH)

        repo.update_file(
            existing.path,
            msg,
            content,
            existing.sha
        )

        print("✅ UPDATED GITHUB")

    except:

        repo.create_file(
            FILE_PATH,
            msg,
            content
        )

        print("✅ CREATED GITHUB")

# =========================================================
# MAIN
# =========================================================

def scrape_and_push():

    all_channel_data = {
        "buncha": [],
        "hoiquan": []
    }

    print("=" * 70)

    print(
        datetime.datetime.now(VN_TZ).strftime(
            "START %H:%M:%S %d/%m/%Y"
        )
    )

    print("=" * 70)

    with sync_playwright() as p:

        browser = p.chromium.launch(

            headless=True,

            args=[

                "--disable-blink-features=AutomationControlled",

                "--disable-features=IsolateOrigins,site-per-process",

                "--disable-site-isolation-trials",

                "--disable-web-security",

                "--no-sandbox",

                "--disable-setuid-sandbox",

                "--disable-dev-shm-usage",

                "--disable-gpu",

                "--autoplay-policy=no-user-gesture-required",
            ]
        )

        context = browser.new_context(

            viewport={
                "width": 1920,
                "height": 1080
            },

            user_agent=_HEADERS["User-Agent"],

            ignore_https_errors=True
        )

        # =================================================
        # LOAD MATCHES
        # =================================================

        for channel in CHANNELS:

            print(f"\n📺 {channel['name']}")

            page = context.new_page()

            Stealth().apply_stealth_sync(page)

            try:

                page.goto(
                    channel["url"],
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                page.wait_for_timeout(5000)

            except:
                pass

            for _ in range(4):

                page.mouse.wheel(0, 3000)

                page.wait_for_timeout(1000)

            links = []

            seen = set()

            for el in page.locator(
                "a[href*='-vs-']"
            ).all():

                href = el.get_attribute("href")

                if (
                    not href
                    or "-vs-" not in href
                    or href in seen
                ):
                    continue

                seen.add(href)

                if not href.startswith("http"):
                    href = channel["base_url"] + href

                links.append(href)

            if LIMIT_MATCHES:
                links = links[:LIMIT_MATCHES]

            print(f"✅ FOUND {len(links)}")

            for idx, href in enumerate(links):

                doi_nha, doi_khach, thoi_gian = (
                    parse_url_to_info(href)
                )

                is_live = False

                status = "Chưa đá ⏳"

                try:

                    match_time = (
                        datetime.datetime.strptime(
                            thoi_gian,
                            "%H:%M %d/%m/%Y"
                        )
                        .replace(tzinfo=VN_TZ)
                    )

                    diff_minutes = (
                        datetime.datetime.now(VN_TZ)
                        -
                        match_time
                    ).total_seconds() / 60

                    if -10 <= diff_minutes <= 120:

                        is_live = True

                        status = "Đang trực tiếp 🔴"

                    elif diff_minutes > 120:

                        status = "Đã kết thúc 🏁"

                except:
                    pass

                print(
                    f"[{idx+1}] "
                    f"{doi_nha} vs {doi_khach}"
                )

                match_info = {

                    "id": str(idx + 1),

                    "title": (
                        f"{doi_nha} vs {doi_khach}"
                    ),

                    "doi_nha": doi_nha,

                    "doi_khach": doi_khach,

                    "thoi_gian": thoi_gian,

                    "trang_thai": status,

                    "is_live": is_live,

                    "logo_nha": get_team_logo(doi_nha),

                    "logo_khach": get_team_logo(doi_khach),

                    "stream_url": WAITING_VIDEO_URL,

                    "link_xem": href,

                    "headers": {
                        "Referer": channel["base_url"] + "/",
                        "Origin": channel["base_url"]
                    }
                }

                all_channel_data[
                    channel["id"]
                ].append(match_info)

            page.close()

        # =================================================
        # CAPTURE STREAMS
        # =================================================

        print("\n🎥 CAPTURE STREAMS")

        for channel in CHANNELS:

            live_matches = [

                m for m in all_channel_data[
                    channel["id"]
                ]

                if m["is_live"]
            ]

            if not live_matches:
                continue

            print(
                f"\n► {channel['name']}"
            )

            for idx, match in enumerate(live_matches):

                print(
                    f"\n[{idx+1}/{len(live_matches)}] "
                    f"{match['title']}"
                )

                stream = None

                for _ in range(2):

                    stream = capture_stream(
                        context,
                        match["link_xem"]
                    )

                    if stream:
                        break

                    time.sleep(3)

                if stream:
                    match["stream_url"] = stream

        browser.close()

    content = create_json(all_channel_data)

    push_to_github(content)

    print("\n✅ DONE")

# =========================================================

if __name__ == "__main__":
    scrape_and_push()
