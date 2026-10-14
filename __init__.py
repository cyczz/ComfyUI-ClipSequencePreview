import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid

import folder_paths

WEB_DIRECTORY = './web'
_lock = threading.Lock()


def ffmpeg_binary():
    binary = shutil.which('ffmpeg')
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError('需要 FFmpeg：安装 imageio-ffmpeg 或将 ffmpeg 加入 PATH。')


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError('视频拼接失败：' + result.stderr[-3000:])


def normalize(binary, source, target, fps):
    probe = subprocess.run([binary, '-hide_banner', '-i', str(source)], capture_output=True,
                           text=True, encoding='utf-8', errors='replace')
    args = [binary, '-y', '-i', str(source)]
    if 'Audio:' not in probe.stderr:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    args += ['-map', '0:v:0', '-map', '0:a:0' if 'Audio:' in probe.stderr else '1:a:0',
             '-vf', f'scale=768:432:force_original_aspect_ratio=decrease,pad=768:432:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}',
             '-af', 'apad', '-shortest', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
             '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', '-ac', '2', str(target)]
    run(args)


class PreviousClipPreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'filenames': ('VHS_FILENAMES',),
                            'session': ('STRING', {'default': 'selflift_avatar'}),
                            'frame_rate': ('INT', {'default': 24, 'min': 1, 'max': 120}),
                            'reset_token': ('INT', {'default': 0, 'min': 0}),
                            'previous_video': ('STRING', {'default': ''})},
                'hidden': {'unique_id': 'UNIQUE_ID'}}

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('preview_path',)
    FUNCTION = 'preview'
    CATEGORY = 'video/连续视频预览'
    OUTPUT_NODE = True

    def preview(self, filenames, session, frame_rate, reset_token, previous_video, unique_id='0'):
        paths = filenames[1]
        videos = [Path(p) for p in paths if Path(p).suffix.lower() in ('.mp4', '.mov', '.webm', '.mkv')]
        if not videos or not videos[-1].is_file():
            raise RuntimeError('没有找到当前视频，请把 VHS Video Combine 设置为 MP4 输出。')
        current = videos[-1].resolve()
        signature = f'{current}:{current.stat().st_size}:{current.stat().st_mtime_ns}'
        key = hashlib.sha256(f'{session}:{unique_id}'.encode()).hexdigest()[:20]
        root = Path(folder_paths.get_output_directory()) / 'previous_clip_preview'
        root.mkdir(parents=True, exist_ok=True)
        state_path = root / f'{key}.json'
        with _lock:
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            cached = state.get('signature') == signature and state.get('reset_token') == reset_token and state.get('fps') == frame_rate and state.get('manual') == previous_video
            if cached and Path(state.get('preview', '')).is_file():
                output = Path(state['preview'])
            else:
                previous = previous_video.strip() or (state.get('current', '') if state.get('reset_token') == reset_token else '')
                if previous_video.strip() and not Path(previous).is_file():
                    raise RuntimeError('指定的上一段视频不存在：' + previous)
                output = root / f'{key}_{uuid.uuid4().hex[:12]}.mp4'
                work = root / f'work_{uuid.uuid4().hex}'
                work.mkdir()
                try:
                    binary = ffmpeg_binary()
                    clips = []
                    if previous and Path(previous).is_file() and Path(previous).resolve() != current:
                        clips.append(Path(previous))
                    clips.append(current)
                    for index, clip in enumerate(clips):
                        normalize(binary, clip, work / f'{index}.mp4', frame_rate)
                    listing = work / 'clips.txt'
                    listing.write_text(''.join(f"file '{i}.mp4'\n" for i in range(len(clips))), encoding='utf-8')
                    run([binary, '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(output)])
                    updated = {'current': str(current), 'signature': signature, 'preview': str(output),
                               'reset_token': reset_token, 'fps': frame_rate, 'manual': previous_video}
                    temporary = state_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(updated, ensure_ascii=False), encoding='utf-8')
                    os.replace(temporary, state_path)
                finally:
                    shutil.rmtree(work)
        return {'ui': {'previous_clip_preview': [{'filename': output.name, 'subfolder': 'previous_clip_preview', 'type': 'output'}]},
                'result': (str(output),)}


NODE_CLASS_MAPPINGS = {'PreviousClipPreview': PreviousClipPreview}
NODE_DISPLAY_NAME_MAPPINGS = {'PreviousClipPreview': '上一段＋当前段 · 自动拼接预览'}
from .manager import FixedClipPreview
NODE_CLASS_MAPPINGS['PreviousClipPreview'] = FixedClipPreview
NODE_CLASS_MAPPINGS['ClipSequencePreview'] = FixedClipPreview
NODE_DISPLAY_NAME_MAPPINGS['PreviousClipPreview'] = '逐段固定拼接预览（旧工作流兼容）'
NODE_DISPLAY_NAME_MAPPINGS['ClipSequencePreview'] = '逐段固定拼接预览'
