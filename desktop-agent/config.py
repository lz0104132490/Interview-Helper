import os

from dotenv import load_dotenv

load_dotenv()


def env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


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

AUDIO_MAX_SECONDS = env_int("AUDIO_MAX_SECONDS", 20)
AUDIO_QUESTION_MIN_WORDS = env_int("AUDIO_QUESTION_MIN_WORDS", 6)

WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "zh")

STREAM_HOTKEY = os.getenv("STREAM_HOTKEY", "alt+w")
STREAM_DEVICE = os.getenv("STREAM_DEVICE", "cuda")
STREAM_WHISPER_MODEL = os.getenv("STREAM_WHISPER_MODEL", "large-v3")
STREAM_COMPUTE_TYPE = os.getenv("STREAM_COMPUTE_TYPE", "int8_float16")
STREAM_LANGUAGE = os.getenv("STREAM_LANGUAGE", "zh")
STREAM_LOOPBACK_DEVICE_NAME = os.getenv("STREAM_LOOPBACK_DEVICE_NAME")
STREAM_CHUNK_SECONDS = env_float("STREAM_CHUNK_SECONDS", 3.0)
STREAM_BEAM_SIZE = env_int("STREAM_BEAM_SIZE", 2)
STREAM_SAMPLE_RATE = env_int("STREAM_SAMPLE_RATE", 16000)
STREAM_PROMPT = os.getenv(
    "STREAM_PROMPT",
    "Answer the interviewer's question clearly and concisely.",
)
STREAM_GPT_MODEL = os.getenv("STREAM_GPT_MODEL", AUDIO_GPT_MODEL)
STREAM_MIN_SECONDS_BETWEEN_ANSWERS = env_float(
    "STREAM_MIN_SECONDS_BETWEEN_ANSWERS", 8.0
)
STREAM_SEND_HOTKEY = os.getenv("STREAM_SEND_HOTKEY", "alt+e")
STREAM_MANUAL_ONLY = os.getenv("STREAM_MANUAL_ONLY", "false").lower() in (
    "1",
    "true",
    "yes",
    "y",
    "on",
)
STREAM_SILENCE_THRESHOLD = env_float("STREAM_SILENCE_THRESHOLD", 0.015)
STREAM_SILENCE_MS = env_int("STREAM_SILENCE_MS", 700)
STREAM_MIN_SEGMENT_SECONDS = env_float("STREAM_MIN_SEGMENT_SECONDS", 1.2)
STREAM_MAX_SEGMENT_SECONDS = env_float("STREAM_MAX_SEGMENT_SECONDS", 8.0)

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_ENDPOINT = os.getenv(
    "DASHSCOPE_ENDPOINT",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
)

REQUEST_COOLDOWN_SECONDS = float(os.getenv("REQUEST_COOLDOWN_SECONDS", "5"))
MAX_QUEUE_SIZE = env_int("MAX_QUEUE_SIZE", 3)

CONTROL_SCROLL_HOTKEY = os.getenv("CONTROL_SCROLL_HOTKEY", "ctrl+alt+down")
CONTROL_SCROLL_DELTA = env_int("CONTROL_SCROLL_DELTA", 400)
CONTROL_SCROLL_UP_HOTKEY = os.getenv("CONTROL_SCROLL_UP_HOTKEY", "ctrl+alt+up")
