# Cookbook: Schema-Driven Web Extraction to JSON

## Objective
Extract structured JSON data from a target web page using HTML pre-stripping and JSON Schema validation.

## Prerequisites
- Python 3.11+ with `urllib` and `json`.

## Execution Recipe
1. **Fetch & Strip Heavy HTML Elements**:
   ```python
   import re, urllib.request

   req = urllib.request.Request(url, headers={"User-Agent": "Brahma/1.0"})
   html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
   # Strip scripts, styles, comments, and SVGs:
   clean = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
   clean = re.sub(r"<!--.*?-->", "", clean, flags=re.DOTALL)
   clean_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", clean)).strip()[:10000]
   ```
2. **Execute Structured Schema Extraction**:
   Pass `clean_text` to agent completer with strict JSON Schema:
   ```json
   {
     "type": "object",
     "required": ["items"],
     "properties": {
       "items": {
         "type": "array",
         "items": { "type": "object", "properties": { "title": {"type": "string"}, "price": {"type": "number"} } }
       }
     }
   }
   ```
3. **Verification Gate**:
   *Gate: Extracted payload parses as valid JSON and contains $\ge 1$ item with non-null values.*
4. **Remediation**:
   If dynamic JS rendering hides data, fetch through headless browser or query internal API endpoints directly.
