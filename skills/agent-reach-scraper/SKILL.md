---
name: agent-reach-scraper
description: Zero-API-fee multi-platform scraper to search and harvest public web content across GitHub, Reddit, documentation portals, and community sites.
metadata:
  domain: research
  tags: [scraper, search, web, github, reddit, public-data, research]
triggers:
  - "agent reach scraper"
  - "scrape public web"
  - "search reddit and github"
  - "zero api search"
  - "harvest documentation"
---

# Agent Reach Scraper Skill

## Overview
Drawn from the `Agent-Reach` repository, this skill gives the coding agent eyes across the open internet without relying on expensive, rate-limited commercial search APIs. By utilizing clean public endpoints, RSS feeds, GitHub public APIs, and headless DOM extractors, the agent can research technical discussions, library release notes, and community bug reports autonomously.

## Safety & Rate Limiting
1. **Respect Robots.txt**: Adhere to site crawling rules and backoff headers (`429 Too Many Requests`).
2. **Polite Jitter & Backoff**: Add random 500ms–1500ms intervals between requests to avoid overloading origin servers.
3. **Structured Extraction**: Extract text into Markdown summaries discarding noisy header/footer boilerplate.
