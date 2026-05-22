import asyncio
import json
import os
import re
import time
import sys
from datetime import datetime
from urllib.parse import urljoin

import requests
from playwright.async_api import async_playwright

# ==================== 配置区 ====================
# 请根据你的目标网站修改以下配置
SAVE_DIR = "downloads"                # 下载根目录
BASE_URL = "https://example.com"      # 漫画网站主域名（用于拼接相对路径和 Referer）
# 初始章节（第一话）完整 URL
INITIAL_URL = "https://example.com/comic/xxx/chapter/1"

# User-Agent，可改为你浏览器的 UA 或保持通用值
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 下载行为控制
CHAPTER_DELAY = 3                     # 章节之间休息（秒）
BATCH_SIZE = 1                        # 每批下载多少话后询问是否继续
MAX_CONCURRENT_DOWNLOADS = 5          # 同一话内同时下载的图片数
PROGRESS_FILE = "progress.json"       # 断点续传进度文件
FAILED_FILE = "failed_downloads.jsonl"     # 单张图片下载失败的记录
SKIPPED_FILE = "skipped_chapters.jsonl"   # 因图片不全而跳过的章节记录

# 重试策略
MAX_PAGE_RETRIES = 3                  # 章节页面加载失败重试次数
MAX_REFRESH_RETRIES = 2               # 图片收集不全时刷新页面重试次数
RETRY_DELAY = 2                       # 重试等待（秒）

# 浏览器启动参数（解决无头模式懒加载失效等问题）
BROWSER_ARGS = [
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-field-trial-config',
    '--disable-hang-monitor',
    '--disable-ipc-flooding-protection',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-features=CalculateNativeWinOcclusion',
    '--disable-features=ThrottleDisplayNoneAndVisibilityHidden',
    '--disable-composited-antialiasing',
]
# =================================================

class ChapterLoadError(Exception):
    """章节页面加载失败（重试耗尽）"""

# ---------- 进度与记录工具 ----------
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    print(f"❌ 进度文件 '{PROGRESS_FILE}' 为空。")
                    print("   请删除该文件后重新运行脚本。")
                    sys.exit(1)
                data = json.loads(content)
            chapter_num = data['next_chapter_num']
            next_url = data['next_url']
            print(f"[进度恢复] 从第 {chapter_num} 话继续")
            return chapter_num, next_url
        except json.JSONDecodeError:
            print(f"❌ 进度文件 '{PROGRESS_FILE}' 格式错误（非有效JSON）。")
            print("   请修正文件内容或删除该文件。")
            print('   正确格式示例：{"next_chapter_num": 1, "next_url": "https://..."}')
            sys.exit(1)
        except KeyError as e:
            print(f"❌ 进度文件 '{PROGRESS_FILE}' 缺少必要字段 {e}。")
            print("   请修正文件内容或删除该文件。")
            print('   正确格式示例：{"next_chapter_num": 1, "next_url": "https://..."}')
            sys.exit(1)
    return 1, INITIAL_URL

def save_progress(chapter_num, url):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump({"next_chapter_num": chapter_num, "next_url": url},
                  f, ensure_ascii=False, indent=2)
    print(f"[进度保存] 下一话为第 {chapter_num} 话")

