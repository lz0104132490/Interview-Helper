import logging
import threading
import time
from queue import Queue, Full
from typing import Callable

import config

request_queue: Queue[tuple[str, Callable[[], None]]] = Queue(
    maxsize=config.MAX_QUEUE_SIZE
)
next_request_at = 0.0
queue_lock = threading.Lock()


def enqueue_request(label: str, fn: Callable[[], None]) -> None:
    global next_request_at
    now = time.perf_counter()
    with queue_lock:
        if now < next_request_at:
            logging.info("Request cooldown active; dropping %s.", label)
            return
        next_request_at = now + config.REQUEST_COOLDOWN_SECONDS

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
