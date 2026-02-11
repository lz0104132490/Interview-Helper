import logging

import requests
from openai import OpenAI

import config

client = OpenAI(api_key=config.OPENAI_API_KEY)
http_session = requests.Session()


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
    if not config.DASHSCOPE_API_KEY:
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
        "Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    res = http_session.post(
        config.DASHSCOPE_ENDPOINT, json=payload, headers=headers, timeout=30
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


def post_feedback(payload: dict) -> None:
    url = f"{config.SERVER_URL.rstrip('/')}/api/feedback"
    res = http_session.post(url, json=payload, timeout=10)
    res.raise_for_status()


def post_control(action: str, delta: int) -> None:
    url = f"{config.SERVER_URL.rstrip('/')}/api/control"
    payload = {"action": action, "delta": delta}
    res = http_session.post(url, json=payload, timeout=5)
    res.raise_for_status()
