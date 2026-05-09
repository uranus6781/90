import os
import datetime
import re
import time
import requests
import json
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from github import Github

# ==========================================
# CẤU HÌNH
# ==========================================
TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")
FILE_PATH = "bongda.json"
WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"
LIMIT_MATCHES = 10
MAX_STREAM_WAIT = 25      # giây tối đa chờ m3u8 mỗi trận
STREAM_POLL_INTERVAL = 1  # giây poll một lần

LOGO_CACHE = {}

# ==========================================
# LẤY LOGO — WIKIPEDIA → THESPORTSDB → UI-AVATARS
# ==========================================
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

def _logo_wikipedia(team_name: str) -> str | None:
    """Tìm logo qua Wikipedia API (không bị block, miễn phí)."""
    try:
        # Bước 1: tìm trang
        search_url = "https://en.wikipedia.org/w/api.php"
        r = requests.get(search_url, params={
            "action": "query", "list": "search",
            "srsearch": f"{team_name} football club",
            "srlimit": 1, "format": "json"
        }, headers=_HEADERS, timeout=6)
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None

        page_title = results[0]["title"]

        # Bước 2: lấy ảnh chính (pageimage)
        r2 = requests.get(search_url, params={
            "action": "query", "titles": page_title,
            "prop": "pageimages", "pithumbsize": 200,
            "format": "json"
        }, headers=_HEADERS, timeout=6)
        pages = r2.json().get("query", {}).get("pages", {})
        for page in pages.values():
            thumb = page.get("thumbnail", {}).get("source")
            if thumb:
                return thumb
    except Exception:
        pass
    return None


def _logo_thesportsdb(team_name: str) -> str | None:
    """TheSportsDB — DB bóng đá miễn phí, logo chất lượng cao."""
    try:
        r = requests.get(
            f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
            params={"t": team_name},
            headers=_HEADERS, timeout=6
        )
        teams = r.json().get("teams")
        if teams:
            logo = teams[0].get("strTeamBadge") or teams[0].get("strTeamLogo")
            if logo:
                # Thêm /preview để lấy ảnh nhỏ hơn, load nhanh hơn
                return logo + "/preview" if not logo.endswith("/preview") else logo
    except Exception:
        pass
    return None


def _logo_fallback(team_name: str) -> str:
    """Fallback: avatar chữ cái đầu — luôn thành công."""
    initials = "".join(w[0].upper() for w in team_name.split()[:3] if w)
    return (
        f"https://ui-avatars.com/api/?name={requests.utils.quote(initials)}"
        f"&size=200&background=1a3e6e&color=ffffff&bold=true&format=png"
    )


def get_team_logo(team_name: str) -> str:
    """Lấy logo theo thứ tự: cache → Wikipedia → TheSportsDB → fallback."""
    if not team_name or team_name in ("Unknown", ""):
        return _logo_fallback("?")

    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]

    logo = _logo_wikipedia(team_name) \
        or _logo_thesportsdb(team_name) \
        or _logo_fallback(team_name)

    LOGO_CACHE[team_name] = logo
    print(f"      🏷  Logo [{team_name}]: {logo[:60]}...")
    return logo