def write_records(file_path, records):
    """将字典列表按行写入 JSONL 文件"""
    with open(file_path, 'a', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def append_failed(chapter_num, chapter_url, failed_list):
    records = [{
        "chapter": chapter_num,
        "chapter_url": chapter_url,
        "image_number": img_no,
        "image_url": img_url,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    } for img_no, img_url in failed_list]
    write_records(FAILED_FILE, records)

def append_skipped(chapter_num, chapter_url):
    write_records(SKIPPED_FILE, [{
        "chapter": chapter_num,
        "chapter_url": chapter_url,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }])

# ---------- 图片下载 ----------
def download_image_sync(url, filepath, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Referer": BASE_URL},
                timeout=60
            )
            resp.raise_for_status()
            with open(filepath, 'wb') as f:
                f.write(resp.content)
            return True
        except (requests.exceptions.Timeout, Exception):
            if attempt == max_retries:
                return False
            time.sleep(2)
    return False

async def download_image_with_semaphore(sem, url, filepath, idx, img_url):
    async with sem:
        success = await asyncio.to_thread(download_image_sync, url, filepath)
    if success:
        print(f"  已保存: {os.path.basename(filepath)}")
        return None
    else:
        print(f"  下载彻底失败: {img_url}")
        return (idx, img_url)

# ---------- 章节文件检查 ----------
def get_existing_page_indices(chapter_dir, chapter_num):
    existing = set()
    if not os.path.isdir(chapter_dir):
        return existing
    pattern = re.compile(rf"^{chapter_num:03d}\.(\d{{2}})\.jpg$")
    for fname in os.listdir(chapter_dir):
        m = pattern.match(fname)
        if m:
            existing.add(int(m.group(1)))
    return existing

# ---------- 滚动收集图片 ----------
async def scroll_and_collect_urls(page, total_images):
    collected = set()
    ordered_urls = []
    scroll_step = 300
    max_scrolls = 1000
    scroll_count = 0
    no_new_count = 0
    max_no_new = 5

    while len(ordered_urls) < total_images and scroll_count < max_scrolls:
        before = len(ordered_urls)
        imgs = await page.query_selector_all('img.lazyloaded')
        for img in imgs:
            src = await img.get_attribute('src')
            if src and src not in collected:
                collected.add(src)
                ordered_urls.append(src)
        after = len(ordered_urls)

        if after == before:
            no_new_count += 1
        else:
            no_new_count = 0

        if no_new_count >= max_no_new:
            at_bottom = await page.evaluate(
                'window.scrollY + window.innerHeight >= document.body.scrollHeight - 30'
            )
            if at_bottom:
                await page.wait_for_timeout(2000)
                imgs = await page.query_selector_all('img.lazyloaded')
                for img in imgs:
                    src = await img.get_attribute('src')
                    if src and src not in collected:
                        collected.add(src)
                        ordered_urls.append(src)
                break

        await page.evaluate(f'window.scrollBy(0, {scroll_step})')
        await page.wait_for_timeout(500)
        scroll_count += 1

    return ordered_urls

# ---------- 页面与导航辅助 ----------
async def get_next_url(page):
    next_a = await page.query_selector("div.comicContent-next a")
    if next_a:
        href = await next_a.get_attribute("href")
        class_name = await next_a.get_attribute("class")
        if href and not (class_name and "prev-null" in class_name):
            return urljoin(BASE_URL, href)
    return None

async def scroll_bottom_and_get_next(page, collect_next=False):
    if collect_next:
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        await page.wait_for_timeout(500)
        return await get_next_url(page)
    return None

async def load_page_with_retry(browser, chapter_url):
    for attempt in range(1, MAX_PAGE_RETRIES + 1):
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        try:
            await page.goto(chapter_url, wait_until="load", timeout=30000)
            await page.wait_for_selector(".comicCount", state="attached", timeout=10000)
            await page.wait_for_timeout(1500)
            return page, context
        except Exception as e:
            print(f"  页面加载失败 (尝试 {attempt}/{MAX_PAGE_RETRIES}): {e}")
            await page.close()
            await context.close()
            if attempt == MAX_PAGE_RETRIES:
                raise ChapterLoadError(f"章节加载失败: {chapter_url}")
            await asyncio.sleep(RETRY_DELAY)

# ---------- 核心：下载一个章节的图片 ----------
async def download_chapter_images(browser, chapter_url, chapter_number, collect_next=False):
    print(f"\n▶ 正在处理第 {chapter_number} 话: {chapter_url}")
    chapter_dir = os.path.join(SAVE_DIR, f"第{chapter_number:03d}话")
    os.makedirs(chapter_dir, exist_ok=True)

    page, context = await load_page_with_retry(browser, chapter_url)
    failed_pics = []
    skipped = False
    next_url = None

    try:
        comic_count_el = await page.query_selector(".comicCount")
        total_images = int(await comic_count_el.text_content()) if comic_count_el else 0
        print(f"  总图片数: {total_images}")

        if not total_images:
            print("  ⚠ 无法获取总图片数，跳过本章")
            skipped = True
            next_url = await scroll_bottom_and_get_next(page, collect_next)
            return next_url, skipped

        existing = get_existing_page_indices(chapter_dir, chapter_number)
        needed = [i for i in range(1, total_images + 1) if i not in existing]

        if not needed:
            print("  本章所有图片已存在，跳过下载")
            next_url = await scroll_bottom_and_get_next(page, collect_next)
            return next_url, skipped

        print(f"  已有 {len(existing)} 张，还需 {len(needed)} 张\n  正在收集下载网址URL，请耐心等待……")

        ordered_urls = await scroll_and_collect_urls(page, total_images)

        for refresh_try in range(MAX_REFRESH_RETRIES + 1):
            if refresh_try > 0:
                print(f"  第 {refresh_try} 次刷新页面重试...")
                await page.reload(wait_until="load", timeout=30000)
                await page.wait_for_selector(".comicCount", state="attached", timeout=10000)
                await page.wait_for_timeout(1500)
                ordered_urls = await scroll_and_collect_urls(page, total_images)
            if len(ordered_urls) >= total_images:
                break

        if len(ordered_urls) < total_images:
            missing = total_images - len(ordered_urls)
            print(f"  ⚠ 收集不全：{len(ordered_urls)}/{total_images}，缺少 {missing} 张，跳过本章")
            append_skipped(chapter_number, chapter_url)
            skipped = True
            next_url = await scroll_bottom_and_get_next(page, collect_next)
            return next_url, skipped

        print(f"  成功收集全部 {total_images} 张图片 URL")

        page_url_map = {}
        for i, raw_url in enumerate(ordered_urls, start=1):
            if raw_url.startswith('//'):
                abs_url = 'https:' + raw_url
            elif raw_url.startswith('/'):
                abs_url = urljoin(BASE_URL, raw_url)
            else:
                abs_url = raw_url
            if i <= total_images:
                page_url_map[i] = abs_url

        sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        tasks = []
        for pnum in needed:
            if pnum not in page_url_map:
                failed_pics.append((pnum, f"缺少URL(第{pnum}页)"))
                continue
            img_url = page_url_map[pnum]
            filename = f"{chapter_number:03d}.{pnum:02d}.jpg"
            filepath = os.path.join(chapter_dir, filename)
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                print(f"  跳过已存在: {filename}")
                continue
            tasks.append(download_image_with_semaphore(sem, img_url, filepath, pnum, img_url))

        if tasks:
            results = await asyncio.gather(*tasks)
            for r in results:
                if r is not None:
                    failed_pics.append(r)

        if failed_pics:
            append_failed(chapter_number, chapter_url, failed_pics)
            print(f"  ❗ {len(failed_pics)} 张图片下载失败，已记录")
        elif tasks:
            print("  本章所需图片全部下载成功 ✅")

        next_url = await scroll_bottom_and_get_next(page, collect_next)

    except Exception as e:
        print(f"  章节处理出错: {e}")
        raise
    finally:
        await page.close()
        await context.close()

    return next_url, skipped

# ---------- 普通下载流程 ----------
async def normal_download(browser):
    chapter_num, current_url = load_progress()
    while current_url:
        batch_count = 0
        while batch_count < BATCH_SIZE and current_url:
            # 每话开始前先保存进度，防止中途崩溃导致进度丢失
            save_progress(chapter_num, current_url)
            try:
                next_url, skipped = await download_chapter_images(
                    browser, current_url, chapter_num, collect_next=True
                )
            except ChapterLoadError as e:
                print(f"\n❌ {e}")
                print("保存进度并退出。")
                return

            chapter_num += 1
            if next_url is None:
                print("\n✅ 已到达最后一话，任务完成！")
                if os.path.exists(PROGRESS_FILE):
                    os.remove(PROGRESS_FILE)
                return
            current_url = next_url
            batch_count += 1
            await asyncio.sleep(CHAPTER_DELAY)

        if current_url:
            print(f"\n⏸ 已下载完 {BATCH_SIZE} 话，当前至第 {chapter_num - 1} 话。")
            while True:
                choice = input("是否继续下载下一批？(y/n): ").strip().lower()
                if choice == 'y':
                    break
                elif choice == 'n':
                    save_progress(chapter_num, current_url)
                    print("程序已退出，下次运行将从此处继续。")
                    return
                else:
                    print("请输入 y 或 n")

# ---------- 重试失败/跳过 ----------
async def retry_failed_and_skipped(browser):
    if os.path.exists(FAILED_FILE):
        print("\n📥 开始处理下载失败的图片...")
        with open(FAILED_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        records = [json.loads(line) for line in lines if line.strip()]
        if not records:
            print("  无失败记录")
        else:
            sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
            tasks = []
            success_indices = set()

            for idx, rec in enumerate(records):
                chap = rec["chapter"]
                img_no = rec["image_number"]
                img_url = rec["image_url"]
                chapter_dir = os.path.join(SAVE_DIR, f"第{chap:03d}话")
                os.makedirs(chapter_dir, exist_ok=True)
                filepath = os.path.join(chapter_dir, f"{chap:03d}.{img_no:02d}.jpg")
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    success_indices.add(idx)
                    continue
                tasks.append((idx, download_image_with_semaphore(sem, img_url, filepath, img_no, img_url)))

            if tasks:
                results = await asyncio.gather(*[t[1] for t in tasks])
                for (idx, _), res in zip(tasks, results):
                    if res is None:
                        success_indices.add(idx)

            new_records = [rec for i, rec in enumerate(records) if i not in success_indices]
            with open(FAILED_FILE, 'w', encoding='utf-8') as f:
                for rec in new_records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if not new_records:
                os.remove(FAILED_FILE)
                print("  ✅ 所有失败图片已下载成功，已删除失败记录文件")
            else:
                print(f"  ⚠ 仍有 {len(new_records)} 张图片下载失败，记录已更新。")

    if os.path.exists(SKIPPED_FILE):
        print("\n📥 开始处理跳过的章节...")
        with open(SKIPPED_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        skipped_list = [json.loads(line) for line in lines if line.strip()]
        if not skipped_list:
            print("  无跳过记录")
        else:
            os.remove(SKIPPED_FILE)
            for rec in skipped_list:
                chap = rec["chapter"]
                url = rec["chapter_url"]
                try:
                    _, skipped_again = await download_chapter_images(
                        browser, url, chap, collect_next=False
                    )
                    if skipped_again:
                        print(f"  ⊘ 第 {chap} 话再次被跳过，已重新记录。")
                except ChapterLoadError as e:
                    print(f"  ❌ 第 {chap} 话加载失败: {e}，重新记录跳过。")
                    append_skipped(chap, url)

            if os.path.exists(SKIPPED_FILE):
                with open(SKIPPED_FILE, 'r', encoding='utf-8') as f:
                    remaining = f.readlines()
                if remaining:
                    print(f"  ⚠ 仍有 {len(remaining)} 章未成功，详情见 {SKIPPED_FILE}")
                else:
                    os.remove(SKIPPED_FILE)

    print("\n🎉 重试处理完成。")

# ---------- 入口 ----------
async def main():
    has_failed = os.path.exists(FAILED_FILE)
    has_skipped = os.path.exists(SKIPPED_FILE)

    if has_failed or has_skipped:
        print("检测到存在下载失败或跳过的记录：")
        if has_failed:
            print(f"  - {FAILED_FILE}")
        if has_skipped:
            print(f"  - {SKIPPED_FILE}")
        choice = input("是否立即处理这些失败/跳过内容？(y/n): ").strip().lower()
        if choice == 'y':
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
                await retry_failed_and_skipped(browser)
                await browser.close()
            return
        else:
            print("将按照普通流程继续下载。")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        await normal_download(browser)
        if os.path.exists(FAILED_FILE):
            print(f"⚠ 部分图片下载失败，请查看 {FAILED_FILE}")
        if os.path.exists(SKIPPED_FILE):
            print(f"⚠ 部分章节被跳过，请查看 {SKIPPED_FILE}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
