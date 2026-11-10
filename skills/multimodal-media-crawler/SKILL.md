---
name: multimodal-media-crawler
description: Multimodal media extraction protocol capturing video streams, audio tracks, closed captions, and post metadata from public platforms for offline analysis.
metadata:
  domain: multimodal
  tags: [crawler, media, video, audio, transcription, social-media, yt-dlp, multimodal]
triggers:
  - "multimodal media crawler"
  - "extract video transcript"
  - "crawl social media post"
  - "download media assets"
  - "multimodal content extraction"
  - "scrape media stream"
  - "ingest video content"
---

# Multimodal Media Crawler Skill

## Overview
Drawn from `MediaCrawler` and multimodal ingestion pipelines, this skill equips AI agents to extract structured multimedia assets (video, audio waveforms, subtitles, comments, and creator metadata) from public online platforms. Web pages containing embedded video players cannot be understood through plain HTML scraping alone. This skill uses deterministic command-line engines (`yt-dlp`, `ffmpeg`) to extract high-fidelity audio tracks and VTT/SRT subtitles for downstream speech-to-text or visual reasoning.

## Operational Invariants
1. **Subtitle & Metadata First**: Always attempt to retrieve existing closed captions (`--write-sub --write-auto-sub --skip-download`) before downloading heavy raw video files, conserving bandwidth and disk.
2. **Audio-Only Extraction**: For podcasts, lectures, and interviews where visual gestures are secondary, extract compressed audio (`-x --audio-format mp3`) instead of multi-gigabyte 4K video.
3. **Strict Rate-Limiting & Jitter**: Respect platform rate limits by adding randomized sleep intervals (`--sleep-interval 3 --max-sleep-interval 10`) between batch requests.
4. **Permanent Attribution**: Save platform metadata (URL, author, upload date, license, view counts) into an adjacent JSON sidecar.

## Extraction Pipeline
1. **Probe Media Endpoints**: Query URL metadata without downloading (`yt-dlp -J <url>`) to verify formats, durations, and subtitle tracks.
2. **Extract Transcripts**: Download native or auto-generated subtitles in WebVTT format and convert to clean markdown.
3. **Audio Demuxing (if required)**: Extract 16kHz mono audio stream optimized for Whisper/SAPI transcription models.
4. **Metadata Normalization**: Save structured markdown summary with timestamps, key topics, and direct links.
