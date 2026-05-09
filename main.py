import os
import re
import time
import json
import datetime
import requests

from github import Github
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG
# =========================================================

TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"

FILE_PATH = "bongda.json"

WAITING_VIDEO_URL = "https://example.com/waiting.mp4"

LIMIT_MATCHES = 15

VN_TZ = datetime.timezone(
    datetime.timedelta(hours=7)
)

GITHUB_TOKEN = os.getenv("GH_TOKEN")

REPO_NAME = os.getenv(
    "GH_REPO",
    "Eternal161/dausoco"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://bunchatv4.net/",
    "Origin": "https://bunchatv4.net"
}

LOGO_CACHE = {}

# =========================================================
# LOGO
# =========================================================

def normalize_team_name(name):

    name = re.sub(r"\bFc\b$", "FC", name)

    return name.strip()


def get_team_logo(team_name):

    if not team_name:
        return ""

    team_name = normalize_team_name(team_name)

    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]

    try:

        slug = (
            team_name
            .lower()
            .replace(" ", "-")
        )

        url = (
            "https://football-logos.cc/"
            f"{slug}/"
        )

        r = requests.get(
            url,
            headers=_HEADERS,
            timeout=10
        )

        match = re.search(
            r'https://football-logos.cc/logos/[^"]+\.png',
            r.text
        )

        if match:

            logo = match.group(0)

            LOGO_CACHE[team_name] = logo

            print(f"🏷 LOGO: {team_name}")

            return logo

    except:
        pass

    return (
        "https://ui-avatars.com/api/"
        f"?name={team_name[:2]}"
    )

# =========================================================
# PARSE MATCH
# =========================================================

def parse_url_to_info(url):

    try:

        match = re.search(
            r"/truc-tiep/([^/?#]+)",
            url
        )

        if not match:
            return (
                "Unknown",
                "Unknown",
                "Unknown"
            )

        slug = match.group(1)

        time_match = re.search(
            r"(\d{4}-\d{2}-\d{4})$",
            slug
        )

        if time_match:

            t = time_match.group(1)

            thoi_gian = (
                f"{t[0:2]}:{t[2:4]} "
                f"{t[5:7]}/{t[8:10]}/{t[11:15]}"
            )

            teams_slug = slug[
                :slug.rfind("-" + t)
            ]

        else:

            thoi_gian = "Unknown"

            teams_slug = slug

        parts = teams_slug.split(
            "-vs-",
            1
        )

        doi_nha = (
            parts[0]
            .replace("-", " ")
            .title()
        )

        doi_khach = (
            parts[1]
            .replace("-", " ")
            .title()
            if len(parts) > 1
            else "Unknown"
        )

        return (
            doi_nha,
            doi_khach,
            thoi_gian
        )

    except:

        return (
            "Unknown",
            "Unknown",
            "Unknown"
        )

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

        return (
            r.status_code == 200
            and "#EXTM3U" in r.text
        )

    except:

        return False

# =========================================================
# STREAM SCORE
# =========================================================

def stream_score(url):

    lower = url.lower()

    score = 0

    if "taoxanh" in lower:
        score += 1000

    if "cdn-hls" in lower:
        score += 900

    if "index.m3u8" in lower:
        score += 700

    if "master.m3u8" in lower:
        score += 600

    if "playlist.m3u8" in lower:
        score += 300

    if "rapidlive" in lower:
        score += 200

    if ".m3u8" in lower:
        score += 100

    return score

# =========================================================
# CAPTURE STREAM
# =========================================================

