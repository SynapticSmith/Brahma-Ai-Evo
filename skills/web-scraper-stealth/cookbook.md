# Cookbook: Web Scraping & Content Ingestion

## Objective
Fetch, sanitize, and structure web content with polite rate limiting and fallback headers.

## Prerequisites
- python 3.11+

## Execution Recipe
1. **Check robots.txt**:
   Inspect target site's robots.txt for disallowed routes and crawl delays.
2. **Execute Polite Fetch**:
   Use standard User-Agent header, timeout clamping, and exponential backoff.
3. **Sanitize & Convert HTML to Markdown**:
   Strip scripts, styles, and navigation elements. Extract article body.
4. **Verification Gate**:
   Check that extracted text is non-empty, contains article headers, and has no raw HTML tags.
5. **Remediation**:
   If 403 Forbidden is returned, rotate User-Agent or fall back to cached copy.