# ==========================================
# PARSE URL → THÔNG TIN TRẬN
# ==========================================
def parse_url_to_info(url: str) -> tuple[str, str, str]:
    """Trả về (doi_nha, doi_khach, thoi_gian) từ slug URL."""
    try:
        match = re.search(r'/truc-tiep/([^/?#]+)', url)
        if not match:
            return "Unknown", "Unknown", "Chưa có lịch"

        slug = match.group(1)

        # Dạng: ten-doi-a-vs-ten-doi-b-2200-08-05-2026
        time_match = re.search(r'(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
        if time_match:
            t = time_match.group(1)   # "2200-08-05-2026"
            hour, minute = t[0:2], t[2:4]
            day, month, year = t[5:7], t[8:10], t[11:15]
            thoi_gian = f"{hour}:{minute} {day}/{month}/{year}"
            teams_slug = slug[: slug.rfind('-' + t)]
        else:
            thoi_gian = "Chưa có lịch"
            teams_slug = slug

        parts = teams_slug.split('-vs-', 1)
        doi_nha = parts[0].replace('-', ' ').title().strip()
        doi_khach = parts[1].replace('-', ' ').title().strip() if len(parts) > 1 else "Unknown"
        return doi_nha, doi_khach, thoi_gian

    except Exception as e:
        print(f"  [!] parse_url lỗi: {e}")
        return "Unknown", "Unknown", "Unknown"


# ==========================================
# BẮT LUỒNG M3U8 — THÔNG MINH HƠN
# ==========================================
_AD_KEYWORDS = [
    "ads", "ad.", "/ad/", "advertisement", "doubleclick",
    "googlesyndication", "quangcao", "preroll", "midroll",
]
_SKIP_SELECTORS = [
    ".skip-ad-btn", ".vast-skip-button", ".skip-button",
    "[class*='skip']", "[id*='skip']",
    "xpath=//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'bỏ qua')]",
    "xpath=//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'skip ad')]",
]

def capture_stream(page, match_url: str) -> str | None:
    """
    Bắt luồng m3u8 / flv:
    - Poll từng giây thay vì chờ cứng
    - Lọc quảng cáo
    - Tối đa MAX_STREAM_WAIT giây
    """
    streams: list[str] = []
    ad_streams: set[str] = set()

    def on_request(req):
        url = req.url.lower()
        if ".mp4" in url:
            return
        if ".m3u8" in url or ".flv" in url:
            is_ad = any(kw in url for kw in _AD_KEYWORDS)
            if is_ad:
                ad_streams.add(req.url)
            elif req.url not in streams:
                streams.append(req.url)

    try:
        page.on("request", on_request)
        page.goto(match_url, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_timeout(2_000)

        # Kích hoạt player
        _trigger_player(page)

        # Chờ thông minh: poll mỗi 1s, dừng sớm nếu đã có luồng xịn
        deadline = time.time() + MAX_STREAM_WAIT
        skip_attempted = False

        while time.time() < deadline:
            time.sleep(STREAM_POLL_INTERVAL)

            # Thử skip quảng cáo một lần
            if not skip_attempted and ad_streams:
                skip_attempted = True
                if _try_skip_ad(page):
                    print("         🔪 Đã skip quảng cáo, xóa luồng ads...")
                    streams.clear()   # luồng trước skip = quảng cáo
                    ad_streams.clear()

            # Dừng sớm nếu đã có luồng xịn
            if streams:
                print(f"         ✅ Có luồng sau {MAX_STREAM_WAIT - (deadline - time.time()):.0f}s")
                break

        remaining = deadline - time.time()
        if remaining > 0 and not streams:
            print(f"         ⏳ Chờ thêm {remaining:.0f}s...")

    except PWTimeout:
        print("         ⚠️  Timeout trang")
    except Exception as e:
        print(f"         ❌ Lỗi: {e}")
    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass

    # Ưu tiên luồng chứa "live" hoặc luồng cuối cùng
    live_streams = [s for s in streams if "live" in s.lower()]
    return (live_streams or streams or [None])[-1]


def _trigger_player(page) -> None:
    """Kích hoạt video player bằng nhiều cách."""
    try:
        page.evaluate("""
            document.querySelectorAll('video').forEach(v => {
                v.muted = true;
                v.play().catch(() => {});
            });
        """)
    except Exception:
        pass

    for selector in [".vjs-big-play-button", ".jw-icon-display",
                     ".play-btn", ".play-wrapper", "[class*='play']"]:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=800):
                btn.click(timeout=800)
                break
        except Exception:
            pass

    try:
        vp = page.viewport_size
        if vp:
            page.mouse.click(vp["width"] / 2, vp["height"] / 2)
    except Exception:
        pass


def _try_skip_ad(page) -> bool:
    """Thử click nút skip quảng cáo. Trả True nếu thành công."""
    for sel in _SKIP_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1_500):
                btn.click(timeout=1_500)
                page.wait_for_timeout(1_500)
                return True
        except Exception:
            pass
    return False


# ==========================================
# TẠO JSON
# ==========================================
def create_json(matches_data: list) -> str:
    live_count = sum(1 for m in matches_data if m.get("is_live"))
    stream_count = sum(1 for m in matches_data if m.get("luong_video"))

    export = {
        "playlist_name": "Sáng TV",
        "last_updated": datetime.datetime.now().strftime("%H:%M %d/%m/%Y"),
        "total_live": live_count,
        "total_streams": stream_count,
        "matches": []
    }

    for idx, m in enumerate(matches_data, 1):
        title = m.get("title", "Unknown vs Unknown")
        parts = title.split(" vs ", 1)
        doi_nha = parts[0].strip()
        doi_khach = parts[1].strip() if len(parts) > 1 else "Unknown"

        stream = m.get("luong_video") or WAITING_VIDEO_URL

        export["matches"].append({
            "id": str(idx),
            "doi_nha": doi_nha,
            "doi_khach": doi_khach,
            "logo_nha": m.get("logo_doi_nha", ""),
            "logo_khach": m.get("logo_doi_khach", ""),
            "thoi_gian": m.get("thoi_gian", "Chưa có lịch"),
            "trang_thai": m.get("trang_thai", "Chưa đá"),
            "is_live": m.get("is_live", False),
            "stream_url": stream,
        })

    return json.dumps(export, indent=2, ensure_ascii=False)


