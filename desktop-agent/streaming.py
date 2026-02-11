import logging
import logging
import threading
import time
from datetime import datetime, timezone
from queue import Queue, Full, Empty

import numpy as np
import requests
import sounddevice as sd

import clients
import config
import questions
import whisper_utils

stream_thread: threading.Thread | None = None
stream_stop_event: threading.Event | None = None
stream_lock = threading.Lock()
stream_state_lock = threading.Lock()
stream_transcript_window = ""
stream_last_question = ""
stream_last_answer_at = 0.0
stream_answer_queue: Queue[str] | None = None


def find_loopback_device() -> int | None:
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        return None

    wasapi_index = None
    for index, api in enumerate(hostapis):
        if str(api.get("name", "")).lower() == "windows wasapi":
            wasapi_index = index
            break

    if wasapi_index is None:
        return None

    hostapi = hostapis[wasapi_index]
    device_ids = hostapi.get("devices", [])
    if config.STREAM_LOOPBACK_DEVICE_NAME:
        for device_id in device_ids:
            info = sd.query_devices(device_id)
            if config.STREAM_LOOPBACK_DEVICE_NAME.lower() in info["name"].lower():
                return device_id

    default_output = hostapi.get("default_output_device")
    if isinstance(default_output, int) and default_output >= 0:
        return default_output

    output_device = sd.default.device[1]
    if output_device is None:
        return None
    return output_device


def open_loopback_stream(callback):
    try:
        extra_settings = sd.WasapiSettings(loopback=True)
    except Exception:
        logging.error(
            "WASAPI loopback is not available on this system. "
            "Ensure you are running on Windows with WASAPI support."
        )
        return None, None

    device_id = find_loopback_device()
    if device_id is None:
        logging.error(
            "No WASAPI loopback device found. "
            "Set STREAM_LOOPBACK_DEVICE_NAME to match your output device."
        )
        return None, None

    device_info = sd.query_devices(device_id)
    device_rate = int(
        device_info.get("default_samplerate") or config.STREAM_SAMPLE_RATE
    )
    channels = max(1, min(int(device_info.get("max_output_channels") or 2), 2))

    stream = sd.InputStream(
        device=device_id,
        channels=channels,
        samplerate=device_rate,
        callback=callback,
        extra_settings=extra_settings,
    )
    return stream, device_rate


def transcribe_stream_loopback(
    stop_event: threading.Event,
    audio_queue: Queue[np.ndarray],
    sample_rate: int,
) -> None:
    model = whisper_utils.load_stream_whisper_model()
    buffer = np.zeros((0,), dtype=np.float32)
    min_segment_samples = int(
        config.STREAM_SAMPLE_RATE * config.STREAM_MIN_SEGMENT_SECONDS
    )
    max_segment_samples = int(
        config.STREAM_SAMPLE_RATE * config.STREAM_MAX_SEGMENT_SECONDS
    )
    silence_samples = int(
        config.STREAM_SAMPLE_RATE * (config.STREAM_SILENCE_MS / 1000.0)
    )
    trailing_silence = 0
    answer_queue: Queue[str] = Queue(maxsize=1)

    global stream_answer_queue
    with stream_state_lock:
        stream_answer_queue = answer_queue
        global stream_transcript_window, stream_last_question, stream_last_answer_at
        stream_transcript_window = ""
        stream_last_question = ""
        stream_last_answer_at = 0.0

    def answer_worker():
        while not stop_event.is_set():
            try:
                question = answer_queue.get(timeout=0.2)
            except Empty:
                continue

            if not question:
                answer_queue.task_done()
                continue

            try:
                logging.info(
                    "Sending stream question to DashScope (%s)…",
                    config.STREAM_GPT_MODEL,
                )
                answer = clients.call_dashscope(
                    config.STREAM_GPT_MODEL, config.STREAM_PROMPT, question
                )
                if not answer:
                    logging.info("DashScope returned empty answer for stream question.")
                    answer_queue.task_done()
                    continue

                timestamp = datetime.now(timezone.utc).isoformat()
                post_payload = {
                    "feedback": answer,
                    "image": "",
                    "timestamp": timestamp,
                    "meta": {
                        "mode": "audio",
                        "source": "loopback_stream",
                        "model": config.STREAM_GPT_MODEL,
                        "question": question,
                    },
                }
                clients.post_feedback(post_payload)
                logging.info("Stream answer delivered ✅")
            except requests.RequestException as err:
                logging.error("Failed to reach relay for stream answer: %s", err)
            except Exception as err:  # pylint: disable=broad-except
                logging.exception("Stream answer pipeline crashed: %s", err)
            finally:
                answer_queue.task_done()

    thread = threading.Thread(target=answer_worker, daemon=True)
    thread.start()

    while not stop_event.is_set():
        try:
            chunk = audio_queue.get(timeout=0.2)
        except Empty:
            continue

        mono = whisper_utils.to_mono(chunk)
        if sample_rate != config.STREAM_SAMPLE_RATE:
            mono = whisper_utils.resample_audio(
                mono, sample_rate, config.STREAM_SAMPLE_RATE
            )
        if mono.size == 0:
            continue

        buffer = np.concatenate([buffer, mono])

        rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
        if rms < config.STREAM_SILENCE_THRESHOLD:
            trailing_silence += mono.size
        else:
            trailing_silence = 0

        should_cut = (
            buffer.size >= min_segment_samples and trailing_silence >= silence_samples
        )
        force_cut = buffer.size >= max_segment_samples

        while buffer.size >= min_segment_samples and (should_cut or force_cut):
            if trailing_silence > 0:
                cut_point = max(buffer.size - trailing_silence, min_segment_samples)
            else:
                cut_point = min(buffer.size, max_segment_samples)

            # Cut on silence boundaries to avoid mid-syllable splits.
            audio = buffer[:cut_point]
            buffer = buffer[cut_point:]
            trailing_silence = 0

            segments, _info = model.transcribe(
                audio,
                language=config.STREAM_LANGUAGE,
                task="transcribe",
                beam_size=config.STREAM_BEAM_SIZE,
            )

            for segment in segments:
                text = (segment.text or "").strip()
                if text:
                    logging.info(
                        "Recruiter [%0.1f-%0.1f] %s", segment.start, segment.end, text
                    )
                    with stream_state_lock:
                        global stream_transcript_window
                        stream_transcript_window = (
                            f"{stream_transcript_window} {text}".strip()
                        )

            with stream_state_lock:
                transcript_window = stream_transcript_window

            if transcript_window and not config.STREAM_MANUAL_ONLY:
                question = questions.extract_question(transcript_window)
                now = time.perf_counter()
                if question and try_queue_question(question, now):
                    with stream_state_lock:
                        stream_transcript_window = ""


