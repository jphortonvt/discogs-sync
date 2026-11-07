from __future__ import annotations

import keyring

SERVICE = "discogs-sync"


def set_token(provider: str, name: str, value: str) -> None:
    keyring.set_password(SERVICE, f"{provider}:{name}", value)


def get_token(provider: str, name: str) -> str | None:
    return keyring.get_password(SERVICE, f"{provider}:{name}")


def delete_token(provider: str, name: str) -> None:
    try:
        keyring.delete_password(SERVICE, f"{provider}:{name}")
    except keyring.errors.PasswordDeleteError:
        pass
