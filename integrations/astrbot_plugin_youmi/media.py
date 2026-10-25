"""Provenance and bounded temporary WAV files; AstrBot owns image/STT/OneBot conversion."""

import io
from pathlib import Path
import shutil
import tempfile
import wave


def source_kind(raw):
    if not isinstance(raw, dict):
        return 'text'
    parts = raw.get('message', [])
    if isinstance(parts, list) and any(isinstance(item, dict) and item.get('type') == 'record' for item in parts):
        if any(isinstance(item, dict) and item.get('type') == 'image' for item in parts):
            return 'media'  # Cannot distinguish caption from transcription after preprocessing.
        return 'asr'
    if isinstance(parts, list) and any(isinstance(item, dict) and item.get('type') == 'text'
                                     and item.get('data', {}).get('text', '').strip() for item in parts):
        return 'text'
    return 'media' if isinstance(parts, list) and parts else 'text'


def original_text(raw):
    """Only OneBot text segments are user evidence; preprocessing may add captions."""
    if not isinstance(raw, dict) or not isinstance(raw.get('message'), list):
        return ''
    return ''.join(item.get('data', {}).get('text', '') for item in raw['message']
                   if isinstance(item, dict) and item.get('type') == 'text').strip()


def save_wav(audio, directory):
    if not isinstance(audio, bytes) or len(audio) > 3 * 1024 * 1024:
        raise ValueError('audio_too_large')
    with wave.open(io.BytesIO(audio), 'rb') as source:
        if (source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 24000
                or not 0 < source.getnframes() <= 24000 * 60):
            raise ValueError('unsupported_audio')
        if len(source.readframes(source.getnframes())) != source.getnframes() * 2:
            raise ValueError('truncated_audio')
    with tempfile.NamedTemporaryFile(prefix='youmi-', suffix='.wav', dir=directory, delete=False) as output:
        output.write(audio)
        return Path(output.name)


def selected_image_file(source, directory):
    """One selected Meme asset in AstrBot's allowed tool temp root, not a library copy."""
    source = Path(source)
    if source.suffix.lower() not in {'.gif', '.png', '.jpg', '.jpeg', '.webp'} or source.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('unsupported_meme_image')
    Path(directory).mkdir(parents=True, exist_ok=True)
    target = None
    try:
        with source.open('rb') as incoming, tempfile.NamedTemporaryFile(prefix='youmi-meme-', suffix=source.suffix,
                dir=directory, delete=False) as output:
            target = Path(output.name)
            shutil.copyfileobj(incoming, output, length=65536)
        return target
    except BaseException:
        if target:
            target.unlink(missing_ok=True)
        raise
