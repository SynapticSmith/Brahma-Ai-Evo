---
name: ffmpeg-media-processing
description: Machine-readable FFmpeg media pipeline for cutting, transcoding, audio loudness normalization, subtitle extraction, and waveform rendering.
metadata:
  domain: devops
  tags: [ffmpeg, media, video, audio, transcoding, loudness, subtitles]
triggers:
  - "ffmpeg media processing"
  - "transcode video"
  - "normalize audio loudness"
  - "extract audio from video"
  - "cut video clip"
  - "generate video thumbnail"
---

# FFmpeg Media Processing Skill

## Overview
Drawn from the `ffmpeg-skill` framework, this skill provides deterministic, production-grade recipes for video and audio processing via the `ffmpeg` command-line utility. It eliminates the need for heavyweight desktop GUI editors for routine media operations like trimming, re-encoding, loudness leveling (-14 LUFS for web/podcast), and asset extraction.

## Core Media Standards
1. **Web Video Baseline**: H.264 video (`libx264`, `crf=23`, `preset=medium`) + AAC audio (`128k`, `44100Hz`) inside an MP4 container with `-movflags +faststart`.
2. **Audio Normalization**: EBU R128 two-pass loudness normalization (`loudnorm=I=-14:LRA=7:tp=-1.0`).
3. **Lossless Trimming**: Fast stream-copy seeking using `-ss` before `-i` and `-c copy`.
4. **Thumbnail Extraction**: High-quality single-frame export using `-vframes 1 -q:v 2`.
