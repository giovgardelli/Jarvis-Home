import os


def identify_speaker(chat_id: str) -> str | None:
    mapping = {
        os.environ.get("GIO_CHAT_ID", ""): "gio",
        os.environ.get("KENZ_CHAT_ID", ""): "kenz",
    }
    return mapping.get(chat_id)


def get_chat_id(speaker: str) -> str | None:
    mapping = {
        "gio": os.environ.get("GIO_CHAT_ID"),
        "kenz": os.environ.get("KENZ_CHAT_ID"),
    }
    return mapping.get(speaker)
