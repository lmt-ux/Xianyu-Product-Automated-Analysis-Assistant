# main.py
import hashlib
import os
import random
from datetime import datetime
import pandas as pd
from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright
from tortoise import Model, fields
from tortoise.contrib.fastapi import register_tortoise
from dotenv import load_dotenv
import asyncio
import glob
from price_fetcher import get_market_price_stats, calc_price_stats
from deepseek_client import batch_analyze

load_dotenv()

PLAYWRIGHT_BROWSERS_PATH = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
if PLAYWRIGHT_BROWSERS_PATH:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PLAYWRIGHT_BROWSERS_PATH

HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
INTERVAL_MIN = float(os.getenv("CRAWL_INTERVAL_MIN", "1"))
INTERVAL_MAX = float(os.getenv("CRAWL_INTERVAL_MAX", "3"))

EXCEL_ROOT = os.getenv("EXCEL_ROOT", "exports")
app = FastAPI(title="闲鱼商品搜索API", description="支持登录态持久化、Excel导出和AI倒卖分析")

# ========== 辅助函数 ==========
def get_md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def get_link_unique_key(link: str) -> str:
    parts = link.split('&', 1)
    return '&'.join(parts[:1]) if len(parts) >= 2 else link

# ========== 数据库模型 ==========
class XianyuProduct(Model):
    id = fields.IntField(pk=True)
    title = fields.TextField()
    price = fields.CharField(max_length=50)
    area = fields.CharField(max_length=100)
    seller = fields.CharField(max_length=100)
    link = fields.TextField(column_type="MEDIUMTEXT")
    link_hash = fields.CharField(max_length=32, unique=True)
    image_url = fields.TextField(column_type="MEDIUMTEXT")
    publish_time = fields.DatetimeField(null=True)

    class Meta:
        table = "xianyu_products"

# ========== 数据库配置 ==========
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("请设置环境变量 DATABASE_URL")
DATABASE_CONFIG = {
    "connections": {"default": DATABASE_URL},
    "apps": {"models": {"models": ["__main__"], "default_connection": "default"}}
}
register_tortoise(app, config=DATABASE_CONFIG, generate_schemas=True, add_exception_handlers=True)

# ========== 数据解析与存储 ==========
async def safe_get(data, *keys, default="暂无"):
    for key in keys:
        try:
            data = data[key]
        except (KeyError, TypeError, IndexError):
            return default
    return data

async def save_to_db(data_list):
    new_records = 0
    new_ids = []
    for item in data_list:
        try:
            link = item["商品链接"]
            unique_part = get_link_unique_key(link)
            link_hash = get_md5(unique_part)
            product, created = await XianyuProduct.get_or_create(
                link_hash=link_hash,
                defaults={
                    "title": item["商品标题"],
                    "price": item["当前售价"],
                    "area": item["发货地区"],
                    "seller": item["卖家昵称"],
                    "link": link,
                    "image_url": item["商品图片链接"],
                    "publish_time": datetime.strptime(item["发布时间"], "%Y-%m-%d %H:%M")
                    if item["发布时间"] != "未知时间" else None,
                }
            )
            if created:
                new_records += 1
                new_ids.append(product.id)
        except Exception as e:
            print(f"DB保存出错: {e}")
    return new_records, new_ids

