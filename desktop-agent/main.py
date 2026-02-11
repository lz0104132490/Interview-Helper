import base64
import logging
import os
import re
import sys
import subprocess
import threading
import time
from queue import Queue, Full
from dataclasses import dataclass
from typing import Callable
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import keyboard
import mss
import mss.tools
import requests
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from openai import OpenAI
from pyannote.audio import Pipeline
import torch

load_dotenv()

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:4000")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

BASE_PROMPT = "Solve the problem shown in this image. Show your work."
DEFAULT_PROMPT = os.getenv("PROMPT", BASE_PROMPT)
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_HOTKEY = os.getenv("HOTKEY", "ctrl+alt+space")

AUDIO_HOTKEY = os.getenv("AUDIO_HOTKEY", "alt+q")
AUDIO_GPT_MODEL = os.getenv("AUDIO_GPT_MODEL", "qwen-max")
AUDIO_STT_MODEL = os.getenv("AUDIO_STT_MODEL", "large-v3")
AUDIO_PROMPT = os.getenv(
    "AUDIO_PROMPT",
    "Answer the interviewer's question clearly and concisely.",
)


def env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


AUDIO_MAX_SECONDS = env_int("AUDIO_MAX_SECONDS", 20)
AUDIO_QUESTION_MIN_WORDS = env_int("AUDIO_QUESTION_MIN_WORDS", 6)

WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "zh")

DIARIZATION_ENABLED = os.getenv("DIARIZATION_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
    "y",
    "on",
)
DIARIZATION_TARGET = os.getenv("DIARIZATION_TARGET")
HF_TOKEN = os.getenv("HF_TOKEN")

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_ENDPOINT = os.getenv(
    "DASHSCOPE_ENDPOINT",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
)

REQUEST_COOLDOWN_SECONDS = float(os.getenv("REQUEST_COOLDOWN_SECONDS", "5"))
MAX_QUEUE_SIZE = env_int("MAX_QUEUE_SIZE", 3)

if not OPENAI_API_KEY:
    print(
        "OPENAI_API_KEY missing. Copy env.sample -> .env and fill the key.",
        file=sys.stderr,
    )
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


@dataclass
class ModeConfig:
    name: str
    hotkey: str
    model: str
    prompt: str


def load_modes() -> list[ModeConfig]:
    modes: list[ModeConfig] = []

    primary_hotkey = os.getenv("PRIMARY_HOTKEY", DEFAULT_HOTKEY)
    primary_model = os.getenv("PRIMARY_MODEL", DEFAULT_MODEL)
    primary_prompt = os.getenv("PRIMARY_PROMPT", DEFAULT_PROMPT)
    if primary_hotkey:
        modes.append(
            ModeConfig(
                name="primary",
                hotkey=primary_hotkey,
                model=primary_model,
                prompt=primary_prompt,
            )
        )

    secondary_hotkey = os.getenv("SECONDARY_HOTKEY")
    if secondary_hotkey:
        secondary_model = os.getenv("SECONDARY_MODEL", primary_model)
        secondary_prompt = os.getenv("SECONDARY_PROMPT", primary_prompt)
        modes.append(
            ModeConfig(
                name="secondary",
                hotkey=secondary_hotkey,
                model=secondary_model,
                prompt=secondary_prompt,
            )
        )

    if not modes:
        raise SystemExit(
            "No hotkeys configured. Set PRIMARY_HOTKEY or HOTKEY in the .env file."
        )

    return modes


client = OpenAI(api_key=OPENAI_API_KEY)
http_session = requests.Session()
processing_lock = threading.Lock()
modes = load_modes()

audio_lock = threading.Lock()
audio_process: subprocess.Popen | None = None
audio_output_path: str | None = None
audio_start_time = 0.0
audio_stop_timer: threading.Timer | None = None

request_queue: Queue[tuple[str, Callable[[], None]]] = Queue(maxsize=MAX_QUEUE_SIZE)
next_request_at = 0.0
queue_lock = threading.Lock()

whisper_model: WhisperModel | None = None
diarization_pipeline: Pipeline | None = None

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


def capture_fullscreen_png() -> bytes:
    with mss.mss() as sct:
        monitor = sct.monitors[0]  # primary virtual screen
        shot = sct.grab(monitor)
        return mss.tools.to_png(shot.rgb, shot.size)


def call_openai(model_name: str, feedback_prompt: str, image_b64: str) -> str:
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": feedback_prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{image_b64}",
                    },
                ],
            }
        ],
    )

    text = getattr(response, "output_text", None)
    if text:
        return text.strip()

    # fallback if output_text missing
    chunks = []
    for item in getattr(response, "output", []):
        if item.type == "message":
            for content in item.content:
                if content.type == "output_text":
                    chunks.append(content.text)
    return "\n".join(chunks).strip()


