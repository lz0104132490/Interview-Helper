import base64
import logging
import time
from datetime import datetime, timezone

import mss
import mss.tools
import requests

import clients
import locks


def capture_fullscreen_png() -> bytes:
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        shot = sct.grab(monitor)
        return mss.tools.to_png(shot.rgb, shot.size)


def run_pipeline(mode):
    if not locks.processing_lock.acquire(blocking=False):
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
        feedback = clients.call_openai(mode.model, mode.prompt, b64_payload)
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
        clients.post_feedback(post_payload)
        logging.info("Feedback delivered ✅")
    except requests.RequestException as err:
        logging.error("Failed to reach relay: %s", err)
    except Exception as err:  # pylint: disable=broad-except
        logging.exception("Pipeline crashed: %s", err)
    finally:
        locks.processing_lock.release()
