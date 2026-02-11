import re

import config

QUESTION_PREFIXES = (
    "what",
    "why",
    "how",
    "when",
    "where",
    "who",
    "which",
    "can you",
    "could you",
    "would you",
    "do you",
    "tell me",
    "explain",
    "compare",
    "difference",
    "walk me through",
)


def extract_question(transcript: str) -> str | None:
    normalized = " ".join(transcript.split())
    if not normalized:
        return None

    sentences = re.split(r"(?<=[\.\?\!])\s+", normalized)
    candidates: list[str] = []
    for sentence in sentences:
        trimmed = sentence.strip()
        if not trimmed:
            continue
        lower = trimmed.lower()
        if "?" in trimmed:
            candidates.append(trimmed)
            continue
        if any(lower.startswith(prefix) for prefix in QUESTION_PREFIXES):
            candidates.append(trimmed)

    if not candidates:
        return None

    question = candidates[-1]
    if len(question.split()) < config.AUDIO_QUESTION_MIN_WORDS:
        return None
    if not question.endswith("?"):
        question = question.rstrip(".! ") + "?"
    return question


def build_transcript_for_speaker(
    transcript_segments: list[dict],
    speaker: str | None,
) -> str:
    if not transcript_segments:
        return ""
    if speaker is None:
        return " ".join(segment["text"] for segment in transcript_segments)
    selected = [
        segment["text"]
        for segment in transcript_segments
        if segment.get("speaker") == speaker
    ]
    if not selected:
        return " ".join(segment["text"] for segment in transcript_segments)
    return " ".join(selected)
