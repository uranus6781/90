import os
import re
import time
import json
import datetime
import requests
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ==========================================
# CẤU HÌNH CÁC KÊNH (MULTI-CHANNEL)
# ==========================================
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

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dausoco")
FILE_PATH    = "bongda.json"
WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"

LIMIT_MATCHES_PER_CHANNEL = 15
MAX_STREAM_WAIT   = 40
STREAM_POLL_INTERVAL = 1

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

# ==========================================
# PARSE DANH SÁCH TRẬN TỪ HTML (LINH HOẠT CHO MỌI DOMAIN)
# ==========================================
def _is_team_logo_url(url: str) -> bool:
    """Loại trừ các ảnh rác, icon, background của web để giữ lại đúng Logo đội bóng"""
    u = url.lower()
    bad_keywords = ["/categories/", "header", "logo.svg", "earth.png", 
                    "user_avatar", "header-mobi", "icon", "banner", "bg", ".gif"]
    return not any(b in u for b in bad_keywords)

def parse_match_block(html_block: str, base_url: str) -> dict | None:
    try:
        # Lấy href linh hoạt (có chứa -vs-)
        href_m = re.search(r'href="(/[^"]+-vs-[^"]+)"', html_block)
        if not href_m: return None
        href = base_url + href_m.group(1)

        # Trích xuất tất cả thẻ <img> để tìm Logo và Tên đội (Alt)
        img_tags = re.findall(r'<img[^>]+>', html_block)
        team_logos = []
        team_names = []
        
        for tag in img_tags:
            src_m = re.search(r'src="([^"]+)"', tag)
            alt_m = re.search(r'alt="([^"]+)"', tag)
            if src_m:
                src = src_m.group(1)
                # Sửa lỗi thiếu https:// ở một số web
                if src.startswith("//"): src = "https:" + src
                elif src.startswith("/"): src = base_url + src
                
                if _is_team_logo_url(src):
                    team_logos.append(src)
                    team_names.append(alt_m.group(1).strip() if alt_m else "")

        logo_nha   = team_logos[0] if len(team_logos) >= 1 else ""
        logo_khach = team_logos[1] if len(team_logos) >= 2 else ""
        doi_nha    = team_names[0] if len(team_names) >= 1 else ""
        doi_khach  = team_names[1] if len(team_names) >= 2 else ""

        # Fallback tên đội từ URL nếu thẻ alt rỗng
        if not doi_nha or not doi_khach:
            fn, fk, _ = parse_url_to_info(href)
            if not doi_nha: doi_nha = fn
            if not doi_khach: doi_khach = fk

        _, _, thoi_gian = parse_url_to_info(href)
        is_live = bool(re.search(r'(?i)\bLive\b|Đang trực tiếp', html_block))

        return {
            "href": href,
            "doi_nha": doi_nha, "doi_khach": doi_khach,
            "logo_nha": logo_nha, "logo_khach": logo_khach,
            "thoi_gian": thoi_gian, "is_live": is_live,
        }
    except Exception as e:
        return None

def fetch_match_list_from_html(html: str, base_url: str) -> list[dict]:
    # Tìm tất cả các thẻ <a> bao trọn 1 trận đấu
    blocks = re.findall(r'(<a\s+[^>]*href="/[^"]*-vs-[^"]*"[^>]*>.*?</a>)', html, re.DOTALL)
    results, seen = [], set()
    for block in blocks:
        parsed = parse_match_block(block, base_url)
        if parsed and parsed["href"] not in seen:
            seen.add(parsed["href"])
            results.append(parsed)
    return results

# ==========================================
# PARSE URL → THÔNG TIN TRẬN
# ==========================================
def parse_url_to_info(url: str) -> tuple[str, str, str]:
    try:
        if "-vs-" not in url: return "Unknown", "Unknown", "Chưa có lịch"
        
        # Bóc tách đoạn cuối của URL
        slug = url.rstrip('/').split('/')[-1].split('?')[0].split('#')[0]
        slug = re.sub(r'/?\d{6,}$', '', slug) # Cắt bỏ ID số nếu có
        
        t_m  = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
        if t_m:
            t = t_m.group(1)
            thoi_gian  = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            teams_slug = slug[: slug.rfind('-' + t)]
        else:
            thoi_gian, teams_slug = "Chưa có lịch", slug
            
        parts = teams_slug.split('-vs-', 1)
        doi_nha   = parts[0].replace('-', ' ').title().strip()
        doi_khach = parts[1].replace('-', ' ').title().strip() if len(parts) > 1 else "Unknown"
        return doi_nha, doi_khach, thoi_gian
    except Exception:
        return "Unknown", "Unknown", "Unknown"

