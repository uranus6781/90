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
TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")

FILE_PATH = "bongda.json"

WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"

LIMIT_MATCHES = 15

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

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
# TEAM NAME
# =========================================================
def normalize_team_name(raw: str) -> str:

    cleaned = re.sub(r"\bFc\b$", "FC", raw)
    cleaned = re.sub(r"\bFootball Club\b", "FC", cleaned)

    return cleaned.strip()


# =========================================================
# LOGO
# =========================================================
def _logo_fallback(team_name: str):

    initials = "".join(
        w[0].upper()
        for w in team_name.split()[:3]
        if w
    )

    return (
        "https://ui-avatars.com/api/?name="
        f"{requests.utils.quote(initials)}"
        "&size=200&background=1565C0&color=ffffff"
    )


def get_team_logo(team_name: str):

    if not team_name:
        return _logo_fallback("?")

    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]

    logo = _logo_fallback(team_name)

    LOGO_CACHE[team_name] = logo

    return logo


# =========================================================
# PARSE MATCH INFO
# =========================================================
def parse_url_to_info(url: str):

    try:

        match = re.search(
            r"/truc-tiep/([^/?#]+)",
            url
        )

        if not match:
            return "Unknown", "Unknown", "Chưa có lịch"

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

            teams_slug = slug[: slug.rfind("-" + t)]

        else:

            thoi_gian = "Chưa có lịch"

            teams_slug = slug

        parts = teams_slug.split("-vs-", 1)

        doi_nha = (
            parts[0]
            .replace("-", " ")
            .title()
            .strip()
        )

        doi_khach = (
            parts[1]
            .replace("-", " ")
            .title()
            .strip()
            if len(parts) > 1
            else "Unknown"
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
            timeout=8
        )

        text = r.text[:500]

        return "#EXTM3U" in text

    except:

        return False


