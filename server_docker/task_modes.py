"""Shared task-mode contract; legacy requests remain compatible."""
from __future__ import annotations

MODES = {
    'video_only': {'download': True, 'analyze': False, 'narration': False, 'raw': False},
    'narration_only': {'download': False, 'analyze': False, 'narration': False, 'raw': True},
    'analysis_only': {'download': False, 'analyze': True, 'narration': False, 'raw': False},
    'full': {'download': True, 'analyze': True, 'narration': True, 'raw': True},
    'custom': None,
}


def resolve_options(body: dict, *, preset_id: str | None = None, prompt: str | None = None) -> dict:
    mode = str(body.get('task_mode') or 'legacy')
    if mode not in {*MODES, 'legacy'}:
        raise ValueError('INVALID_TASK_MODE')
    if mode == 'legacy':
        # Historical clients requested AI analysis and optional MP4.
        return {'task_mode': 'legacy', 'download': bool(body.get('download', True)),
                'analyze': True, 'narration': (preset_id == 'script_copy'),
                'raw': False, 'timed': False}
    flags = dict(MODES[mode]) if MODES[mode] is not None else {
        'download': bool(body.get('download', True)),
        'analyze': bool(body.get('analyze', True)),
        'narration': bool(body.get('narration', False)),
        'raw': bool(body.get('raw_transcript', False)),
    }
    if mode in ('full', 'narration_only'):
        # Explicit flags allow opting out of the preset sub-options.
        flags['narration'] = bool(body.get('narration', flags['narration']))
        flags['raw'] = bool(body.get('raw_transcript', flags['raw']))
    flags['timed'] = bool(body.get('timed_transcript', False))
    if flags['timed'] and not flags['raw']:
        raise ValueError('TIMELINE_REQUIRES_RAW_TRANSCRIPT')
    if flags['narration'] and not flags['analyze']:
        # AI edited narration requires an analysis request. RAW-only is available.
        raise ValueError('NARRATION_REQUIRES_ANALYSIS')
    if not any(flags[key] for key in ('download', 'analyze', 'raw')):
        raise ValueError('EMPTY_TASK')
    return {'task_mode': mode, **flags}
