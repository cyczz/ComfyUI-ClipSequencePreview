import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from aiohttp import web
import folder_paths
from server import PromptServer
from . import ffmpeg_binary, normalize, run

_guard = threading.Lock()
EXTENSIONS = {'.mp4', '.mov', '.webm', '.mkv'}


def root():
    p = Path(folder_paths.get_output_directory()) / 'fixed_clip_preview'
    p.mkdir(parents=True, exist_ok=True)
    return p


def key_for(session, node):
    return hashlib.sha256(f'{session}:{node}'.encode()).hexdigest()[:24]


def load(key):
    path = root() / f'{key}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'fixed': [], 'candidate': None, 'seen': None, 'fps': 24, 'reset': 0, 'revision': 0}


def save(key, state):
    path = root() / f'{key}.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def clip(path):
    p = Path(path.strip().strip('"')).resolve()
    if not p.is_file() or p.suffix.lower() not in EXTENSIONS:
        raise ValueError('请选择存在的 MP4、MOV、WEBM 或 MKV 视频文件。')
    return {'path': str(p), 'name': p.name, 'signature': f'{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}'}


def render(key, state):
    clips = state['fixed'] + ([state['candidate']] if state['candidate'] else [])
    if not clips:
        state['preview'] = None
        return
    binary = ffmpeg_binary()
    cache = root() / 'cache'
    cache.mkdir(exist_ok=True)
    normalized = []
    for item in clips:
        if not Path(item['path']).is_file():
            raise ValueError('视频文件已不存在：' + item['path'])
        digest = hashlib.sha256(f"{item['signature']}:{state['fps']}".encode()).hexdigest()
        target = cache / f'{digest}.mp4'
        if not target.exists():
            temp = cache / f'{digest}_{uuid.uuid4().hex}.mp4'
            try:
                normalize(binary, item['path'], temp, state['fps'])
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
        normalized.append(target)
    output = root() / f'{key}_{uuid.uuid4().hex[:12]}.mp4'
    listing = root() / f'{uuid.uuid4().hex}.txt'
    try:
        listing.write_text(''.join(f"file 'cache/{p.name}'\n" for p in normalized), encoding='utf-8')
        run([binary, '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(output)])
    finally:
        listing.unlink(missing_ok=True)
    state['preview'] = {'filename': output.name, 'subfolder': 'fixed_clip_preview', 'type': 'output'}


def update(key, action, path='', fps=24, reset=0, revision=None):
    if not _guard.acquire(blocking=False):
        raise ValueError('正在处理视频，请稍后再操作。')
    try:
        state = copy.deepcopy(load(key))
        if revision is not None and revision != state['revision']:
            raise ValueError('片段状态已更新，请点击刷新后再操作。')
        if action == 'generated':
            item = clip(path)
            if state['reset'] != reset:
                state = {'fixed': [], 'candidate': None, 'seen': None, 'fps': fps, 'reset': reset, 'revision': state['revision']}
            if state['seen'] == item['signature'] and state['fps'] == fps:
                return state
            state['candidate'] = item
            state['seen'] = item['signature']
            state['fps'] = fps
        elif action == 'import':
            state['candidate'] = clip(path)
            state['seen'] = None
            state['fps'] = fps
        elif action == 'fix':
            if not state['candidate']:
                raise ValueError('当前没有候选视频。')
            state['fixed'].append(state['candidate'])
            state['candidate'] = None
        elif action == 'discard':
            state['candidate'] = None
        elif action == 'undo':
            if state['candidate']:
                raise ValueError('请先固定或舍弃当前候选，再取消最后一段固定。')
            if not state['fixed']:
                raise ValueError('还没有固定片段。')
            state['candidate'] = state['fixed'].pop()
        else:
            raise ValueError('未知操作。')
        render(key, state)
        state['revision'] += 1
        save(key, state)
        return state
    finally:
        _guard.release()


def generating():
    running, pending = PromptServer.instance.prompt_queue.get_current_queue()
    return bool(running or pending)


@PromptServer.instance.routes.get('/fixed-clip-preview/state')
async def get_state(request):
    key = key_for(request.query.get('session', ''), request.query.get('node', ''))
    return web.json_response(load(key))


@PromptServer.instance.routes.post('/fixed-clip-preview/action')
async def action(request):
    try:
        if generating():
            raise ValueError('生成队列尚未结束，请生成结束后再固定、取消或导入。')
        data = await request.json()
        fps = int(data.get('fps', 24))
        if not 1 <= fps <= 120:
            raise ValueError('帧率应在1到120之间。')
        state = await asyncio.to_thread(update, key_for(data['session'], data['node']), data['action'],
                                        data.get('path', ''), fps, 0, data.get('revision'))
        return web.json_response(state)
    except Exception as error:
        return web.json_response({'error': str(error)}, status=400)


@PromptServer.instance.routes.post('/fixed-clip-preview/upload')
async def upload(request):
    target = None
    try:
        if generating():
            raise ValueError('请在生成结束后导入视频。')
        reader = await request.multipart()
        field = await reader.next()
        suffix = Path(field.filename or '').suffix.lower()
        if suffix not in EXTENSIONS:
            raise ValueError('不支持的视频格式。')
        directory = Path(folder_paths.get_input_directory()) / 'fixed_clip_imports'
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f'{uuid.uuid4().hex}{suffix}'
        with target.open('wb') as stream:
            while chunk := await field.read_chunk():
                stream.write(chunk)
        return web.json_response({'path': str(target)})
    except Exception as error:
        if target:
            target.unlink(missing_ok=True)
        return web.json_response({'error': str(error)}, status=400)


class FixedClipPreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'session': ('STRING', {'default': 'selflift_avatar'}),
                            'frame_rate': ('INT', {'default': 24, 'min': 1, 'max': 120}),
                            'reset_token': ('INT', {'default': 0, 'min': 0}),
                            'previous_video': ('STRING', {'default': ''})},
                'optional': {'filenames': ('VHS_FILENAMES',)}, 'hidden': {'unique_id': 'UNIQUE_ID'}}

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('preview_path',)
    FUNCTION = 'preview'
    CATEGORY = 'video/连续视频预览'
    OUTPUT_NODE = True

    def preview(self, session, frame_rate, reset_token, previous_video='', filenames=None, unique_id='0'):
        key = key_for(session, unique_id)
        if filenames:
            videos = [p for p in filenames[1] if Path(p).suffix.lower() in EXTENSIONS]
            if not videos:
                raise ValueError('视频输出中没有可预览的视频文件。')
            state = update(key, 'generated', videos[-1], frame_rate, reset_token)
        else:
            state = load(key)
        info = state.get('preview')
        return {'ui': {'fixed_clip_state': [state]}, 'result': (str(root() / info['filename']) if info else '',)}
