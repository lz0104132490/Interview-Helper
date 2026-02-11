import logging
import sys
import threading

import keyboard

import audio_record
import clients
import config
import image_pipeline
import modes
import queueing
import streaming


def main() -> None:
    if not config.OPENAI_API_KEY:
        print(
            "OPENAI_API_KEY missing. Copy env.sample -> .env and fill the key.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    threading.Thread(target=queueing.request_worker, daemon=True).start()

    loaded_modes = modes.load_modes()
    logging.info("Interview agent armed.")
    logging.info("Relay endpoint: %s", config.SERVER_URL)
    for mode in loaded_modes:
        logging.info(
            "Mode '%s': hotkey=%s model=%s", mode.name, mode.hotkey, mode.model
        )
    if not config.DASHSCOPE_API_KEY:
        logging.warning("DASHSCOPE_API_KEY not set; audio Q&A will fail.")
    logging.info("Audio Q&A hotkey: %s (toggle capture)", config.AUDIO_HOTKEY)
    logging.info("Loopback stream hotkey: %s (toggle on/off)", config.STREAM_HOTKEY)
    logging.info(
        "Loopback send hotkey: %s (send transcript)", config.STREAM_SEND_HOTKEY
    )
    logging.info(
        "Scroll hotkey: %s (delta=%d)",
        config.CONTROL_SCROLL_HOTKEY,
        config.CONTROL_SCROLL_DELTA,
    )
    logging.info(
        "Scroll up hotkey: %s (delta=-%d)",
        config.CONTROL_SCROLL_UP_HOTKEY,
        config.CONTROL_SCROLL_DELTA,
    )
    logging.info("Press Ctrl+C to exit.")

    for mode in loaded_modes:
        keyboard.add_hotkey(
            mode.hotkey,
            lambda m=mode: queueing.enqueue_request(
                "image", lambda: image_pipeline.run_pipeline(m)
            ),
        )

    keyboard.add_hotkey(
        config.AUDIO_HOTKEY,
        lambda: audio_record.stop_audio_capture(auto=False)
        if audio_record.is_recording()
        else audio_record.start_audio_capture(),
    )

    keyboard.add_hotkey(config.STREAM_HOTKEY, streaming.toggle_streaming_loopback)
    keyboard.add_hotkey(config.STREAM_SEND_HOTKEY, streaming.send_stream_question)
    keyboard.add_hotkey(
        config.CONTROL_SCROLL_HOTKEY,
        lambda: threading.Thread(
            target=clients.post_control,
            args=("scroll", config.CONTROL_SCROLL_DELTA),
            daemon=True,
        ).start(),
    )
    keyboard.add_hotkey(
        config.CONTROL_SCROLL_UP_HOTKEY,
        lambda: threading.Thread(
            target=clients.post_control,
            args=("scroll", -config.CONTROL_SCROLL_DELTA),
            daemon=True,
        ).start(),
    )

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        logging.info("Exiting…")


if __name__ == "__main__":
    main()
