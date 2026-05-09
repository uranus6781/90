import os
import datetime
import re
import requests
import json
from playwright.sync_api import sync_playwright
from github import Github

# ==========================================
# CẤU HÌNH HỆ THỐNG
# ==========================================
TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")
FILE_PATH = "bongda.json" # Đã đổi thành file JSON
WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"
LIMIT_MATCHES = 10

LOGO_CACHE = {}

# ==========================================
# HÀM LẤY LOGO (SOFASCORE & UI-AVATARS)
# ==========================================
def get_team_logo(team_name):
    if not team_name or team_name == "Unknown" or len(team_name) < 3:
        return "https://cdn.sofascore.app/api/v1/team/placeholder/image"
    
    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]
    
    try:
        search_url = f"https://api.sofascore.app/api/v1/search/all?q={requests.utils.quote(team_name)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(search_url, headers=headers, timeout=5)
        data = response.json()
        
        if data and "results" in data:
            for item in data["results"]:
                if item.get("type") == "team":
                    team_id = item["entity"]["id"]
                    raw_logo = f"https://api.sofascore.app/api/v1/team/{team_id}/image"
                    optimized_logo = f"https://wsrv.nl/?url={raw_logo}&w=200&h=200&fit=contain&output=png"
                    LOGO_CACHE[team_name] = optimized_logo
                    return optimized_logo
    except:
        pass
    
    fallback = f"https://ui-avatars.com/api/?name={requests.utils.quote(team_name)}&size=200&background=1a73e8&color=fff&bold=true&format=png"
    LOGO_CACHE[team_name] = fallback
    return fallback

# ==========================================
# HÀM PARSE THỜI GIAN
# ==========================================
def parse_url_to_info_buncha(url):
    try:
        match = re.search(r'/truc-tiep/([^/]+)', url)
        if match:
            slug = match.group(1)
            # Cấu trúc web Buncha: HHmm-DD-MM-YYYY (VD: 2200-08-05-2026)
            time_match = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
            if time_match:
                time_str = time_match.group(1)
                
                hour = time_str[0:2]
                minute = time_str[2:4]
                day = time_str[5:7]
                month = time_str[8:10]
                year = time_str[11:15]
                
                thoi_gian = f"{hour}:{minute} {day}/{month}/{year}"
                teams_part = slug[:slug.rfind('-' + time_str)]
            else:
                teams_part = slug
                thoi_gian = "Chưa có lịch"

            teams = teams_part.split('-vs-')
            doi_nha = teams[0].replace('-', ' ').title()
            doi_khach = teams[1].replace('-', ' ').title() if len(teams) > 1 else "Unknown"
            
            return doi_nha.strip(), doi_khach.strip(), thoi_gian
    except Exception as e:
        print(f"  [!] Lỗi parse URL: {e}")
    return "Unknown", "Unknown", "Unknown"

