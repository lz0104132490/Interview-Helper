import logging

from pyannote.audio import Pipeline

import config

diarization_pipeline: Pipeline | None = None


def load_diarization_pipeline() -> Pipeline | None:
    global diarization_pipeline
    if diarization_pipeline is not None:
        return diarization_pipeline
    if not config.HF_TOKEN:
        logging.warning("HF_TOKEN not set; diarization disabled.")
        return None
    diarization_pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=config.HF_TOKEN,
    )
    return diarization_pipeline


def diarize_audio(audio_path: str) -> list[tuple[float, float, str]]:
    if not config.DIARIZATION_ENABLED:
        return []
    pipeline = load_diarization_pipeline()
    if pipeline is None:
        return []
    diarization = pipeline(audio_path)
    segments: list[tuple[float, float, str]] = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append((turn.start, turn.end, speaker))
    return segments


def assign_speakers(
    transcript_segments: list[dict],
    diarization_segments: list[tuple[float, float, str]],
) -> None:
    for segment in transcript_segments:
        best_speaker = None
        best_overlap = 0.0
        start = float(segment["start"])
        end = float(segment["end"])
        for diar_start, diar_end, speaker in diarization_segments:
            overlap_start = max(start, diar_start)
            overlap_end = min(end, diar_end)
            overlap = overlap_end - overlap_start
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = speaker
        if best_speaker:
            segment["speaker"] = best_speaker


def pick_target_speaker(
    diarization_segments: list[tuple[float, float, str]],
) -> str | None:
    if config.DIARIZATION_TARGET:
        return config.DIARIZATION_TARGET
    totals: dict[str, float] = {}
    for start, end, speaker in diarization_segments:
        totals[speaker] = totals.get(speaker, 0.0) + (end - start)
    if not totals:
        return None
    return max(totals.items(), key=lambda item: item[1])[0]
