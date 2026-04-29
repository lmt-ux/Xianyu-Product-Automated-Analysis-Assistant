# deepseek_client.py
import json
import os
from openai import AsyncOpenAI
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError("请在 .env 文件中设置 DEEPSEEK_API_KEY")

DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL
)

async def analyze_single_product(product: Dict[str, Any], market_info: str) -> Dict[str, Any]:
    """
    分析单个商品，市场信息由外部传入（如京东价格区间）
    market_info: 例如 "京东平台同款价格区间 1899~2599元，均价2199元（采样15件）"
    """
    prompt = f"""
你是闲鱼倒卖评估专家。判断以下商品是否值得低价买进、高价卖出（目标毛利 ≥20%）。
商品信息：
- 标题：{product.get('商品标题', '未知')}
- 闲鱼价格（买入价）：{product.get('当前售价', '未知')}
- 市场参考价：{market_info}
- 地区：{product.get('发货地区', '未知')}
- 卖家：{product.get('卖家昵称', '未知')}

杂费估算：
- 平台手续费：成交价的 5%
- 运费：20 元（假设包邮转卖）
实际利润 = 建议出手价 - 买入价 - (建议出手价 × 5% + 20)
毛利率 = (实际利润 / (买入价 + 杂费)) × 100%

请基于市场参考价与闲鱼价的差距，给出倒卖建议。只输出 JSON，不要有其他文字：
{{
  "评分": 1-10的整数,
  "建议": "强烈推荐/可以收/保持观望/切勿碰",
  "理由": "简短理由（最多50字）",
  "预期利润率": "预估毛利率，例如 35%",
  "建议售价": "建议出手价（整数，单位元）",
  "预估利润": "预估到手利润（整数，单位元）"
}}
"""
    try:
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return {
            "评分": result.get("评分", 5),
            "建议": result.get("建议", "保持观望"),
            "理由": result.get("理由", "无明确理由"),
            "预期利润率": result.get("预期利润率", "未知"),
            "建议售价": result.get("建议售价", "未知"),
            "预估利润": result.get("预估利润", "未知")
        }
    except Exception as e:
        print(f"DeepSeek分析失败: {e}")
        return {
            "评分": 0,
            "建议": "分析失败",
            "理由": f"API错误: {str(e)[:30]}",
            "预期利润率": "N/A",
            "建议售价": "N/A",
            "预估利润": "N/A"
        }

async def batch_analyze(products: List[Dict[str, Any]], market_info: str, max_concurrent: int = 3) -> List[Dict[str, Any]]:
    """
    批量分析商品
    :param products: 商品列表
    :param market_info: 市场参考价格信息（字符串），所有商品共用
    :param max_concurrent: 最大并发数
    """
    import asyncio
    semaphore = asyncio.Semaphore(max_concurrent)
    async def analyze_with_semaphore(product):
        async with semaphore:
            analysis = await analyze_single_product(product, market_info)
            return {**product, **analysis}
    tasks = [analyze_with_semaphore(p) for p in products]
    return await asyncio.gather(*tasks)