# 闲鱼倒卖分析系统

基于 FastAPI + Playwright + AI 的闲鱼商品搜索与倒卖利润评估系统。

自动爬取闲鱼商品 → 全网比价 → AI 评估倒卖利润 → 导出 Excel。

---

## 功能特性

### 闲鱼爬虫
- 基于 Playwright 真实浏览器，模拟人工操作
- 劫持闲鱼 API 响应直接解析 JSON（比 DOM 解析更稳定）
- 登录态持久化（扫码一次，长期复用）
- 支持翻页爬取、按最新排序
- 反风控增强：隐藏 webdriver 标记、随机操作间隔、鼠标轨迹模拟

### 商品详情
- 商品标题 / 当前售价 / 发货地区 / 卖家昵称
- 商品成色（99新 / 轻微使用痕迹 等）
- 商品标签（验货宝 / 包邮 / 自提 等）
- 商品 ID、图片链接、发布时间

### 市场比价
- 采集什么值得买（smzdm）同款商品价格
- AI 智能过滤不相关商品（如搜手机时自动排除手机壳、充电器）
- 关键字预过滤 + AI 二次过滤，双重保障
- IQR 统计：中位数、核心成交区间（去掉 25% 极端值）

### AI 倒卖分析（DeepSeek）
- 综合考量闲鱼价、市场价、平台手续费（5%）、运费
- 批量并发分析，控制并发数防止 API 限流
- 输出：评分 / 建议 / 理由 / 预期利润率 / 建议售价 / 预估利润
- AI 失败时自动降级，不影响数据导出

### 数据统计
- 中位数替代平均值（抗极端值干扰）
- IQR 方法剔除首尾 25% 异常价格
- 按型号聚类统计（小米14 / 小米14 Pro / 小米14 Ultra 分开统计）
- 型号分布一目了然

### 容错机制
- 部分成功模式：爬虫成功 → 后面环节失败不影响已获取的数据
- 每个步骤独立容错，失败原因记录到 errors 字段
- API 返回 `status: success` 或 `status: partial_success`

### 数据导出
- MySQL 去重存储（链接 MD5 唯一约束）
- Excel 自动导出，按日期建子目录自动编号
- 高利润率商品（>30%）单独标记
- Excel 包含 AI 分析和详情字段

---

## 技术栈

| 组件 | 用途 |
|------|------|
| FastAPI | RESTful API 框架 |
| Uvicorn | ASGI 服务器 |
| Playwright | Chromium 浏览器自动化 |
| Tortoise-ORM | 异步数据库 ORM |
| MySQL | 数据持久化存储 |
| Pandas + openpyxl | Excel 导出 |
| DeepSeek | AI 倒卖分析 |
| python-dotenv | 环境变量管理 |

---

## 项目结构

```
xianyu_spider/
├── spider.py              # 主程序：FastAPI + 闲鱼爬虫 + Excel + 数据库
├── deepseek_client.py     # DeepSeek AI 分析模块 + AI 商品过滤
├── price_fetcher.py       # 什么值得买价格采集 + IQR 统计
├── jd_price_fetcher.py    # 京东价格采集（已实现，可接入）
├── test.py                # 命令行交互版爬虫（不含 AI）
├── test_browser.py        # Playwright 浏览器环境检测
├── requirements.txt       # Python 依赖
├── .env                   # 环境变量配置（敏感信息，不上传 Git）
├── .gitignore             # Git 忽略规则
├── taobao_auth.json       # 闲鱼登录态缓存
├── exports/               # Excel 导出目录
├── smzdm_debug/           # 什么值得买调试截图
└── jd_debug/              # 京东调试截图
```

---

## 快速开始

### 1. 环境要求

```
Python >= 3.8
MySQL
Chromium 浏览器
```

### 2. 安装依赖

```bash
cd xianyu_spider
pip install -r requirements.txt
playwright install chromium
```

### 3. 配置环境变量

创建 `.env` 文件：

