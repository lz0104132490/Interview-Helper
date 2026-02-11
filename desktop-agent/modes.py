import os
from dataclasses import dataclass

import config


@dataclass
class ModeConfig:
    name: str
    hotkey: str
    model: str
    prompt: str


def load_modes() -> list[ModeConfig]:
    modes: list[ModeConfig] = []

    primary_hotkey = os.getenv("PRIMARY_HOTKEY", config.DEFAULT_HOTKEY)
    primary_model = os.getenv("PRIMARY_MODEL", config.DEFAULT_MODEL)
    primary_prompt = os.getenv("PRIMARY_PROMPT", config.DEFAULT_PROMPT)
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