def stream_loopback_worker(stop_event: threading.Event) -> None:
    audio_queue: Queue[np.ndarray] = Queue(maxsize=50)

    def stream_callback(indata, frames, time_info, status):
        if status:
            logging.warning("Stream audio status: %s", status)
        try:
            audio_queue.put_nowait(indata.copy())
        except Full:
            pass

    stream, stream_rate = open_loopback_stream(stream_callback)
    if stream is None or stream_rate is None:
        return

    transcriber = threading.Thread(
        target=transcribe_stream_loopback,
        args=(stop_event, audio_queue, stream_rate),
        daemon=True,
    )
    transcriber.start()

    try:
        with stream:
            logging.info("Loopback streaming started.")
            while not stop_event.is_set():
                sd.sleep(200)
    finally:
        logging.info("Loopback streaming stopped.")
        stop_event.set()
        transcriber.join(timeout=2)
        with stream_state_lock:
            global stream_answer_queue, stream_transcript_window
            stream_answer_queue = None
            stream_transcript_window = ""


def toggle_streaming_loopback() -> None:
    global stream_thread, stream_stop_event
    with stream_lock:
        if stream_thread is not None and stream_thread.is_alive():
            if stream_stop_event is not None:
                stream_stop_event.set()
                logging.info("Stopping loopback stream...")
            return

        stream_stop_event = threading.Event()
        stream_thread = threading.Thread(
            target=stream_loopback_worker,
            args=(stream_stop_event,),
            daemon=True,
        )
        stream_thread.start()
        logging.info("Starting loopback stream...")


def try_queue_question(question: str, now: float, manual: bool = False) -> bool:
    global stream_last_question, stream_last_answer_at
    if not question:
        return False
    with stream_state_lock:
        if stream_answer_queue is None:
            return False
        if question == stream_last_question:
            return False
        if not manual and (
            now - stream_last_answer_at < config.STREAM_MIN_SECONDS_BETWEEN_ANSWERS
        ):
            return False
        try:
            stream_answer_queue.put_nowait(question)
        except Full:
            return False
        stream_last_question = question
        stream_last_answer_at = now
    return True


def send_stream_question() -> None:
    with stream_state_lock:
        transcript = stream_transcript_window.strip()
    if not transcript:
        logging.info("No transcript available to send.")
        return
    question = questions.extract_question(transcript)
    if not question:
        logging.info("No question detected in transcript; not sending.")
        return
    now = time.perf_counter()
    if try_queue_question(question, now, manual=True):
        with stream_state_lock:
            global stream_transcript_window
            stream_transcript_window = ""
        logging.info("Queued stream question via hotkey.")
    else:
        logging.info("Stream question not queued (cooldown or duplicate).")
