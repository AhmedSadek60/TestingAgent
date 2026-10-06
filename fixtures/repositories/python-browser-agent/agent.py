from playwright.sync_api import sync_playwright

def run(task: str):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://localhost:8080")
        page.click("text=Submit")
        page.screenshot(path="out.png")
