import logging
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
import sounddevice as sd

import clients
import config
import diarization
import locks
import questions
import queueing
import whisper_utils

audio_lock = threading.Lock()
audio_stream: sd.InputStream | None = None
audio_wave_file: wave.Wave_write | None = None
audio_sample_rate = 0
audio_channels = 0
audio_output_path: str | None = None
audio_start_time = 0.0
audio_stop_timer: threading.Timer | None = None


def is_recording() -> bool:
    return audio_stream is not None


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


def open_loopback_record_stream(callback):
    try:
        extra_settings = sd.WasapiSettings(loopback=True)
    except Exception:
        logging.error(
            "WASAPI loopback is not available on this system. "
            "Ensure you are running on Windows with WASAPI support."
        )
        return None, 0, 0

    device_id = find_loopback_device()
    if device_id is None:
        logging.error(
            "No WASAPI loopback device found. "
            "Set STREAM_LOOPBACK_DEVICE_NAME to match your output device."
        )
        return None, 0, 0

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
        dtype="int16",
    )
    return stream, device_rate, channels


def start_audio_capture() -> None:
    global audio_stream, audio_output_path, audio_start_time, audio_stop_timer
    global audio_wave_file, audio_sample_rate, audio_channels
    with audio_lock:
        if audio_stream is not None:
            logging.info("Audio capture already running.")
            return

        temp_file = NamedTemporaryFile(delete=False, suffix=".wav")
        audio_output_path = temp_file.name
        temp_file.close()

        def stream_callback(indata, frames, time_info, status):
            if status:
                logging.warning("Audio record status: %s", status)
            wave_file = audio_wave_file
            if wave_file is not None:
                wave_file.writeframes(indata.tobytes())

        stream, sample_rate, channels = open_loopback_record_stream(stream_callback)
        if stream is None:
            logging.error("Failed to start loopback recording.")
            return

        audio_sample_rate = sample_rate
        audio_channels = channels
        audio_wave_file = wave.open(audio_output_path, "wb")
        audio_wave_file.setnchannels(audio_channels)
        audio_wave_file.setsampwidth(2)
        audio_wave_file.setframerate(audio_sample_rate)
        stream.start()
        audio_stream = stream
        audio_start_time = time.perf_counter()

        if config.AUDIO_MAX_SECONDS > 0:
            audio_stop_timer = threading.Timer(
                config.AUDIO_MAX_SECONDS, stop_audio_capture, kwargs={"auto": True}
            )
            audio_stop_timer.daemon = True
            audio_stop_timer.start()

        logging.info("Audio capture started (hotkey=%s).", config.AUDIO_HOTKEY)


def stop_audio_capture(auto: bool = False) -> None:
    global audio_stream, audio_output_path, audio_stop_timer
    global audio_wave_file, audio_sample_rate, audio_channels
    with audio_lock:
        if audio_stream is None:
            return
        stream = audio_stream
        audio_stream = None
        if audio_stop_timer is not None:
            audio_stop_timer.cancel()
            audio_stop_timer = None

    try:
        stream.stop()
        stream.close()
    except Exception:
        logging.exception("Failed to stop loopback stream.")

    try:
        if audio_wave_file is not None:
            audio_wave_file.close()
    except Exception:
        logging.exception("Failed to finalize WAV file.")
    finally:
        audio_wave_file = None
        audio_sample_rate = 0
        audio_channels = 0

    duration = time.perf_counter() - audio_start_time
    logging.info("Audio capture stopped (%.1fs).", duration)

    output_path = audio_output_path
    if not output_path or not Path(output_path).exists():
        logging.warning("Audio output missing; skipping pipeline.")
        return

    queueing.enqueue_request("audio", lambda: run_audio_pipeline(output_path))


def transcribe_audio(audio_path: str) -> list[dict]:
    model = whisper_utils.load_whisper_model()
    segments, _info = model.transcribe(
        audio_path,
        language=config.WHISPER_LANGUAGE,
        vad_filter=True,
    )
    output: list[dict] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        output.append({"start": segment.start, "end": segment.end, "text": text})
    return output


def run_audio_pipeline(audio_path: str) -> None:
    if not locks.processing_lock.acquire(blocking=False):
        logging.info("Pipeline already running; skipping audio run.")
        return
    start = time.perf_counter()
    try:
        logging.info("Transcribing audio (%s)…", config.AUDIO_STT_MODEL)
        transcript_segments = transcribe_audio(audio_path)
        if not transcript_segments:
            logging.info("Transcript empty; skipping response.")
            return

        diarization_segments = diarization.diarize_audio(audio_path)
        if diarization_segments:
            diarization.assign_speakers(transcript_segments, diarization_segments)
        target_speaker = diarization.pick_target_speaker(diarization_segments)
        transcript = questions.build_transcript_for_speaker(
            transcript_segments, target_speaker
        )
        question = questions.extract_question(transcript)
        if not question:
            logging.info("No question detected; skipping response.")
            return

        logging.info("Sending question to DashScope (%s)…", config.AUDIO_GPT_MODEL)
        answer = clients.call_dashscope(
            config.AUDIO_GPT_MODEL, config.AUDIO_PROMPT, question
        )
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
                "model": config.AUDIO_GPT_MODEL,
                "hotkey": config.AUDIO_HOTKEY,
                "question": question,
                "speaker": target_speaker,
                "diarized": bool(diarization_segments),
                "latency_ms": int((time.perf_counter() - start) * 1000),
            },
        }

        logging.info("Posting audio answer to relay…")
        clients.post_feedback(post_payload)
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
        locks.processing_lock.release()