def save_to_excel_with_analysis(data_list, keyword):
    if not data_list:
        return None
    df = pd.DataFrame(data_list)
    df["关键词"] = keyword
    df["爬取时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    required_cols = ["评分", "建议", "理由", "预期利润率",
                     "建议售价", "预估利润"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""

    order = ["商品标题", "当前售价", "建议售价", "预估利润",
             "预期利润率", "评分", "建议", "理由",
             "商品成色", "商品标签", "业务类型",
             "发货地区", "卖家昵称", "商品链接", "商品ID", "发布时间", "关键词", "爬取时间"]
    order = [col for col in order if col in df.columns]
    df = df[order]

    base_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    today = datetime.now().strftime("%Y-%m-%d")
    subdir = os.path.join(EXCEL_ROOT, today)
    os.makedirs(subdir, exist_ok=True)
    pattern = os.path.join(subdir, f"{base_name}_*.xlsx")
    existing_files = glob.glob(pattern)
    if existing_files:
        seq_list = []
        for f in existing_files:
            try:
                seq = int(os.path.basename(f).split('_')[-1].split('.')[0])
                seq_list.append(seq)
            except:
                pass
        next_seq = max(seq_list) + 1 if seq_list else 1
    else:
        next_seq = 1
    file_path = os.path.join(subdir, f"{base_name}_{next_seq}.xlsx")
    df.to_excel(file_path, sheet_name="闲鱼倒卖分析", index=False)
    print(f"已导出 {len(df)} 条数据（含建议出手价&利润）到 {file_path}")
    return file_path

# ========== 登录态管理 ==========
STORAGE_STATE_FILE = "taobao_auth.json"

async def get_authenticated_context(browser):
    viewport = {'width': 1920, 'height': 1080}
    if os.path.exists(STORAGE_STATE_FILE):
        print("检测到已保存的登录状态，直接加载...")
        context = await browser.new_context(storage_state=STORAGE_STATE_FILE, viewport=viewport)
        return context
    else:
        print("未找到登录状态，需要扫码登录...")
        context = await browser.new_context(viewport=viewport)
        page = await context.new_page()
        await page.goto("https://www.goofish.com")
        print("请在弹出的浏览器中扫码登录...")
        try:
            await page.wait_for_selector(".user-avatar, .login-user-info, [class*='user']", timeout=120000)
            print("登录成功！正在保存状态...")
            await context.storage_state(path=STORAGE_STATE_FILE)
            print("登录状态已保存")
        except Exception as e:
            print("等待登录超时", e)
            raise RuntimeError("登录失败")
        return context

# ========== 核心爬虫（闲鱼） ==========
async def scrape_xianyu(keyword: str, max_pages: int = 1):
    print(f"--- 开始爬取，关键词={keyword}, 最大页数={max_pages} ---")
    data_list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ]
        )
        context = await get_authenticated_context(browser)
        page = await context.new_page()

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
        """)

        async def random_delay():
            await asyncio.sleep(random.uniform(INTERVAL_MIN, INTERVAL_MAX))

        async def human_like_move():
            await page.mouse.move(
                random.randint(200, 1000),
                random.randint(100, 700)
            )
            await asyncio.sleep(random.uniform(0.2, 0.6))
            await page.evaluate(f"window.scrollBy(0, {random.randint(80, 400)})")
            await asyncio.sleep(random.uniform(0.2, 0.5))

        async def on_response(response):
            if "h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search" in response.url:
                try:
                    result_json = await response.json()
                    items = result_json.get("data", {}).get("resultList", [])
                    for item in items:
                        main_data = await safe_get(item, "data", "item", "main", "exContent", default={})
                        click_params = await safe_get(item, "data", "item", "main", "clickParam", "args", default={})
                        title = await safe_get(main_data, "title", default="未知标题")
                        price_parts = await safe_get(main_data, "price", default=[])
                        price = "价格异常"
                        if isinstance(price_parts, list):
                            price = "".join([str(p.get("text", "")) for p in price_parts if isinstance(p, dict)])
                            price = price.replace("当前价", "").strip()
                            if "万" in price:
                                price = f"¥{float(price.replace('¥', '').replace('万', '')) * 10000:.0f}"
                        area = await safe_get(main_data, "area", default="地区未知")
                        seller = await safe_get(main_data, "userNickName", default="匿名卖家")
                        raw_link = await safe_get(item, "data", "item", "main", "targetUrl", default="")
                        image_url = await safe_get(main_data, "picUrl", default="")
                        pub_time = click_params.get("publishTime", "")
                        publish_time_str = "未知时间"
                        if pub_time and pub_time.isdigit():
                            publish_time_str = datetime.fromtimestamp(int(pub_time)/1000).strftime("%Y-%m-%d %H:%M")

                        # ---- 额外详情字段 ----
                        item_main = await safe_get(item, "data", "item", "main", default={})
                        tags = await safe_get(main_data, "tags", default=None)
                        tag_list = []
                        if isinstance(tags, list):
                            for t in tags:
                                if isinstance(t, dict):
                                    tag_list.append(t.get("text", ""))
                                elif isinstance(t, str):
                                    tag_list.append(t)
                        elif isinstance(tags, str):
                            tag_list = [tags]

                        quality = await safe_get(main_data, "quality", default="")
                        if not quality:
                            quality = await safe_get(main_data, "itemStatus", default="")
                        biz_type = await safe_get(main_data, "bizType", default="")
                        want_count_raw = await safe_get(click_params, "wantCount", default="")
                        item_id = await safe_get(click_params, "itemId", default="")
                        if not item_id:
                            item_id = await safe_get(main_data, "itemId", default="")

                        data_list.append({
                            "商品标题": title,
                            "当前售价": price,
                            "发货地区": area,
                            "卖家昵称": seller,
                            "商品链接": raw_link.replace("fleamarket://", "https://www.goofish.com/"),
                            "商品图片链接": f"https:{image_url}" if image_url and not image_url.startswith("http") else image_url,
                            "发布时间": publish_time_str,
                            "商品ID": item_id,
                            "商品标签": "|".join(tag_list) if tag_list else "",
                            "商品成色": quality,
                            "业务类型": biz_type,
                        })
                except Exception as e:
                    print(f"解析响应异常: {e}")

        page.on("response", on_response)

        try:
            await page.goto("https://www.goofish.com", timeout=30000)
            await random_delay()

            await human_like_move()
            await page.fill('input[class*="search-input"]', keyword)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            async with page.expect_response(lambda r: "h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search" in r.url, timeout=15000) as resp_info:
                await page.click('button[type="submit"]')
            await resp_info.value
            await random_delay()

            try:
                await page.click('text=新发布')
                await page.click('text=最新')
                await random_delay()
            except Exception:
                print("排序失败，可能页面结构变化")

            current_page = 1
            while current_page < max_pages:
                print(f"翻页到第 {current_page+1} 页...")
                next_btn = await page.query_selector("[class*='search-pagination-arrow-right']:not([disabled])")
                if not next_btn:
                    break
                await human_like_move()
                await next_btn.click()
                await random_delay()
                current_page += 1

            print(f"爬取完成，共获取 {len(data_list)} 条数据")
        finally:
            await browser.close()

    return data_list


# ========== 闲鱼数据统计（按型号聚类 + IQR） ==========
def extract_model(title: str, keyword: str) -> str:
    """从标题中提取具体型号，eg: '小米14 12+256G 白色' → '小米14'"""
    kw = keyword.lower()
    title_lower = title.lower()
    if kw not in title_lower:
        return "其他"
    after_kw = title_lower[title_lower.index(kw) + len(kw):].strip()
    variant = ""
    for word in after_kw.split():
        if any(c.isalpha() or c.isdigit() for c in word):
            if word in ("白色", "黑色", "蓝色", "绿色", "紫色", "金色", "银色", "灰色",
                        "红色", "橙色", "粉色", "颜色", "国行", "港版", "美版", "日版",
                        "公开版", "全网通", "移动版", "联通版", "电信版", "未激活", "在保",
                        "过保", "无锁", "有锁", "卡贴机", "官换机", "资源机"):
                continue
            variant = word
            break
    if variant:
        return f"{keyword} {variant}"
    return keyword


def calc_xianyu_stats(data_list: list, keyword: str) -> dict:
    """按型号聚类闲鱼数据，输出每个型号和整体的 IQR 统计"""
    from collections import defaultdict
    groups = defaultdict(list)
    for item in data_list:
        price_str = item.get("当前售价", "").replace("¥", "")
        try:
            price = float(price_str)
        except (ValueError, TypeError):
            continue
        model = extract_model(item.get("商品标题", ""), keyword)
        groups[model].append(price)

    all_prices = []
    model_lines = []
    for model_name in sorted(groups.keys()):
        prices = groups[model_name]
        all_prices.extend(prices)
        if len(prices) >= 2:
            median = sorted(prices)[len(prices) // 2]
            model_lines.append(f"  - {model_name}: {len(prices)}件, 中位数¥{median}")
        else:
            model_lines.append(f"  - {model_name}: {len(prices)}件, ¥{prices[0]}")

    stats = calc_price_stats(all_prices) if all_prices else None
    return {
        "stats": stats,
        "model_breakdown": model_lines,
    }


# ========== API 接口 ==========
@app.post("/search/", summary="商品搜索接口（可选AI倒卖分析）")
async def search_items(keyword: str, max_pages: int = 1, enable_ai: bool = True):
    errors = []
    data_list = []
    xianyu_stats = {}
    smzdm_stats = {}
    market_info = "暂无可靠市场参考价"
    analyzed_data = []
    new_count = 0
    new_ids = []
    excel_file = ""
    high_value_items = []
    ai_enabled = False

    # 1. 闲鱼爬取（必须成功）
    try:
        data_list = await scrape_xianyu(keyword, max_pages)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"闲鱼爬取失败: {str(e)}")

    if not data_list:
        return {"status": "no_data", "keyword": keyword, "message": "未获取到任何商品"}

    # 2. 闲鱼自身统计
    try:
        xianyu_stats = calc_xianyu_stats(data_list, keyword)
    except Exception as e:
        errors.append(f"闲鱼统计失败: {str(e)[:100]}")

    # 3. 什么值得买比价
    try:
        smzdm_stats = await get_market_price_stats(keyword)
    except Exception as e:
        errors.append(f"什么值得买比价失败: {str(e)[:100]}")

    # 4. 构造市场信息
    try:
        parts = []
        if xianyu_stats.get("stats"):
            s = xianyu_stats["stats"]
            parts.append(
                f"闲鱼同款参考: 均价¥{s['avg_price']}, "
                f"中位数¥{s['median_price']}, "
                f"核心成交区间¥{s['iqr_min']}~¥{s['iqr_max']} "
                f"(采样{s['sample_count']}件, 剔除{s['removed_by_iqr']}件极端值)"
            )
            parts.append("型号分布:\n" + "\n".join(xianyu_stats.get("model_breakdown", [])))

        if smzdm_stats and smzdm_stats.get('sample_count', 0) > 0:
            s = smzdm_stats
            note = s.get('note', '')
            note_suffix = f"（{note}）" if note else ""
            parts.append(
                f"什么值得买参考: 均价¥{s['avg_price']}, "
                f"中位数¥{s['median_price']}, "
                f"核心成交区间¥{s['iqr_min']}~¥{s['iqr_max']} "
                f"(采样{s['sample_count']}件){note_suffix}"
            )
        if parts:
            market_info = "\n".join(parts)
    except Exception as e:
        errors.append(f"构造市场信息失败: {str(e)[:100]}")

    print(f"市场参考信息:\n{market_info}")

    # 5. AI 分析
    if enable_ai:
        try:
            print("正在调用 DeepSeek AI 分析商品倒卖价值...")
            analyzed_data = await batch_analyze(data_list, market_info=market_info, max_concurrent=3)
            ai_enabled = True
        except Exception as e:
            errors.append(f"AI分析失败(已回退为爬虫数据): {str(e)[:100]}")
            analyzed_data = data_list
    else:
        analyzed_data = data_list

    # 6. 保存到数据库
    try:
        new_count, new_ids = await save_to_db(analyzed_data)
    except Exception as e:
        errors.append(f"数据库保存失败: {str(e)[:100]}")

    # 7. 导出 Excel
    try:
        excel_file = save_to_excel_with_analysis(analyzed_data, keyword) or ""
    except Exception as e:
        errors.append(f"Excel导出失败: {str(e)[:100]}")

    # 8. 提取高利润率商品
    try:
        if ai_enabled:
            for item in analyzed_data:
                margin_str = item.get("预期利润率", "0%")
                try:
                    margin_value = float(margin_str.strip('%'))
                except:
                    margin_value = 0
                if margin_value > 30:
                    high_value_items.append({
                        "title": item.get("商品标题"),
                        "price": item.get("当前售价"),
                        "建议售价": item.get("建议售价"),
                        "预估利润": item.get("预估利润"),
                        "预期利润率": margin_str,
                        "评分": item.get("评分"),
                        "建议": item.get("建议"),
                        "理由": item.get("理由")
                    })
    except Exception as e:
        errors.append(f"提取高利润率商品失败: {str(e)[:100]}")

    final_status = "partial_success" if errors else "success"
    response = {
        "status": final_status,
        "keyword": keyword,
        "total_results": len(data_list),
        "new_records": new_count,
        "new_record_ids": new_ids,
        "excel_file": excel_file,
        "ai_enabled": ai_enabled,
        "high_value_items": high_value_items,
        "xianyu_stats": xianyu_stats.get("stats"),
        "smzdm_stats": smzdm_stats,
    }
    if errors:
        response["errors"] = errors
    return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)