# ==========================================
# PUSH LÊN GITHUB
# ==========================================
def push_to_github(content: str, live: int, streams: int) -> None:
    if not GITHUB_TOKEN:
        print("⚠️  Không có GH_TOKEN, lưu local.")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    now = datetime.datetime.now().strftime("%H:%M %d/%m/%Y")
    msg = f"⚽ {now} — {live} live, {streams} streams"

    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print(f"✅ Đã cập nhật GitHub: {FILE_PATH}")
    except Exception:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã tạo mới trên GitHub: {FILE_PATH}")


# ==========================================
# HÀM CHÍNH
# ==========================================
def scrape_and_push():
    matches_data = []
    print("=" * 65)
    print(f"⏰ BẮT ĐẦU: {datetime.datetime.now().strftime('%H:%M:%S %d/%m/%Y')}")
    print("=" * 65)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = ctx.new_page()

        # ── BƯỚC 1: Lấy danh sách trận ──────────────────────────
        print("\n📋 BƯỚC 1: Lấy danh sách trận...")
        try:
            page.goto(TARGET_SITE, timeout=60_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception as e:
            print(f"  ⚠️  Load chậm: {e}")

        for _ in range(3):
            page.evaluate("window.scrollBy(0, 900)")
            page.wait_for_timeout(800)

        seen_hrefs: set[str] = set()
        valid: list = []
        for link in page.locator("a[href*='/truc-tiep/']").all():
            href = link.get_attribute("href") or ""
            if "-vs-" in href and href not in seen_hrefs:
                seen_hrefs.add(href)
                valid.append(link)
        if LIMIT_MATCHES:
            valid = valid[:LIMIT_MATCHES]
        print(f"   ✓ Tìm thấy {len(valid)} trận")

        # ── BƯỚC 2: Phân tích thông tin ─────────────────────────
        print("\n📊 BƯỚC 2: Phân tích trận & lấy logo...")
        for i, el in enumerate(valid):
            try:
                href = el.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    base = "/".join(TARGET_SITE.split("/")[:3])
                    href = base + href

                doi_nha, doi_khach, thoi_gian = parse_url_to_info(href)

                # Xác định live
                raw_text = ""
                try:
                    raw_text = (el.inner_text() + " " +
                                el.locator("xpath=./ancestor::div[3]").inner_text()).upper()
                except Exception:
                    raw_text = el.inner_text().upper()

                is_live = any(kw in raw_text for kw in
                              ["LIVE", "TRỰC TIẾP", "HIỆP", "PHÚT", "ĐANG"])
                status = "Đang trực tiếp 🔴" if is_live else "Chưa đá"

                # Logo (song song 2 đội)
                logo_nha = get_team_logo(doi_nha)
                logo_khach = get_team_logo(doi_khach)

                matches_data.append({
                    "title": f"{doi_nha} vs {doi_khach}",
                    "doi_nha": doi_nha,
                    "doi_khach": doi_khach,
                    "trang_thai": status,
                    "is_live": is_live,
                    "thoi_gian": thoi_gian,
                    "logo_doi_nha": logo_nha,
                    "logo_doi_khach": logo_khach,
                    "link_xem": href,
                    "luong_video": "",
                })
                print(f"   [{i+1:2d}/{len(valid)}] {'🔴' if is_live else '⚪'} "
                      f"{doi_nha} vs {doi_khach}  |  {thoi_gian}")
            except Exception as e:
                print(f"   [!] Lỗi trận {i+1}: {e}")

        # ── BƯỚC 3: Bắt luồng trận live ─────────────────────────
        live_matches = [m for m in matches_data if m["is_live"]]
        print(f"\n🎥 BƯỚC 3: Bắt luồng {len(live_matches)} trận live...")

        for idx, match in enumerate(live_matches):
            print(f"\n   [{idx+1}/{len(live_matches)}] {match['title']}")
            stream = capture_stream(page, match["link_xem"])
            if stream:
                match["luong_video"] = stream
                print(f"         ✅ {stream[:70]}...")
            else:
                print("         ❌ Không tìm được luồng")
            page.wait_for_timeout(500)

        browser.close()

    # ── BƯỚC 4: Đẩy lên GitHub ──────────────────────────────────
    if not matches_data:
        print("\n❌ Không có dữ liệu!")
        return

    live_cnt = sum(1 for m in matches_data if m["is_live"])
    stream_cnt = sum(1 for m in matches_data if m["luong_video"])
    json_text = create_json(matches_data)
    push_to_github(json_text, live_cnt, stream_cnt)

    print(f"\n{'='*65}")
    print(f"✅ XONG — {len(matches_data)} trận | {live_cnt} live | {stream_cnt} có luồng")
    print("=" * 65)


if __name__ == "__main__":
    scrape_and_push()
