# price_fetcher.py
import asyncio
import re
import random
import logging
import os
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright
from dotenv import load_dotenv
from deepseek_client import filter_relevant_items

load_dotenv()

HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
]

EXCLUDE_KEYWORDS = [
    "壳", "膜", "套", "充电", "数据线", "贴膜", "保护壳", "钢化膜",
    "充电器", "充电头", "充电宝", "支架", "挂绳", "指环", "背夹",
    "防摔", "防水壳", "镜头膜", "耳机套", "屏幕膜", "保护套",
    "卡槽", "卡贴", "转接头", "扩展坞", "分线器", "集线器",
    "线缆", "充电线", "磁吸", "手机挂件", "手机链",
]

async def random_sleep(min_sec: float = 0.5, max_sec: float = 2.0):
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def simulate_human_behavior(page):
    scroll_y = random.randint(100, 800)
    await page.evaluate(f"window.scrollBy(0, {scroll_y})")
    await random_sleep(0.3, 0.8)
    await page.mouse.move(random.randint(100, 1200), random.randint(200, 800))
    await random_sleep(0.2, 0.5)

def keyword_prefilter(items: list) -> list:
    filtered = []
    removed = 0
    for item in items:
        title = item.get("title", "")
        is_excluded = any(kw in title for kw in EXCLUDE_KEYWORDS)
        if is_excluded:
            removed += 1
        else:
            filtered.append(item)
    if removed > 0:
        print(f"关键字过滤: 排除 {removed} 件明显不相关的商品")
    return filtered


def calc_price_stats(prices: list) -> dict:
    """
    传入价格列表，返回包含 IQR 中位数等统计信息的字典
    prices: [123.0, 456.0, ...]
    """
    if not prices:
        return None
    prices = sorted(prices)
    n = len(prices)
    avg = round(sum(prices) / n, 2)
    median = prices[n // 2] if n % 2 else round((prices[n // 2 - 1] + prices[n // 2]) / 2, 2)
    q1_idx = n // 4
    q3_idx = 3 * n // 4
    iqr_prices = prices[q1_idx:q3_idx] if q3_idx > q1_idx else prices
    return {
        "min_price": prices[0],
        "max_price": prices[-1],
        "avg_price": avg,
        "median_price": median,
        "iqr_min": iqr_prices[0],
        "iqr_max": iqr_prices[-1],
        "iqr_avg": round(sum(iqr_prices) / len(iqr_prices), 2),
        "sample_count": n,
        "iqr_sample_count": len(iqr_prices),
        "removed_by_iqr": n - len(iqr_prices),
        "price_list": prices[:200],
    }

async def get_smzdm_price_stats(keyword: str, timeout: int = 30000, max_retries: int = 2) -> Optional[Dict[str, Any]]:
    print(f"\n--- 开始什么值得买价格采集: {keyword} ---")
    for attempt in range(max_retries + 1):
        browser = None
        context = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=HEADLESS,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                    ]
                )
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=random.choice(USER_AGENTS)
                )
                page = await context.new_page()

                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
                """)

                search_url = f"https://search.smzdm.com/?c=home&s={keyword}"
                logger.info(f"正在什么值得买搜索: {keyword} (尝试 {attempt+1}/{max_retries+1})")
                await page.goto(search_url, timeout=timeout, wait_until="domcontentloaded")
                await random_sleep(2, 4)

                try:
                    await page.wait_for_selector(".feed-row-wide, .feed-list li", timeout=10000)
                except:
                    logger.warning("未找到商品列表选择器")
                    os.makedirs("smzdm_debug", exist_ok=True)
                    await page.screenshot(path=f"smzdm_debug/{keyword}_fail_{attempt}.png")

                await simulate_human_behavior(page)

                # ——— 第一步：提取结构化商品（标题+价格配对） ———
                feed_items = await page.query_selector_all(".feed-row-wide")
                raw_items = []
                for fi in feed_items:
                    title_el = await fi.query_selector("h5.feed-block-title a, .feed-block-title")
                    if not title_el:
                        continue
                    title = (await title_el.inner_text()).strip()
                    price_el = await fi.query_selector(".z-highlight, .price, .feed-price, .list-price, .buy-price")
                    if not price_el:
                        continue
                    price_text = await price_el.inner_text()
                    match = re.search(r'(\d+(?:\.\d+)?)', price_text)
                    if not match:
                        continue
                    price = float(match.group(1))
                    if price < 1 or price > 100000:
                        continue
                    raw_items.append({"title": title, "price": price})

                # ——— 第二步：关键字预过滤 ———
                filtered_items = keyword_prefilter(raw_items)

                # ——— 第三步：AI 过滤（排除配件等不相关商品） ———
                if filtered_items:
                    filtered_items = await filter_relevant_items(keyword, filtered_items)

                # ——— 第四步：如果结构化提取失败，降级为旧逻辑 ———
                if not filtered_items:
                    if not raw_items:
                        logger.warning(f"结构化提取失败，降级到通用价格提取")
                        price_selectors = [
                            ".z-highlight", ".price", ".feed-price", ".list-price",
                            ".buy-price", ".feed-ext .price", ".feed-now .price"
                        ]
                        prices = []
                        for sel in price_selectors:
                            elements = await page.query_selector_all(sel)
                            for elem in elements:
                                text = await elem.inner_text()
                                match = re.search(r'(\d+(?:\.\d+)?)', text)
                                if match:
                                    p = float(match.group(1))
                                    if 1 <= p <= 100000:
                                        prices.append(p)
                        if prices:
                            stats = calc_price_stats(prices)
                            if stats:
                                stats["source"] = "smzdm"
                                stats["note"] = "降级模式（未提取到标题），价格可能包含配件"
                                logger.info(f"什么值得买 {keyword} - 价格区间: {stats['min_price']}~{stats['max_price']}, 均价: {stats['avg_price']}, 中位数: {stats['median_price']}, IQR区间: {stats['iqr_min']}~{stats['iqr_max']}, 样本数: {stats['sample_count']}（降级模式）")
                                return stats
                    else:
                        logger.warning("过滤后无数据，恢复全部商品")
                        filtered_items = raw_items

                # ——— 第五步：计算结果 ———
                prices = sorted(set(item["price"] for item in filtered_items))
                stats = calc_price_stats(prices)
                if not stats:
                    logger.warning(f"未提取到价格数据，关键词: {keyword}")
                    if attempt < max_retries:
                        await asyncio.sleep(random.uniform(8, 12))
                        continue
                    return None
                stats["source"] = "smzdm"
                logger.info(f"什么值得买 {keyword} - 价格区间: {stats['min_price']}~{stats['max_price']}, 均价: {stats['avg_price']}, 中位数: {stats['median_price']}, IQR区间: {stats['iqr_min']}~{stats['iqr_max']}, 样本数: {stats['sample_count']}, IQR样本: {stats['iqr_sample_count']}")
                return stats

        except Exception as e:
            logger.error(f"什么值得买搜索异常 [{keyword}]: {e}")
            if attempt < max_retries:
                await asyncio.sleep(random.uniform(8, 12))
                continue
            return None
    return None

async def get_market_price_stats(keyword: str) -> Optional[Dict[str, Any]]:
    return await get_smzdm_price_stats(keyword)

if __name__ == "__main__":
    asyncio.run(get_market_price_stats("手机"))