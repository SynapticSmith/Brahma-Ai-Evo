# Cookbook: Video Subtitle & Audio Extraction via yt-dlp

## Objective
Extract closed captions and compressed audio tracks from a video URL without downloading full video files.

## Prerequisites
- `yt-dlp` and `ffmpeg` installed and in PATH.

## Execution Recipe
1. **Probe Available Subtitle Languages**:
   ```powershell
   yt-dlp --list-subs "<media_url>"
   ```
2. **Download Subtitle Track to Clean VTT**:
   ```powershell
   yt-dlp --write-sub --write-auto-sub --sub-lang "en.*" --skip-download --sub-format vtt -o "%(title)s.%(ext)s" "<media_url>"
   ```
3. **Extract Lightweight Speech Audio (if subtitles missing)**:
   ```powershell
   yt-dlp -x --audio-format mp3 --audio-quality 5 -o "%(title)s.%(ext)s" "<media_url>"
   ```
4. **Convert VTT to Clean Paragraph Markdown**:
   Filter out VTT timestamps and duplicate lines using regex:
   ```powershell
   (Get-Content -Path "*.vtt") | Where-Object { $_ -notmatch '-->|^[0-9]+$|^WEBVTT' } | Out-File "transcript.md"
   ```
5. **Verification Gate**:
   *Gate: Subtitle or audio file exists on disk with size > 1KB.*
6. **Remediation**:
   If video is geo-restricted or bot-blocked, pass browser cookies via `--cookies-from-browser chrome`.
