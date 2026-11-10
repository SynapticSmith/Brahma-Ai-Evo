# Cookbook: Deterministic FFmpeg Video & Audio Operations

## Objective
Execute precise media transformations (cutting, transcoding, loudness normalization, audio extraction) using standard FFmpeg commands.

## Prerequisites
- `ffmpeg` binary installed and available in system PATH.

## Execution Recipe
1. **Lossless Video Clip Trimming**:
   Extract a segment between timestamp 00:01:30 and 00:02:45 without re-encoding:
   ```powershell
   ffmpeg -ss 00:01:30 -to 00:02:45 -i input.mp4 -c copy output_clip.mp4
   ```
2. **Web-Optimized Transcode (H.264 / AAC)**:
   Compress a large video for streaming:
   ```powershell
   ffmpeg -i input.mov -c:v libx264 -crf 23 -preset medium -c:a aac -b:a 128k -movflags +faststart output.mp4
   ```
3. **EBU R128 Audio Loudness Normalization**:
   Normalize an audio track to standard podcast / web targets (-14 LUFS):
   ```powershell
   ffmpeg -i speech.wav -af loudnorm=I=-14:LRA=7:tp=-1.0 -ar 44100 normalized.wav
   ```
4. **Extract Representative Frame Thumbnail**:
   Export high-resolution JPEG at timestamp 00:00:15:
   ```powershell
   ffmpeg -ss 00:00:15 -i input.mp4 -vframes 1 -q:v 2 thumbnail.jpg
   ```
5. **Verification Gate**:
   Inspect output file metadata using `ffprobe`:
   ```powershell
   ffprobe -v error -show_entries format=duration,size output.mp4
   ```
6. **Remediation**:
   If video cuts with frozen initial frames in copy mode, remove `-c copy` and re-encode the stream to establish a keyframe at the cut boundary.
