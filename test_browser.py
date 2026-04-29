import os
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "D:\\my-playwright-browsers"

from playwright.sync_api import sync_playwright

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        print("✅ 浏览器启动成功")
        browser.close()
except Exception as e:
    print(f"❌ 启动失败: {e}")
    import traceback
    traceback.print_exc()