def capture_stream(context, match_url):

    page = context.new_page()

    Stealth().apply_stealth_sync(page)

    streams = set()

    # =====================================================
    # CONSOLE
    # =====================================================

    page.on(
        "console",
        lambda msg: print(
            "BROWSER:",
            msg.text
        )
    )

    # =====================================================
    # RESPONSE HANDLER
    # =====================================================

    def handle_response(res):

        try:

            url = res.url

            ct = res.headers.get(
                "content-type",
                ""
            ).lower()

            # =================================================
            # DIRECT M3U8
            # =================================================

            if (
                ".m3u8" in url.lower()
                or "mpegurl" in ct
            ):

                if "ads" not in url.lower():

                    streams.add(url)

                    print("\n🎯 DIRECT M3U8")
                    print(url)

            # =================================================
            # PARSE BODY
            # =================================================

            try:

                body = res.text()

                found = re.findall(
                    r'https?:\/\/[^\s"\']+\.m3u8[^\s"\']*',
                    body
                )

                for m3u8 in found:

                    if "ads" in m3u8.lower():
                        continue

                    streams.add(m3u8)

                    print("\n🔥 BODY M3U8")
                    print(m3u8)

            except:
                pass

        except Exception as e:

            print("LISTENER ERROR:", e)

    page.on(
        "response",
        handle_response
    )

    # =====================================================
    # REQUEST HANDLER
    # =====================================================

    def handle_request(req):

        try:

            url = req.url

            if ".m3u8" in url.lower():

                streams.add(url)

                print("\n⚡ REQUEST M3U8")
                print(url)

        except:
            pass

    page.on(
        "request",
        handle_request
    )

    # =====================================================
    # LOAD PAGE
    # =====================================================

    try:

        page.goto(
            match_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        # =================================================
        # REMOVE OVERLAY
        # =================================================

        try:

            page.evaluate("""
            document.querySelectorAll('*')
            .forEach(el => {

                const s =
                    window.getComputedStyle(el);

                const z =
                    parseInt(s.zIndex);

                if (
                    s.position === 'fixed'
                    && z > 999
                ) {
                    el.remove();
                }

            });
            """)

        except:
            pass

        # =================================================
        # CLICK CENTER
        # =================================================

        try:

            vp = page.viewport_size

            cx = vp["width"] // 2
            cy = vp["height"] // 2

            page.mouse.click(cx, cy)

            page.wait_for_timeout(1500)

            page.mouse.click(cx, cy)

            page.wait_for_timeout(1500)

            page.mouse.click(cx, cy)

        except:
            pass

        # =================================================
        # PLAY VIDEO
        # =================================================

        for frame in page.frames:

            try:

                frame.evaluate("""
                document
                .querySelectorAll('video')
                .forEach(v => {

                    v.muted = true;

                    const p = v.play();

                    if (p !== undefined) {
                        p.catch(()=>{});
                    }

                });
                """)

            except:
                pass

        # =================================================
        # WAIT STREAM
        # =================================================

        deadline = time.time() + 25

        while time.time() < deadline:

            if streams:
                print(
                    f"\n📡 TOTAL STREAMS: {len(streams)}"
                )

            time.sleep(1)

    except PWTimeout:

        print("⚠️ TIMEOUT")

    except Exception as e:

        print("❌ STREAM ERROR:", e)

    finally:

        page.close()

    # =====================================================
    # SELECT BEST STREAM
    # =====================================================

    if streams:

        valid_streams = []

        for s in streams:

            score = stream_score(s)

            valid_streams.append(
                (score, s)
            )

        valid_streams.sort(
            reverse=True
        )

        print("\n====================")
        print("📋 STREAM RANKING")
        print("====================")

        for score, url in valid_streams[:10]:

            print(
                f"[{score}] {url}"
            )

        # =============================================
        # VALIDATE BEST
        # =============================================

        for score, url in valid_streams:

            print("\n🔍 TEST:")
            print(url)

            if validate_m3u8(url):

                print("✅ VALID STREAM")

                return url

            else:

                print("❌ DEAD STREAM")

    return None

# =========================================================
# JSON
# =========================================================

def create_json(matches):

    data = {

        "playlist_name": "Sang TV",

        "updated": (
            datetime.datetime.now(VN_TZ)
            .strftime("%H:%M %d/%m/%Y")
        ),

        "total_matches": len(matches),

        "matches": matches
    }

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

        return

    g = Github(GITHUB_TOKEN)

    repo = g.get_repo(REPO_NAME)

    msg = (
        "⚽ Update "
        + datetime.datetime.now(VN_TZ)
        .strftime("%H:%M %d/%m/%Y")
    )

    try:

        existing = repo.get_contents(FILE_PATH)

        repo.update_file(
            existing.path,
            msg,
            content,
            existing.sha
        )

        print("✅ Updated GitHub")

    except:

        repo.create_file(
            FILE_PATH,
            msg,
            content
        )

        print("✅ Created GitHub file")

# =========================================================
# MAIN
# =========================================================

def scrape_and_push():

    matches = []

    with sync_playwright() as p:

        browser = p.chromium.launch(

            channel="chrome",

            headless=True,

            args=[

                "--disable-blink-features=AutomationControlled",

                "--no-sandbox",

                "--disable-setuid-sandbox",

                "--disable-dev-shm-usage",

                "--disable-gpu",

                "--disable-web-security",

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

        page = context.new_page()

        Stealth().apply_stealth_sync(page)

        print("📋 LOAD MATCHES")

        page.goto(
            TARGET_SITE,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        for _ in range(5):

            page.mouse.wheel(0, 3000)

            page.wait_for_timeout(1000)

        links = []

        seen = set()

        for el in page.locator(
            "a[href*='/truc-tiep/']"
        ).all():

            href = el.get_attribute("href")

            if not href:
                continue

            if "-vs-" not in href:
                continue

            if href in seen:
                continue

            seen.add(href)

            if not href.startswith("http"):

                href = (
                    "https://bunchatv4.net"
                    + href
                )

            links.append(href)

        if LIMIT_MATCHES:

            links = links[:LIMIT_MATCHES]

        print(f"✅ FOUND {len(links)} MATCHES")

        # =================================================
        # PROCESS MATCHES
        # =================================================

        for idx, href in enumerate(links):

            doi_nha, doi_khach, thoi_gian = (
                parse_url_to_info(href)
            )

            print(
                f"\n[{idx+1}/{len(links)}]"
            )

            print(
                f"{doi_nha} vs {doi_khach}"
            )

            stream = capture_stream(
                context,
                href
            )

            match = {

                "id": str(idx + 1),

                "title":
                    f"{doi_nha} vs {doi_khach}",

                "doi_nha": doi_nha,

                "doi_khach": doi_khach,

                "thoi_gian": thoi_gian,

                "logo_nha":
                    get_team_logo(doi_nha),

                "logo_khach":
                    get_team_logo(doi_khach),

                "stream_url":
                    stream
                    if stream
                    else WAITING_VIDEO_URL,

                "link_xem": href
            }

            matches.append(match)

        browser.close()

    content = create_json(matches)

    push_to_github(content)

    print("\n✅ DONE")

# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    scrape_and_push()