```env
DATABASE_URL=mysql://root:密码@localhost/xianyu
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
PLAYWRIGHT_BROWSERS_PATH=D:/my-playwright-browsers
BROWSER_HEADLESS=false
CRAWL_INTERVAL_MIN=1
CRAWL_INTERVAL_MAX=3
DEEPSEEK_MODEL=deepseek-v4-flash
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `DATABASE_URL` | MySQL 连接串 | 必填 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | 必填 |
| `PLAYWRIGHT_BROWSERS_PATH` | 浏览器路径 | — |
| `BROWSER_HEADLESS` | 无头模式 | `false` |
| `CRAWL_INTERVAL_MIN` | 操作间隔下限(秒) | `1` |
| `CRAWL_INTERVAL_MAX` | 操作间隔上限(秒) | `3` |
| `DEEPSEEK_MODEL` | AI 模型 | `deepseek-v4-flash` |
| `DEEPSEEK_BASE_URL` | API 地址（可选） | `https://api.deepseek.com/v1` |

### 4. 创建 MySQL 数据库

```sql
CREATE DATABASE xianyu DEFAULT CHARACTER SET utf8mb4;
```

Tortoise-ORM 会自动建表。

### 5. 启动服务

```bash
python spider.py
```

成功启动后：
```
INFO:     Started server process [43952]
INFO:     Tortoise-ORM started
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 6. 首次使用（扫码登录）

首次启动后，浏览器会弹出闲鱼登录页面。用手机闲鱼扫码登录后，登录态会保存到 `taobao_auth.json`，后续免重复登录。

---

## API 文档

### 访问 Swagger 文档

```
http://localhost:8000/docs
```

### POST /search/

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `keyword` | string | **必填** | 搜索关键词 |
| `max_pages` | int | `1` | 最大爬取页数 |
| `enable_ai` | bool | `true` | 是否启用 AI 分析 |

#### cURL 示例

```bash
# 基础搜索 + AI 分析
curl -X POST "http://localhost:8000/search/?keyword=小米14&max_pages=1"

# 只爬取数据，不用 AI（更快）
curl -X POST "http://localhost:8000/search/?keyword=小米14&max_pages=1&enable_ai=false"

# 多页爬取
curl -X POST "http://localhost:8000/search/?keyword=iPhone15&max_pages=3"
```

#### 响应示例

```json
{
  "status": "success",
  "keyword": "小米14",
  "total_results": 30,
  "new_records": 12,
  "new_record_ids": [101, 102, 103],
  "excel_file": "exports/2026-04-29/20260429_151234_1.xlsx",
  "ai_enabled": true,
  "high_value_items": [
    {
      "title": "小米14 12+256G 白色",
      "price": "¥2500",
      "建议售价": 3299,
      "预估利润": 650,
      "预期利润率": "32%",
      "评分": 8,
      "建议": "可以收",
      "理由": "闲鱼价低于市场均价，利润空间良好"
    }
  ],
  "xianyu_stats": {
    "avg_price": 2949.0,
    "median_price": 2499.0,
    "iqr_min": 2099.0,
    "iqr_max": 3299.0,
    "sample_count": 28,
    "removed_by_iqr": 7
  },
  "smzdm_stats": {
    "avg_price": 2488.0,
    "median_price": 2299.0,
    "sample_count": 45,
    "source": "smzdm"
  }
}
```

#### 部分成功示例

当某些环节出错时（如 AI 超时、数据库连不上），HTTP 仍然返回 200，`status` 变为 `partial_success`：

```json
{
  "status": "partial_success",
  "keyword": "小米14",
  "total_results": 30,
  "excel_file": "exports/2026-04-29/20260429_155000_1.xlsx",
  "errors": [
    "AI分析失败(已回退为爬虫数据): Connection timeout",
    "数据库保存失败: Lost connection to MySQL"
  ]
}
```

---

## 工作流程

```
用户请求 API (POST /search/)
    │
    ▼
