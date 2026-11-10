---
name: smart-web-scraper-graph
description: Declarative AI-driven web scraping protocol converting messy HTML and dynamic DOM trees into strictly typed JSON models without brittle CSS/XPath selectors.
metadata:
  domain: coding
  tags: [scraper, json, ai-scraping, dom, scrapegraph, firecrawl, extraction, coding]
triggers:
  - "smart web scraper graph"
  - "ai web scraper"
  - "extract json from web page"
  - "declarative web scraping"
  - "schema based scraper"
  - "scrape without css selectors"
  - "extract structured data from url"
---

# Smart Web Scraper Graph Skill

## Overview
Drawn from `Scrapegraph-ai` and `firecrawl`, this skill enables AI agents to extract structured, schema-compliant JSON payloads directly from raw HTML, markdown web snapshots, or dynamic DOM trees. Traditional web scraping relies on hardcoded CSS selectors (`div.product-item > span.price-tag`) that break with every frontend redesign. This skill uses declarative JSON schema extraction: defining the desired fields, types, and validation rules, allowing semantic pattern matching to extract exact values regardless of class name changes.

## Scraping Invariants
1. **Schema-Driven Extraction**: Every extraction request must declare a typed JSON schema (Pydantic model or JSON Schema) specifying required fields and datatypes.
2. **Selector Independence**: Never depend on fragile transient CSS class hashes (e.g. `css-1v8m2p9`); extract based on semantic DOM structure, text patterns, and accessibility labels.
3. **HTML Pre-Stripping**: Strip `<script>`, `<style>`, SVG vectors, and base64 images before LLM ingestion to reduce token payload by $>85\%$.
4. **Data Sanitization**: Strip non-breaking spaces, currency symbols, and extra whitespace from extracted numerical values.

## Extraction Pipeline
1. **Fetch & Sanitize**: Retrieve URL contents and filter down to semantic HTML (headings, tables, lists, article text).
2. **Schema Definition**: Author target JSON schema with field descriptions and validation constraints.
3. **Extraction Prompting**: Feed sanitized DOM slice into structured extraction parser.
4. **Validation**: Validate output against JSON schema; fail-closed on missing required fields.