def call_openai_text(model_name: str, system_prompt: str, question: str) -> str:
    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": question}],
            },
        ],
    )

    text = getattr(response, "output_text", None)
    if text:
        return text.strip()

    chunks = []
    for item in getattr(response, "output", []):
        if item.type == "message":
            for content in item.content:
                if content.type == "output_text":
                    chunks.append(content.text)
    return "\n".join(chunks).strip()


def call_dashscope(model_name: str, system_prompt: str, question: str) -> str:
    if not DASHSCOPE_API_KEY:
        logging.error("DASHSCOPE_API_KEY missing; cannot call DashScope.")
        return ""
    payload = {
        "model": model_name,
        "input": {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ]
        },
        "parameters": {"result_format": "message"},
    }
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    res = http_session.post(
        DASHSCOPE_ENDPOINT, json=payload, headers=headers, timeout=30
    )
    res.raise_for_status()
    data = res.json()
    output = data.get("output", {})
    choices = output.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
    return ""


def audio_helper_path() -> Path:
    env_path = os.getenv("AUDIO_HELPER_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().with_name("capture_audio.exe")


def start_audio_capture() -> None:
    global audio_process, audio_output_path, audio_start_time, audio_stop_timer
    with audio_lock:
        if audio_process is not None:
            logging.info("Audio capture already running.")
            return

        helper = audio_helper_path()
        if not helper.exists():
            logging.error("Audio helper not found at %s", helper)
            return

        temp_file = NamedTemporaryFile(delete=False, suffix=".wav")
        audio_output_path = temp_file.name
        temp_file.close()

        args = [str(helper), "--out", audio_output_path]
        audio_process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        audio_start_time = time.perf_counter()

        if AUDIO_MAX_SECONDS > 0:
            audio_stop_timer = threading.Timer(
                AUDIO_MAX_SECONDS, stop_audio_capture, kwargs={"auto": True}
            )
            audio_stop_timer.daemon = True
            audio_stop_timer.start()

        logging.info("Audio capture started (hotkey=%s).", AUDIO_HOTKEY)


def stop_audio_capture(auto: bool = False) -> None:
    global audio_process, audio_output_path, audio_stop_timer
    with audio_lock:
        if audio_process is None:
            return
        process = audio_process
        audio_process = None
        if audio_stop_timer is not None:
            audio_stop_timer.cancel()
            audio_stop_timer = None

    if process.stdin:
        try:
            process.stdin.write(b"\n")
            process.stdin.flush()
        except Exception:
            logging.exception("Failed to signal audio helper.")
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()

    duration = time.perf_counter() - audio_start_time
    logging.info("Audio capture stopped (%.1fs).", duration)

    output_path = audio_output_path
    if not output_path or not Path(output_path).exists():
        logging.warning("Audio output missing; skipping pipeline.")
        return

    enqueue_request("audio", lambda: run_audio_pipeline(output_path))


def load_whisper_model() -> WhisperModel:
    global whisper_model
    if whisper_model is not None:
        return whisper_model
    device = WHISPER_DEVICE
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    whisper_model = WhisperModel(
        AUDIO_STT_MODEL,
        device=device,
        compute_type=WHISPER_COMPUTE_TYPE,
    )
    return whisper_model


def transcribe_audio(audio_path: str) -> list[dict]:
    model = load_whisper_model()
    segments, _info = model.transcribe(
        audio_path,
        language=WHISPER_LANGUAGE,
        vad_filter=True,
    )
    output: list[dict] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        output.append({"start": segment.start, "end": segment.end, "text": text})
    return output


def load_diarization_pipeline() -> Pipeline | None:
    global diarization_pipeline
    if diarization_pipeline is not None:
        return diarization_pipeline
    if not HF_TOKEN:
        logging.warning("HF_TOKEN not set; diarization disabled.")
        return None
    diarization_pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=HF_TOKEN,
    )
    return diarization_pipeline


def diarize_audio(audio_path: str) -> list[tuple[float, float, str]]:
    if not DIARIZATION_ENABLED:
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
    if DIARIZATION_TARGET:
        return DIARIZATION_TARGET
    totals: dict[str, float] = {}
    for start, end, speaker in diarization_segments:
        totals[speaker] = totals.get(speaker, 0.0) + (end - start)
    if not totals:
        return None
    return max(totals.items(), key=lambda item: item[1])[0]


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
    if len(question.split()) < AUDIO_QUESTION_MIN_WORDS:
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


