"""Bounded, cached-on-first-play MP4 conversion, including Vercel's FFmpeg binary."""
import os
import re
import subprocess
from imageio_ffmpeg import get_ffmpeg_exe

MAX_VIDEO_INPUT = 32 * 1024 * 1024
MAX_VIDEO_OUTPUT = 4 * 1024 * 1024


def transcode_480p(source, destination, output_format="mp4"):
    if output_format not in {"mp4", "webm"}:
        raise ValueError("Unsupported playback format")
    ffmpeg = get_ffmpeg_exe()
    inputs = ['-protocol_whitelist', 'file,pipe', '-format_whitelist',
              'mov,matroska,webm,avi,mpeg,mpegts,gif', '-i', source]
    probe = subprocess.run([ffmpeg, '-hide_banner', *inputs], stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True, timeout=5)
    duration = re.search(r'Duration:\s*(\d+):(\d+):([\d.]+)', probe.stderr)
    if not duration:
        raise ValueError('Unsupported or damaged video')
    hours, minutes, seconds = map(float, duration.groups())
    seconds += hours * 3600 + minutes * 60
    if not 0 < seconds <= 300:
        raise ValueError('Video must be at most five minutes')
    # Reserve space for AAC audio and container overhead below Vercel's 4.5MB response limit.
    bitrate = min(800_000, int(MAX_VIDEO_OUTPUT * 8 * .85 / seconds) - 48_000)
    bitrate = max(32_000, bitrate)
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *inputs,
               '-map', '0:v:0', '-map', '0:a:0?', '-vf',
               "scale=w='min(854,iw)':h='min(480,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
               '-r', '30', '-threads', '2',
               '-pix_fmt', 'yuv420p', '-b:v', str(bitrate), '-maxrate', str(bitrate),
               '-bufsize', str(bitrate * 2), '-b:a', '48k', '-ac', '2', '-map_metadata', '-1']
    if output_format == 'mp4':
        command += ['-c:v', 'libx264', '-preset', 'veryfast', '-c:a', 'aac', '-movflags', '+faststart']
    else:
        command += ['-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '6', '-c:a', 'libopus']
    command += ['-f', output_format, destination]
    try:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20, check=True)
        if not 0 < os.path.getsize(destination) <= MAX_VIDEO_OUTPUT:
            raise ValueError('Video cannot fit the playback size limit')
    except Exception:
        try: os.remove(destination)
        except FileNotFoundError: pass
        raise
