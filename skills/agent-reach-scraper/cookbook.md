# Cookbook: Zero-Fee Multi-Platform Web Research

## Objective
Search public platforms (GitHub repos/issues, documentation sites, technical forums) for solutions to obscure error codes or library bugs.

## Prerequisites
- Standard Python `urllib` or `httpx` with network permission enabled.

## Execution Recipe
1. **Target GitHub Public Search**:
   Query GitHub's public search API for code snippets or issues:
   ```python
   import urllib.request, json
   url = f"https://api.github.com/search/issues?q={query}+in:title,body"
   req = urllib.request.Request(url, headers={"User-Agent": "Brahma-Agent-Research/1.0"})
   with urllib.request.urlopen(req) as resp:
       data = json.loads(resp.read().decode())
   ```
2. **Fetch Documentation Pages via Markdown Extractor**:
   Retrieve target technical doc and convert to clean text using `trafilatura` or `markitdown`.
3. **Filter and Deduplicate**:
   Discard navigation bars, cookie banners, and footer text; extract core code solutions.
4. **Verification Gate**:
   - Relevant answers cite original URLs and timestamps.
   - Total network payload < 1 MB.
5. **Remediation**:
   If 429 Too Many Requests is returned, apply exponential backoff (wait 2s $\rightarrow$ 4s $\rightarrow$ 8s) or switch to local second-brain cache.
