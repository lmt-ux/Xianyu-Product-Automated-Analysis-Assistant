# jd_price_fetcher.py
import asyncio
import re
import os
import random
import logging
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, BrowserContext
from dotenv import load_dotenv

load_dotenv()

HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

JD_STORAGE_STATE_FILE = "jd_auth.json"


async def get_authenticated_context(browser, force_relogin: bool = False) -> BrowserContext:
    if force_relogin or not os.path.exists(JD_STORAGE_STATE_FILE):
        logger.info("需要登录京东账号...")
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
        )
        page = await context.new_page()
        await page.goto("https://passport.jd.com/new/login.aspx")
        logger.info("请在弹出的浏览器中登录京东（扫码或账号密码）...")
        try:
            await page.wait_for_selector(".nickname, .user-name, a[href='//order.jd.com/center/list.action']", timeout=120000)
            logger.info("京东登录成功！正在保存状态...")
            await context.storage_state(path=JD_STORAGE_STATE_FILE)
            logger.info("京东登录状态已保存。")
        except Exception as e:
            logger.error(f"登录超时或失败: {e}")
            raise RuntimeError("京东登录失败")
        return context
    else:
        logger.info("使用已保存的京东登录状态。")
        context = await browser.new_context(storage_state=JD_STORAGE_STATE_FILE)
        return context


async def simulate_human_behavior(page):
    """模拟人类行为：滚动、鼠标移动、随机延迟"""
    await page.evaluate(f"window.scrollBy(0, {random.randint(100, 500)})")
    await asyncio.sleep(random.uniform(0.5, 1.5))
    await page.mouse.move(random.randint(100, 800), random.randint(200, 600))
    await asyncio.sleep(random.uniform(0.2, 0.8))


async def get_jd_price_stats(keyword: str, timeout: int = 30000, max_retries: int = 2) -> Optional[Dict[str, Any]]:
    """
    获取京东搜索页价格统计，增强反风控处理
    """
    for attempt in range(max_retries + 1):
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=HEADLESS,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-features=IsolateOrigins,site-per-process',
                        '--disable-web-security',
                        '--disable-features=BlockInsecurePrivateNetworkRequests',
                        '--no-sandbox',
                        '--disable-dev-shm-usage'
                    ]
                )
                context = await get_authenticated_context(browser)
                page = await context.new_page()

                # 隐藏自动化特征
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = { runtime: {} };
                    // 伪造 plugins
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                    // 伪造 languages
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
                """)

                await page.set_extra_http_headers({
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
                    'Sec-Ch-Ua-Mobile': '?0',
                    'Sec-Ch-Ua-Platform': '"Windows"',
                })

                search_url = f"https://search.jd.com/Search?keyword={keyword}&enc=utf-8"
                logger.info(f"正在京东搜索: {keyword} (尝试 {attempt+1}/{max_retries+1})")
                await page.goto(search_url, timeout=timeout, wait_until="domcontentloaded")
                await simulate_human_behavior(page)

                # 检测并处理验证页面（常见验证码或滑块）
                if await page.is_visible("text=验证") or await page.is_visible("text=滑动"):
                    logger.warning("检测到验证页面，尝试等待手动处理（15秒）...")
                    await asyncio.sleep(15)  # 给手动处理机会
                    # 刷新重试
                    await page.reload()
                    await asyncio.sleep(3)

                # 等待商品列表，多种选择器
                selectors = [".gl-item", "#J_goodsList .gl-item", ".J_goodsList .gl-item", ".goods-list .gl-item"]
                found = False
                for sel in selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=8000)
                        found = True
                        break
                    except:
                        continue
                if not found:
                    logger.warning(f"京东页面未找到商品列表，关键词: {keyword}")
                    # 调试截图
                    debug_dir = "jd_debug"
                    os.makedirs(debug_dir, exist_ok=True)
                    await page.screenshot(path=f"{debug_dir}/{keyword}_fail_{attempt}.png")
                    logger.info(f"截图已保存至 {debug_dir}/{keyword}_fail_{attempt}.png")
                    if attempt < max_retries:
                        wait_time = random.uniform(8, 12)
                        logger.info(f"等待 {wait_time:.1f} 秒后重试...")
                        await asyncio.sleep(wait_time)
                        continue
                    return None

                # 提取价格
                price_elements = await page.query_selector_all(".gl-item .p-price i, #J_goodsList .gl-item .p-price i, .p-price i")
                prices = []
                for elem in price_elements:
                    price_text = await elem.inner_text()
                    match = re.search(r'(\d+\.?\d*)', price_text)
                    if match:
                        try:
                            price = float(match.group(1))
                            prices.append(price)
                        except:
                            pass
                if not prices:
                    logger.warning(f"未提取到价格数据，关键词: {keyword}")
                    if attempt < max_retries:
                        await asyncio.sleep(10)
                        continue
                    return None

                result = {
                    "min_price": min(prices),
                    "max_price": max(prices),
                    "avg_price": round(sum(prices) / len(prices), 2),
                    "sample_count": len(prices),
                    "price_list": prices[:200]
                }
                logger.info(f"京东 {keyword} - 价格区间: {result['min_price']}~{result['max_price']}, 均价: {result['avg_price']}, 样本数: {result['sample_count']}")
                return result

        except Exception as e:
            logger.error(f"京东搜索异常 [{keyword}]: {e}")
            if attempt < max_retries:
                wait_time = random.uniform(8, 12)
                logger.info(f"等待 {wait_time:.1f} 秒后重试...")
                await asyncio.sleep(wait_time)
                continue
            return None

    return None


async def main():
    result = await get_jd_price_stats("手机")
    print(result)

if __name__ == "__main__":
    asyncio.run(main())