def run_audio_pipeline(audio_path: str) -> None:
    if not processing_lock.acquire(blocking=False):
        logging.info("Pipeline already running; skipping audio run.")
        return

    start = time.perf_counter()
    try:
        logging.info("Transcribing audio (%s)…", AUDIO_STT_MODEL)
        transcript_segments = transcribe_audio(audio_path)
        if not transcript_segments:
            logging.info("Transcript empty; skipping response.")
            return

        diarization_segments = diarize_audio(audio_path)
        if diarization_segments:
            assign_speakers(transcript_segments, diarization_segments)
        target_speaker = pick_target_speaker(diarization_segments)
        transcript = build_transcript_for_speaker(transcript_segments, target_speaker)
        question = extract_question(transcript)
        if not question:
            logging.info("No question detected; skipping response.")
            return

        logging.info("Sending question to DashScope (%s)…", AUDIO_GPT_MODEL)
        answer = call_dashscope(AUDIO_GPT_MODEL, AUDIO_PROMPT, question)
        if not answer:
            logging.info("DashScope returned empty answer; skipping response.")
            return

        timestamp = datetime.now(timezone.utc).isoformat()
        post_payload = {
            "feedback": answer,
            "image": "",
            "timestamp": timestamp,
            "meta": {
                "mode": "audio",
                "model": AUDIO_GPT_MODEL,
                "hotkey": AUDIO_HOTKEY,
                "question": question,
                "speaker": target_speaker,
                "diarized": bool(diarization_segments),
                "latency_ms": int((time.perf_counter() - start) * 1000),
            },
        }

        logging.info("Posting audio answer to relay…")
        post_feedback(post_payload)
        logging.info("Audio answer delivered ✅")
    except requests.RequestException as err:
        logging.error("Failed to reach relay: %s", err)
    except Exception as err:  # pylint: disable=broad-except
        logging.exception("Audio pipeline crashed: %s", err)
    finally:
        try:
            if Path(audio_path).exists():
                Path(audio_path).unlink()
        except Exception:
            logging.exception("Failed to remove audio file.")
        processing_lock.release()


def enqueue_request(label: str, fn: Callable[[], None]) -> None:
    global next_request_at
    now = time.perf_counter()
    with queue_lock:
        if now < next_request_at:
            logging.info("Request cooldown active; dropping %s.", label)
            return
        next_request_at = now + REQUEST_COOLDOWN_SECONDS

    try:
        request_queue.put_nowait((label, fn))
        logging.info("Queued %s request (size=%d).", label, request_queue.qsize())
    except Full:
        logging.info("Request queue full; dropping %s.", label)


def request_worker() -> None:
    while True:
        label, fn = request_queue.get()
        try:
            logging.info("Processing %s request.", label)
            fn()
        finally:
            request_queue.task_done()


def post_feedback(payload: dict) -> None:
    url = f"{SERVER_URL.rstrip('/')}/api/feedback"
    res = http_session.post(url, json=payload, timeout=10)
    res.raise_for_status()


def run_pipeline(mode: ModeConfig):
    if not processing_lock.acquire(blocking=False):
        logging.info("Pipeline already running; ignoring duplicate hotkey.")
        return

    start = time.perf_counter()
    try:
        logging.info(
            "Capturing screen (mode=%s, hotkey=%s, model=%s)…",
            mode.name,
            mode.hotkey,
            mode.model,
        )
        image_bytes = capture_fullscreen_png()
        logging.info("Screen captured; %d KB", len(image_bytes) // 1024)

        b64_payload = base64.b64encode(image_bytes).decode("ascii")
        logging.info("Sending to OpenAI (%s)…", mode.model)
        feedback = call_openai(mode.model, mode.prompt, b64_payload)
        logging.info("OpenAI finished (%d chars).", len(feedback))

        timestamp = datetime.now(timezone.utc).isoformat()
        data_url = f"data:image/png;base64,{b64_payload}"

        post_payload = {
            "feedback": feedback,
            "image": data_url,
            "timestamp": timestamp,
            "meta": {
                "mode": mode.name,
                "model": mode.model,
                "hotkey": mode.hotkey,
                "latency_ms": int((time.perf_counter() - start) * 1000),
            },
        }

        logging.info("Posting feedback to relay…")
        post_feedback(post_payload)
        logging.info("Feedback delivered ✅")
    except requests.RequestException as err:
        logging.error("Failed to reach relay: %s", err)
    except Exception as err:  # pylint: disable=broad-except
        logging.exception("Pipeline crashed: %s", err)
    finally:
        processing_lock.release()


def main():
    threading.Thread(target=request_worker, daemon=True).start()
    logging.info("Interview agent armed.")
    logging.info("Relay endpoint: %s", SERVER_URL)
    for mode in modes:
        logging.info(
            "Mode '%s': hotkey=%s model=%s", mode.name, mode.hotkey, mode.model
        )
    if not DASHSCOPE_API_KEY:
        logging.warning("DASHSCOPE_API_KEY not set; audio Q&A will fail.")
    logging.info("Audio Q&A hotkey: %s (toggle capture)", AUDIO_HOTKEY)
    logging.info("Press Ctrl+C to exit.")

    for mode in modes:
        keyboard.add_hotkey(
            mode.hotkey,
            lambda m=mode: enqueue_request("image", lambda: run_pipeline(m)),
        )

    keyboard.add_hotkey(
        AUDIO_HOTKEY,
        lambda: stop_audio_capture(auto=False)
        if audio_process is not None
        else start_audio_capture(),
    )

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        logging.info("Exiting…")


if __name__ == "__main__":
    main()
