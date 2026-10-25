"""Downloads MediaPipe Face Landmarker (library + model) into web/vendor so the interview page needs no CDN.
Cross-platform, standard library only. Run once from the hireneuron folder:   python scripts/fetch_vision.py
(about 22 MB; needs internet access)
"""
import io
import tarfile
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "web" / "vendor"
PKG = "https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-0.10.14.tgz"
MODEL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"


def get(url: str) -> bytes:
    print("Downloading", url)
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def main():
    (VENDOR / "wasm").mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(get(PKG)), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = m.name.removeprefix("package/")
            if m.isfile() and (name == "vision_bundle.mjs" or name.startswith("wasm/")):
                (VENDOR / name).write_bytes(tar.extractfile(m).read())
    (VENDOR / "face_landmarker.task").write_bytes(get(MODEL))
    print("Done. Vision files are in", VENDOR)


if __name__ == "__main__":
    main()