# =========================================================
# CAPTURE STREAM
# =========================================================
def capture_stream(context, match_url):

    page = context.new_page()

    Stealth().apply_stealth_sync(page)

    streams = set()

    # =====================================================
    # RESPONSE LISTENER
    # =====================================================
    def capture_m3u8(res):

        try:

            url = res.url

            ct = res.headers.get(
                "content-type",
                ""
            ).lower()

            if (
                ".m3u8" in url.lower()
                or "mpegurl" in ct
            ):

                if "ads" in url.lower():
                    return

                streams.add(url)

                print("\n====================== - main.py:204")
                print("🎯 REAL M3U8 FOUND - main.py:205")
                print(url)
                print("======================\n - main.py:207")

        except Exception as e:

            print("LISTENER ERROR: - main.py:211", e)

    page.on("response", capture_m3u8)

    try:

        # =================================================
        # ANTI BOT
        # =================================================
        page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        """)

        # =================================================
        # HOOK FETCH/XHR
        # =================================================
        page.add_init_script("""
        (() => {

            const origFetch = window.fetch;

            window.fetch = async (...args) => {

                const url = args[0];

                if (typeof url === 'string') {

                    if (
                        url.includes('.m3u8') ||
                        url.includes('.flv')
                    ) {

                        console.log(
                            'FETCH STREAM:',
                            url
                        );
                    }
                }

                return origFetch(...args);
            };

            const origOpen =
                XMLHttpRequest.prototype.open;

            XMLHttpRequest.prototype.open =
            function(method, url) {

                if (
                    url.includes('.m3u8') ||
                    url.includes('.flv')
                ) {

                    console.log(
                        'XHR STREAM:',
                        url
                    );
                }

                return origOpen.apply(
                    this,
                    arguments
                );
            };

        })();
        """)

        print(f"\n🌐 OPEN MATCH:")
        print(match_url)

        # =================================================
        # OPEN PAGE
        # =================================================
        page.goto(
            match_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        try:

            page.wait_for_load_state(
                "networkidle",
                timeout=10000
            )

        except:
            pass

        # =================================================
        # REMOVE OVERLAY
        # =================================================
        try:

            page.evaluate("""
            document.querySelectorAll('*')
            .forEach(el => {

                const style =
                    window.getComputedStyle(el);

                const zi =
                    parseInt(style.zIndex);

                if (
                    style.position === 'fixed'
                    && zi > 999
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

            if vp:

                cx = vp["width"] // 2
                cy = vp["height"] // 2

                page.mouse.click(cx, cy)

                page.wait_for_timeout(1500)

                page.mouse.click(cx, cy)

        except:
            pass

        # =================================================
        # IFRAME
        # =================================================
        for frame in page.frames:

            try:
                frame.click("video", timeout=3000)
            except:
                pass

            try:
                frame.click("button", timeout=2000)
            except:
                pass

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
        try:

            page.wait_for_response(
                lambda r:
                    ".m3u8" in r.url.lower()
                    or "mpegurl" in (
                        r.headers.get(
                            "content-type",
                            ""
                        ).lower()
                    ),
                timeout=20000
            )

        except:
            pass

        page.wait_for_timeout(5000)

    except PWTimeout:

        print("⚠️ PAGE TIMEOUT")

    except Exception as e:

        print("❌ STREAM ERROR:", e)

    finally:

        page.close()

    # =====================================================
    # CHOOSE BEST STREAM
    # =====================================================
    if streams:

        priority = []

        for s in streams:

            lower = s.lower()

            score = 0

            if "index.m3u8" in lower:
                score += 100

            if "master.m3u8" in lower:
                score += 90

            if ".m3u8" in lower:
                score += 50

            if "live" in lower:
                score += 20

            priority.append((score, s))

        priority.sort(reverse=True)

        best = priority[0][1]

        print("\n✅ FINAL STREAM:")
        print(best)

        if validate_m3u8(best):

            print("✅ VALID M3U8")

            return best

        else:

            print("❌ INVALID M3U8")

    return None


# =========================================================
# JSON
# =========================================================
def create_json(matches_data):

    export = {

        "playlist_name": "Sáng TV",

        "last_updated":
            datetime.datetime.now(VN_TZ)
            .strftime("%H:%M %d/%m/%Y"),

        "total_live":
            sum(
                1
                for m in matches_data
                if m.get("is_live")
            ),

        "total_streams":
            sum(
                1
                for m in matches_data
                if (
                    m.get("stream_url")
                    and
                    m.get("stream_url")
                    != WAITING_VIDEO_URL
                )
            ),

        "matches": matches_data,
    }

    return json.dumps(
        export,
        indent=2,
        ensure_ascii=False
    )


# =========================================================
# PUSH GITHUB
# =========================================================
def push_to_github(content, live, streams):

    if not GITHUB_TOKEN:

        print("⚠️ NO GH_TOKEN")

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

        print("✅ UPDATED GITHUB")

    except:

        repo.create_file(
            FILE_PATH,
            msg,
            content
        )

        print("✅ CREATED FILE")


# =========================================================
# MAIN
# =========================================================
def scrape_and_push():

    matches_data = []

    print("=" * 70)

    print(
        datetime.datetime.now(VN_TZ)
        .strftime("START %H:%M:%S %d/%m/%Y")
    )

    print("=" * 70)

    with sync_playwright() as p:

        browser = p.chromium.launch(

            headless=True,

            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )

        ctx = browser.new_context(

            viewport={
                "width": 1920,
                "height": 1080
            },

            user_agent=_HEADERS["User-Agent"],

            java_script_enabled=True,

            bypass_csp=True,

            ignore_https_errors=True,
        )

        page = ctx.new_page()

        Stealth().apply_stealth_sync(page)

        print("\n📋 LOAD MATCH LIST")

        page.goto(
            TARGET_SITE,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        # =================================================
        # SCROLL
        # =================================================
        for _ in range(5):

            page.mouse.wheel(0, 3000)

            page.wait_for_timeout(1000)

        seen = set()

        valid = []

        for link in page.locator(
            "a[href*='/truc-tiep/']"
        ).all():

            href = link.get_attribute("href") or ""

            if "-vs-" in href:

                if href not in seen:

                    seen.add(href)

                    valid.append(link)

        if LIMIT_MATCHES:
            valid = valid[:LIMIT_MATCHES]

        print(f"✅ FOUND {len(valid)} MATCHES")

        # =================================================
        # BUILD MATCHES
        # =================================================
        for i, el in enumerate(valid):

            try:

                href = el.get_attribute("href") or ""

                if (
                    href
                    and
                    not href.startswith("http")
                ):

                    href = (
                        "/".join(
                            TARGET_SITE.split("/")[:3]
                        )
                        + href
                    )

                doi_nha, doi_khach, thoi_gian = (
                    parse_url_to_info(href)
                )

                is_live = True

                matches_data.append({

                    "id": str(i + 1),

                    "title":
                        f"{doi_nha} vs {doi_khach}",

                    "doi_nha": doi_nha,

                    "doi_khach": doi_khach,

                    "thoi_gian": thoi_gian,

                    "is_live": is_live,

                    "logo_nha":
                        get_team_logo(doi_nha),

                    "logo_khach":
                        get_team_logo(doi_khach),

                    "stream_url":
                        WAITING_VIDEO_URL,

                    "link_xem": href,
                })

                print(
                    f"[{i+1}] "
                    f"{doi_nha} vs {doi_khach}"
                )

            except Exception as e:

                print("❌ MATCH ERROR:", e)

        page.close()

        # =================================================
        # CAPTURE STREAMS
        # =================================================
        live_matches = [
            m
            for m in matches_data
            if m["is_live"]
        ]

        print(
            f"\n🎥 CAPTURE "
            f"{len(live_matches)} STREAMS"
        )

        for idx, match in enumerate(live_matches):

            print(
                f"\n[{idx+1}/{len(live_matches)}]"
            )

            print(match["title"])

            stream = capture_stream(
                ctx,
                match["link_xem"]
            )

            if stream:

                match["stream_url"] = stream

                print("✅ STREAM SAVED")

            else:

                print("❌ NO STREAM")

        browser.close()

    # =====================================================
    # SAVE
    # =====================================================
    if not matches_data:

        print("❌ NO DATA")

        return

    live_cnt = sum(
        1
        for m in matches_data
        if m["is_live"]
    )

    stream_cnt = sum(
        1
        for m in matches_data
        if m["stream_url"] != WAITING_VIDEO_URL
    )

    content = create_json(matches_data)

    push_to_github(
        content,
        live_cnt,
        stream_cnt
    )

    print("\n" + "=" * 70)

    print(
        f"✅ DONE | "
        f"{len(matches_data)} matches | "
        f"{stream_cnt} streams"
    )

    print("=" * 70)


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":

    scrape_and_push()