# ==========================================
# BẮT LUỒNG M3U8
# ==========================================
_AD_KEYWORDS = ["/vast/", "advertisement", "doubleclick.net", "googlesyndication", "quangcao", "preroll", "midroll", "ad-stream"]
_SKIP_SELECTORS = [
    ".skip-ad-btn", ".vast-skip-button", ".skip-button", "[class*='skip']", "[id*='skip']",
    "xpath=//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'bỏ qua')]"
]

def _trigger_player_in_frame(frame) -> None:
    try: frame.evaluate("document.querySelectorAll('video').forEach(v => { v.muted = true; v.play().catch(() => {}); });")
    except: pass
    for sel in [".vjs-big-play-button", ".jw-icon-display", ".play-btn", ".play-wrapper", "[class*='play']"]:
        try:
            el = frame.locator(sel).first
            if el.is_visible(timeout=500):
                el.click(timeout=500)
                break
        except: pass

def _try_skip_ad(page) -> bool:
    for sel in _SKIP_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click(timeout=1000)
                page.wait_for_timeout(1000)
                return True
        except: pass
    return False

def capture_stream(context, match_url: str) -> str | None:
    page = context.new_page()
    streams, ad_streams = [], set()

    def on_request(req):
        url, u = req.url, req.url.lower()
        if ".mp4" in u: return
        if ".m3u8" in u or ".flv" in u or "playlist" in u:
            if any(kw in u for kw in _AD_KEYWORDS):
                ad_streams.add(url)
            elif url not in streams:
                streams.append(url)
                print(f"      📶 Bắt được: {url[:90]}")

    try:
        page.on("request", on_request)
        page.set_extra_http_headers({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
        })

        page.goto(match_url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        _trigger_player_in_frame(page)
        try:
            vp = page.viewport_size
            if vp: page.mouse.click(vp["width"] / 2, vp["height"] / 2)
        except: pass

        for frame in page.frames:
            if frame != page.main_frame: _trigger_player_in_frame(frame)

        deadline = time.time() + MAX_STREAM_WAIT
        skip_attempted = False
        last_frame_count = len(page.frames)

        while time.time() < deadline:
            time.sleep(STREAM_POLL_INTERVAL)
            cur = page.frames
            if len(cur) != last_frame_count:
                last_frame_count = len(cur)
                for frame in cur:
                    if frame != page.main_frame: _trigger_player_in_frame(frame)

            if not skip_attempted and ad_streams:
                skip_attempted = True
                if _try_skip_ad(page):
                    print("      🔪 Skip quảng cáo, reset luồng...")
                    streams.clear(); ad_streams.clear()

            if streams:
                print(f"      ✅ Có luồng sau {MAX_STREAM_WAIT - (deadline - time.time()):.0f}s")
                break

    except PWTimeout: print("      ⚠️  Timeout")
    except Exception as e: print(f"      ❌ Lỗi: {e}")
    finally:
        try: page.remove_listener("request", on_request)
        except: pass
        page.close()

    live_streams = [s for s in streams if "live" in s.lower()]
    return (live_streams or streams or [None])[0]

# ==========================================
# TẠO JSON & PUSH LÊN GITHUB
# ==========================================
def create_json(all_channel_data: dict) -> str:
    total_live = 0
    total_streams = 0
    
    # Đếm tổng quan
    for matches in all_channel_data.values():
        total_live += sum(1 for m in matches if m.get("is_live"))
        total_streams += sum(1 for m in matches if m.get("stream_url") and m["stream_url"] != WAITING_VIDEO_URL)

    export_data = {
        "playlist_name": "Sáng TV",
        "last_updated":  datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "total_live":    total_live,
        "total_streams": total_streams,
    }
    
    # Gộp list theo Key (buncha, hoiquan,...)
    for channel_id, matches in all_channel_data.items():
        export_data[channel_id] = matches
        
    return json.dumps(export_data, indent=2, ensure_ascii=False)

def push_to_github(content: str) -> None:
    if not GITHUB_TOKEN:
        print("⚠️  Không có GH_TOKEN, lưu local.")
        with open(FILE_PATH, "w", encoding="utf-8") as f: f.write(content)
        return
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg = f"⚽ Cập nhật Đa Kênh: {datetime.datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print(f"✅ Đã cập nhật GitHub: {FILE_PATH}")
    except Exception:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã tạo mới GitHub: {FILE_PATH}")

# ==========================================
# HÀM CHÍNH
# ==========================================
def scrape_and_push():
    all_channel_data = {channel["id"]: [] for channel in CHANNELS}
    
    print("=" * 65)
    print(f"⏰ BẮT ĐẦU CÀO ĐA KÊNH: {datetime.datetime.now(VN_TZ).strftime('%H:%M:%S %d/%m/%Y')}")
    print("=" * 65)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required", "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process", "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="vi-VN", timezone_id="Asia/Ho_Chi_Minh", java_script_enabled=True,
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
        """)

        # ─── BƯỚC 1 & 2: LẤY VÀ PARSE DANH SÁCH TỪNG KÊNH ───
        for channel in CHANNELS:
            print(f"\n📺 ĐANG QUÉT KÊNH: {channel['name'].upper()} ({channel['url']})")
            page = ctx.new_page()
            try:
                page.goto(channel["url"], timeout=60000)
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as e:
                print(f"   ⚠️ Load chậm: {e}")

            # Cuộn trang để ép tải hình ảnh
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 900)")
                page.wait_for_timeout(600)
            page.wait_for_timeout(1000)

            full_html = page.content()
            page.close()

            parsed_list = fetch_match_list_from_html(full_html, channel["base_url"])
            if LIMIT_MATCHES_PER_CHANNEL: parsed_list = parsed_list[:LIMIT_MATCHES_PER_CHANNEL]
            print(f"   ✓ Tìm thấy {len(parsed_list)} trận")

            for i, item in enumerate(parsed_list):
                doi_nha, doi_khach = item["doi_nha"], item["doi_khach"]
                thoi_gian, is_live = item["thoi_gian"], item["is_live"]
                
                # Tính chuẩn giờ thực tế
                status = "Đang trực tiếp 🔴" if is_live else "Chưa đá ⏳"
                try:
                    match_time = datetime.datetime.strptime(thoi_gian, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                    diff_minutes = (datetime.datetime.now(VN_TZ) - match_time).total_seconds() / 60
                    if -10 <= diff_minutes <= 120:   is_live, status = True, "Đang trực tiếp 🔴"
                    elif diff_minutes > 120:         is_live, status = False, "Đã kết thúc 🏁"
                    elif diff_minutes < -10:         is_live, status = False, "Chưa đá ⏳"
                except: pass

                all_channel_data[channel["id"]].append({
                    "id":         str(i + 1),
                    "title":      f"{doi_nha} vs {doi_khach}",
                    "doi_nha":    doi_nha,
                    "doi_khach":  doi_khach,
                    "trang_thai": status,
                    "is_live":    is_live,
                    "thoi_gian":  thoi_gian,
                    "logo_nha":   item["logo_nha"],
                    "logo_khach": item["logo_khach"],
                    "link_xem":   item["href"],
                    "stream_url": WAITING_VIDEO_URL,
                })
                print(f"      [{i+1:2d}] {'🔴' if is_live else '⚪'} {doi_nha} vs {doi_khach} | {thoi_gian}")

        # ─── BƯỚC 3: BẮT LUỒNG M3U8 CHO CÁC TRẬN LIVE Ở MỌI KÊNH ───
        print("\n🎥 TIẾN HÀNH BẮT LUỒNG M3U8...")
        for channel in CHANNELS:
            matches = all_channel_data[channel["id"]]
            live_matches = [m for m in matches if m["is_live"]]
            
            if not live_matches:
                print(f"   ► {channel['name']}: Không có trận Live nào.")
                continue
                
            print(f"\n   ► {channel['name']}: Đang xử lý {len(live_matches)} trận Live")
            for idx, match in enumerate(live_matches):
                print(f"\n      [{idx+1}/{len(live_matches)}] {match['title']}")
                stream = capture_stream(ctx, match["link_xem"])
                if stream:
                    match["stream_url"] = stream
                else:
                    print("      ❌ Không tìm được luồng")

        browser.close()

    # Lưu và Đẩy JSON
    push_to_github(create_json(all_channel_data))
    print("\n" + "=" * 65)
    print("✅ HOÀN TẤT CÀO DỮ LIỆU ĐA KÊNH!")
    print("=" * 65)

if __name__ == "__main__":
    scrape_and_push()
