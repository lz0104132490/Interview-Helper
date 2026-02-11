import numpy as np
import torch
from faster_whisper import WhisperModel

import config

whisper_model: WhisperModel | None = None
stream_whisper_model: WhisperModel | None = None


def load_whisper_model() -> WhisperModel:
    global whisper_model
    if whisper_model is not None:
        return whisper_model
    device = config.WHISPER_DEVICE
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    whisper_model = WhisperModel(
        config.AUDIO_STT_MODEL,
        device=device,
        compute_type=config.WHISPER_COMPUTE_TYPE,
    )
    return whisper_model


def load_stream_whisper_model() -> WhisperModel:
    global stream_whisper_model
    if stream_whisper_model is not None:
        return stream_whisper_model

    device = config.STREAM_DEVICE
    compute_type = config.STREAM_COMPUTE_TYPE
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    if device != "cuda":
        compute_type = "int8" if "int8" in compute_type else "float32"

    stream_whisper_model = WhisperModel(
        config.STREAM_WHISPER_MODEL,
        device=device,
        compute_type=compute_type,
    )
    return stream_whisper_model


def resample_audio(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio
    duration = audio.shape[0] / src_rate
    dst_length = int(duration * dst_rate)
    if dst_length <= 1:
        return audio[:0]
    src_positions = np.linspace(0.0, duration, num=audio.shape[0], endpoint=False)
    dst_positions = np.linspace(0.0, duration, num=dst_length, endpoint=False)
    return np.interp(dst_positions, src_positions, audio).astype(np.float32)


def to_mono(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        return samples
    if samples.shape[1] == 1:
        return samples[:, 0]
    return samples.mean(axis=1)
