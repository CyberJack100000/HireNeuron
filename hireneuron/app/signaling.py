"""Secondary-camera pairing. A short-lived token links the candidate's desktop page with their phone;
the server only relays WebRTC signalling messages (offer/answer/ICE), it never sees the video."""
import secrets
import time

TTL = 30 * 60
_pairs: dict[str, dict] = {}


def create(interview_id: str) -> dict:
    for k in [k for k, v in _pairs.items() if v["expires"] < time.time()]:
        del _pairs[k]
    tok = secrets.token_urlsafe(12)
    _pairs[tok] = {"interview_id": interview_id, "expires": time.time() + TTL, "desktop": None, "phone": None, "phone_seen": False}
    return {"token": tok, "expires_in": TTL}


def get(tok: str) -> dict | None:
    p = _pairs.get(tok)
    return p if p and p["expires"] > time.time() else None


def end_for(interview_id: str):
    """The interview is over: phones still connected (now or on reconnect) are told to switch the camera off, and the page no longer accepts the pairing link."""
    for v in _pairs.values():
        if v["interview_id"] == interview_id:
            v["ended"] = True


def phone_connected(interview_id: str) -> bool:
    return any(v["interview_id"] == interview_id and v["phone"] is not None for v in _pairs.values())


def drop(tok: str):
    _pairs.pop(tok, None)
