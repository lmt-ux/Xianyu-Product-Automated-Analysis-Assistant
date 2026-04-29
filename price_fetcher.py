# price_fetcher.py
import asyncio
import re
import random
import logging
import os
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
]

async def random_sleep(min_sec: float = 0.5, max_sec: float = 2.0):
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def simulate_human_behavior(page):
    scroll_y = random.randint(100, 800)
    await page.evaluate(f"window.scrollBy(0, {scroll_y})")
    await random_sleep(0.3, 0.8)
    await page.mouse.move(random.randint(100, 1200), random.randint(200, 800))
    await random_sleep(0.2, 0.5)

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
                            price = float(match.group(1))
                            if 1 <= price <= 100000:
                                prices.append(price)

                if len(prices) < 3:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await random_sleep(2, 3)
                    more = await page.query_selector_all(".z-highlight, .price")
                    for elem in more:
                        text = await elem.inner_text()
                        match = re.search(r'(\d+(?:\.\d+)?)', text)
                        if match:
                            price = float(match.group(1))
                            if 1 <= price <= 100000:
                                prices.append(price)

                if not prices:
                    logger.warning(f"未提取到价格数据，关键词: {keyword}")
                    if attempt < max_retries:
                        await asyncio.sleep(random.uniform(8, 12))
                        continue
                    return None

                prices = sorted(set(prices))
                result = {
                    "min_price": min(prices),
                    "max_price": max(prices),
                    "avg_price": round(sum(prices) / len(prices), 2),
                    "sample_count": len(prices),
                    "price_list": prices[:200],
                    "source": "smzdm"
                }
                logger.info(f"什么值得买 {keyword} - 价格区间: {result['min_price']}~{result['max_price']}, 均价: {result['avg_price']}, 样本数: {result['sample_count']}")
                return result

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