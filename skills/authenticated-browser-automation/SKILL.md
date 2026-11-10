---
name: authenticated-browser-automation
description: Automate browser workflows while leveraging user's logged-in session cookies without stealing foreground focus.
metadata:
  domain: coding
  tags: [browser, automation, cookies, authenticated, playwright, headless]
triggers:
  - "authenticated browser automation"
  - "automate logged in browser"
  - "browser session reuse"
  - "headless browser with cookies"
  - "automated web navigation"
---

# Authenticated Browser Automation Skill

## Overview
Drawn from `BrowserSkill` and `ego-lite`, this skill enables AI agents to automate web tasks that require authentication (e.g. internal dashboards, GitHub PR reviews, SaaS portals) by reusing existing session cookies rather than prompting the user for credentials or solving CAPTCHAs.

## Core Rules
1. **Never Steal Foreground Focus**: Run headless or in an isolated background CDP (Chrome DevTools Protocol) context.
2. **Never Bypass CAPTCHAs**: Per safety guidelines, if a page is gated by a CAPTCHA or anti-bot challenge, pause execution and prompt the user to complete the verification in a visible window.
3. **Cookie Isolation**: Store harvested session state strictly in memory or encrypted user storage; never log session cookies in plain text.
4. **Deterministic Selectors**: Prefer robust text/aria-label locators over brittle CSS class strings that break across builds.
