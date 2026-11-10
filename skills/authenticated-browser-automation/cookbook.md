# Cookbook: Background Browser Automation via Persistent Context

## Objective
Launch an isolated headless browser session that inherits local browser cookies to inspect authenticated pages.

## Prerequisites
- Playwright or CDP client available (`python -m playwright` or Chrome remote debugging port).

## Execution Recipe
1. **Locate Chrome User Profile or Exported Cookies**:
   Identify path to user data directory or exported session state (`%LOCALAPPDATA%\Google\Chrome\User Data`).
2. **Launch Background Context**:
   Use Playwright `launch_persistent_context` in headless mode:
   ```python
   from playwright.sync_api import sync_playwright
   with sync_playwright() as p:
       browser = p.chromium.launch_persistent_context(
           user_data_dir=user_data_path,
           headless=True,
           args=["--disable-blink-features=AutomationControlled"]
       )
       page = browser.new_page()
       page.goto(target_url, wait_until="networkidle")
   ```
3. **Inspect Authenticated DOM**:
   Extract content or take background screenshot:
   ```python
   content = page.locator("main").inner_text()
   ```
4. **Verification Gate**:
   - Status 200 received without redirect to login page.
   - User foreground focus was not interrupted.
5. **Remediation**:
   If an anti-bot challenge is encountered, stop immediately, alert the user, and allow manual completion in visible browser.
