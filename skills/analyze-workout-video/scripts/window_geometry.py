"""Validate/normalize only bounded cached windows, never decode original media."""
import json
import os
from pathlib import Path
import subprocess


def geometry(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,nb_frames:stream_side_data=rotation',
        '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=20)
    stream = json.loads(result.stdout)['streams'][0]
    rotation = next((float(s['rotation']) for s in stream.get('side_data_list', []) if 'rotation' in s), 0)
    return stream, rotation


def ensure_geometry(window, expected_width, expected_height, expected_frames):
    window = Path(window)
    stream, rotation = geometry(window)
    if (stream['width'], stream['height']) == (expected_width, expected_height) and rotation == 0:
        return False
    output = window.with_name('.' + window.stem + '-geometry.mp4')
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(window), '-an',
            '-map_metadata', '-1', '-vf', f'scale={expected_width}:{expected_height},setsar=1',
            '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-crf', '20',
            '-metadata:s:v', 'rotate=0', str(output)], check=True, timeout=90)
        result, result_rotation = geometry(output)
        if ((result['width'], result['height']) != (expected_width, expected_height)
                or result_rotation != 0 or int(result['nb_frames']) != expected_frames):
            raise ValueError('window display geometry/frame count validation failed')
        os.replace(output, window)
        return True
    finally:
        output.unlink(missing_ok=True)