# ==========================================
# HÀM BẮT LUỒNG (CHỜ 8S VÀ CHÉM QUẢNG CÁO)
# ==========================================
def capture_video_streams(page, match_url):
    video_streams = []
    
    def request_handler(request):
        url = request.url.lower()
        if (".m3u8" in url or ".flv" in url) and ".mp4" not in url:
            if not any(ad in url for ad in ["ads", "quangcao", "advertisement", "doubleclick"]):
                if request.url not in video_streams:
                    video_streams.append(request.url)

    try:
        page.on("request", request_handler)
        page.goto(match_url, timeout=60000)
        page.wait_for_timeout(3000)
        
        try:
            page.evaluate("""
                document.querySelectorAll('video').forEach(v => {
                    v.muted = true; 
                    v.play().catch(e => console.log(e));
                });
            """)
        except:
            pass

        try:
            play_buttons = [".vjs-big-play-button", ".jw-icon-display", ".play-wrapper"]
            for btn in play_buttons:
                pb = page.locator(btn).first
                if pb.is_visible(timeout=1000):
                    pb.click()
        except:
            pass

        viewport_size = page.viewport_size
        if viewport_size:
            page.mouse.click(viewport_size['width']/2, viewport_size['height']/2)
            
        print("         ⏳ Đang chờ 8 giây để nhả quảng cáo...")
        page.wait_for_timeout(8000) 
        
        try:
            skip_selectors = [
                "xpath=//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'bỏ qua')]",
                "xpath=//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'skip')]",
                ".skip-ad-btn", ".vast-skip-button"
            ]
            for sel in skip_selectors:
                skip_btn = page.locator(sel).first
                if skip_btn.is_visible(timeout=3000):
                    skip_btn.click()
                    print("         🔪 Đã chém quảng cáo!")
                    video_streams.clear() 
                    page.wait_for_timeout(2000) 
                    break
        except:
            pass 
                
        print("         ⏳ Đang chờ luồng chính tải về...")
        page.wait_for_timeout(10000)
        
    except Exception as e:
        print(f"         ❌ LỖI VÀO PHÒNG: {e}")
    finally:
        page.remove_listener("request", request_handler)
        
    if video_streams:
        return video_streams[-1] 
    return None

# ==========================================
# HÀM TẠO JSON CHO APP ANDROID TV
# ==========================================
def create_json_for_tv_app(matches_data):
    """Đóng gói dữ liệu thành chuẩn JSON siêu sạch cho App Android TV"""
    export_data = {
        "playlist_name": "Bún Chả TV - Dậu App",
        "last_updated": datetime.datetime.now().strftime('%H:%M %d/%m/%Y'),
        "total_live": sum(1 for m in matches_data if "trực tiếp" in m["trang_thai"].lower()),
        "matches": []
    }
    
    for idx, match in enumerate(matches_data, 1):
        stream = match.get("luong_video", "")
        if not stream:
            stream = WAITING_VIDEO_URL
            
        export_data["matches"].append({
            "id": str(idx),
            "doi_nha": match["title"].split(' vs ')[0] if ' vs ' in match["title"] else match["title"],
            "doi_khach": match["title"].split(' vs ')[1] if ' vs ' in match["title"] else "Unknown",
            "logo_nha": match.get("logo_doi_nha", ""),
            "logo_khach": match.get("logo_doi_khach", ""),
            "thoi_gian": match.get("thoi_gian", "Chưa có lịch"),
            "trang_thai": match.get("trang_thai", "Chưa đá"),
            "is_live": True if "trực tiếp" in match.get("trang_thai", "").lower() else False,
            "stream_url": stream
        })
        
    return json.dumps(export_data, indent=4, ensure_ascii=False)