┌────────────────────┐
│ 1. 闲鱼爬虫         │  必须成功，失败则返回 500
│   Playwright 浏览器  │
│   劫持 API 响应      │
│   解析 30 条商品      │
│   └─ 标题/价格/成色   │
│   └─ 标签/地区/卖家   │
└───────┬────────────┘
        │
        ▼
┌────────────────────┐
│ 2. 闲鱼自身统计      │  失败→继续
│   按型号聚类          │
│   IQR 去极端值        │
│   中位数计算           │
│   核心成交区间         │
└───────┬────────────┘
        │
        ▼
┌────────────────────┐
│ 3. 什么值得买比价    │  失败→继续
│   Playwright 浏览器  │
│   关键字预过滤         │
│   AI 过滤不相关商品    │
│   IQR 统计             │
└───────┬────────────┘
        │
        ▼
┌────────────────────┐
│ 4. DeepSeek AI 分析 │  失败→降级为爬虫原数据
│   批量并发            │
│   利润率/评分/建议     │
│   建议售价/预估利润    │
└───────┬────────────┘
        │
        ├──▶ MySQL 去重存储（失败→继续）
        ├──▶ Excel 导出    （失败→继续）
        └──▶ 高利润率筛选  （失败→继续）
        │
        ▼
    返回 JSON（含 errors 列表）
```

---

## 命令行工具

```bash
# 启动 API 服务
python spider.py

# 命令行交互版爬虫（不用 API，直接输入关键词）
python test.py

# 测试浏览器环境
python test_browser.py

# 测试什么值得买比价
python price_fetcher.py
```

---

## AI 成本

| 项目 | 数值 |
|------|------|
| 单次 AI 分析 token | ~500-800 token |
| 30 条商品总 token | ~1.5-2.5 万 |
| DeepSeek 单价 | ~¥1 / 100 万 token |
| 单次搜索 AI 成本 | ~¥0.02 |

过滤不相关商品额外约 ¥0.001/次。

---

## 反风控说明

| 措施 | 说明 |
|------|------|
| 真实浏览器 | Playwright Chromium，不用 requests |
| 隐藏 webdriver | `navigator.webdriver = undefined` |
| 伪造 plugins/languages | 模拟真实浏览器指纹 |
| `--disable-blink-features=AutomationControlled` | 关闭 Chrome 自动化横幅 |
| 随机操作间隔 | 每次 click/fill/goto 后随机 sleep(1~3s) |
| 鼠标轨迹模拟 | 每次关键操作前随机移动鼠标 |
| 随机滚动 | 模拟真人浏览页面 |
| 登录态持久化 | 避免重复登录触发风控 |

如需更保守的反风控策略，可在 `.env` 中调大间隔：

```env
CRAWL_INTERVAL_MIN=3
CRAWL_INTERVAL_MAX=6
```

---

## 转换模型厂商

DeepSeek 使用 OpenAI 兼容接口，改三处即可切换到其他模型：

```python
# .env
DEEPSEEK_API_KEY=sk-your-openai-key
DEEPSEEK_BASE_URL=https://api.openai.com/v1
DEEPSEEK_MODEL=gpt-4o
```

支持所有兼容 OpenAI 接口的模型（通义千问、Moonshot、智谱 GLM 等）。

---

## 依赖

```
fastapi>=0.68.0
uvicorn>=0.15.0
python-dotenv>=0.19.0
tortoise-orm>=0.19.0
playwright>=1.32.0
aiomysql>=0.0.21
pandas>=1.3.0
openpyxl>=3.0.0
openai>=1.0.0
```

生成干净依赖文件请使用 `pipreqs`，不要用 `pip freeze`（会混杂其他项目的不相关依赖）。

---

## 注意事项

1. **法律合规** — 使用前请确保遵守相关法律法规和闲鱼平台协议，本代码仅用于学习研究
2. **反爬机制** — 高频率调用可能触发风控，建议合理设置请求间隔
3. **数据安全** — `.env` 和 `taobao_auth.json` 包含敏感信息，已在 `.gitignore` 中排除
4. **登录态过期** — 闲鱼登录态一般有效期几天，过期后需要删除 `taobao_auth.json` 重新扫码
