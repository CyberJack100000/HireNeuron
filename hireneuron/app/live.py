"""Live call rooms. The server only relays WebRTC signalling between the candidate's page and any number of
recruiters; audio and video flow peer-to-peer and never touch the server."""
import secrets
from dataclasses import dataclass

from fastapi import WebSocket

MAX_MESSAGE = 65536


@dataclass
class Peer:
    id: str
    role: str
    ws: WebSocket


rooms: dict[str, dict[str, Peer]] = {}


def present(iid: str, role: str) -> int:
    return sum(1 for p in rooms.get(iid, {}).values() if p.role == role)


async def _safe_send(peer: Peer, msg: dict):
    try:
        await peer.ws.send_json(msg)
    except Exception:
        pass


async def join(iid: str, role: str, ws: WebSocket) -> Peer:
    room = rooms.setdefault(iid, {})
    if role == "candidate":                      # a reload replaces the old candidate connection
        for old in [p for p in room.values() if p.role == "candidate"]:
            room.pop(old.id, None)
            try:
                await old.ws.close(code=4409)
            except Exception:
                pass
            for p in room.values():
                await _safe_send(p, {"type": "peer-left", "id": old.id})
    peer = Peer(secrets.token_hex(4), role, ws)
    room[peer.id] = peer
    await _safe_send(peer, {"type": "hello", "id": peer.id, "peers": [{"id": p.id, "role": p.role} for p in room.values() if p.id != peer.id]})
    for p in list(room.values()):
        if p.id != peer.id:
            await _safe_send(p, {"type": "peer-joined", "id": peer.id, "role": role})
    return peer


async def relay(iid: str, sender: Peer, msg: dict):
    target = rooms.get(iid, {}).get(str(msg.get("to")))
    if target is None or target.role == sender.role:          # candidate <-> recruiter only
        return
    msg.pop("to", None)
    await _safe_send(target, {**msg, "from": sender.id})


async def leave(iid: str, peer: Peer):
    room = rooms.get(iid)
    if not room or room.get(peer.id) is not peer:
        return False
    room.pop(peer.id, None)
    for p in list(room.values()):
        await _safe_send(p, {"type": "peer-left", "id": peer.id})
    if not room:
        rooms.pop(iid, None)
    return True