# ==========================================
# HÀM CHÍNH SCRAPE
# ==========================================
def scrape_and_push():
    matches_data = []
    
    print("=" * 70)
    print(f"⏰ BẮT ĐẦU QUÉT: {datetime.datetime.now().strftime('%H:%M:%S %d/%m/%Y')}")
    print("=" * 70)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-web-security',
                '--disable-features=IsolateOrigins',
                '--autoplay-policy=no-user-gesture-required' 
            ]
        )
        
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()
        
        try:
            print("\n📋 BƯỚC 1: Lấy danh sách trận đấu...")
            page.goto(TARGET_SITE, timeout=60000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)
            
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(1000)
            
            all_links = page.locator("a[href*='/truc-tiep/']").all()
            valid_matches = []
            seen = set()
            
            for link in all_links:
                href = link.get_attribute("href")
                if href and '-vs-' in href and href not in seen:
                    seen.add(href)
                    valid_matches.append(link)
            
            if LIMIT_MATCHES:
                valid_matches = valid_matches[:LIMIT_MATCHES]
            
            print(f"   ✓ Tìm thấy {len(valid_matches)} trận đấu")
            
            print("\n📊 BƯỚC 2: Phân tích thông tin (Chỉ dùng SofaScore)...")
            for i, el in enumerate(valid_matches):
                try:
                    match_url = el.get_attribute("href")
                    if match_url and not match_url.startswith("http"):
                        domain = "/".join(TARGET_SITE.split("/")[:3])
                        match_url = domain + match_url
                    
                    doi_nha, doi_khach, thoi_gian = parse_url_to_info_buncha(match_url)
                    
                    raw_text = el.inner_text().upper()
                    try:
                        parent_text = el.locator("xpath=./ancestor::div[3]").inner_text().upper()
                        raw_text = raw_text + " " + parent_text
                    except:
                        pass
                    
                    is_live = any(kw in raw_text for kw in ["LIVE", "TRỰC TIẾP", "HIỆP", "PHÚT"])
                    status = "Đang trực tiếp" if is_live else "Chưa đá"
                    
                    logo_nha = get_team_logo(doi_nha)
                    logo_khach = get_team_logo(doi_khach)
                    
                    matches_data.append({
                        "title": f"{doi_nha} vs {doi_khach}",
                        "trang_thai": status,
                        "thoi_gian": thoi_gian,
                        "logo_doi_nha": logo_nha,
                        "logo_doi_khach": logo_khach,
                        "link_xem": match_url,
                        "luong_video": ""
                    })
                    
                    print(f"   [{i+1:2d}/{len(valid_matches)}] {status}: {doi_nha} vs {doi_khach}")
                    
                except Exception as e:
                    print(f"   [!] Lỗi xử lý trận {i+1}: {e}")
                    continue
            
            print(f"\n🎥 BƯỚC 3: Bắt luồng video cho {len([m for m in matches_data if 'trực tiếp' in m['trang_thai'].lower()])} trận live...")
            for idx, match in enumerate([m for m in matches_data if "trực tiếp" in m["trang_thai"].lower()]):
                print(f"\n   [{idx+1}] Đang dò luồng: {match['title']}")
                stream_url = capture_video_streams(page, match["link_xem"])
                if stream_url:
                    match["luong_video"] = stream_url
                    print(f"         ✅ LUỒNG XỊN: {stream_url[:60]}...")
                else:
                    print(f"         ❌ KHÔNG có luồng")
                page.wait_for_timeout(1000)
            
        except Exception as e:
            print(f"\n❌ LỖI HỆ THỐNG: {e}")
        finally:
            browser.close()
    
    if not matches_data:
        print("\n❌ KHÔNG có dữ liệu!")
        return
    
    print(f"\n📝 Tạo file JSON...")
    json_text = create_json_for_tv_app(matches_data)
    
    if GITHUB_TOKEN:
        try:
            g = Github(GITHUB_TOKEN)
            repo = g.get_repo(REPO_NAME)
            
            now = datetime.datetime.now()
            total_live = sum(1 for m in matches_data if "trực tiếp" in m["trang_thai"].lower())
            total_stream = sum(1 for m in matches_data if m["luong_video"])
            
            commit_msg = f"⚽ Update JSON API: {now.strftime('%H:%M %d/%m/%Y')} - {total_live} live, {total_stream} streams"
            
            try:
                contents = repo.get_contents(FILE_PATH)
                repo.update_file(contents.path, commit_msg, json_text, contents.sha)
                print(f"✅ ĐÃ CẬP NHẬT GitHub: {FILE_PATH}!")
            except:
                repo.create_file(FILE_PATH, commit_msg, json_text)
                print(f"✅ ĐÃ TẠO MỚI trên GitHub: {FILE_PATH}!")
                
        except Exception as e:
            print(f"❌ Lỗi GitHub: {e}")
            with open(FILE_PATH, 'w', encoding='utf-8') as f:
                f.write(json_text)
            print(f"   💾 Đã lưu local: {FILE_PATH}")
    else:
        with open(FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(json_text)
        print(f"💾 Đã lưu local: {FILE_PATH}")
    
    print("\n" + "=" * 70)
    print("✅ HOÀN THÀNH!")
    print("=" * 70)

if __name__ == "__main__":
    scrape_and_push()
