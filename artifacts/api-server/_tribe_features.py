"""Per-moment video features for tailored critique.

Turns "engagement dropped at 2s" into "engagement dropped at 2s, right after a
hard cut into a static, faceless, dark shot with the caption 'BUY NOW'". Samples
the clip with OpenCV (motion, brightness, contrast, scene cuts), detects faces
(YuNet DNN) and people (HOG), reads on-screen text with Tesseract, and reads the
extracted WAV for per-second audio loudness.

Everything is cheap and runs on CPU in a few seconds for an 8 s clip. Runs under
the TRIBE venv (cv2 + pytesseract + soundfile).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

# Tunables (normalised 0-1 units unless noted).
CUT_THRESHOLD = 0.28
DARK = 0.22
BRIGHT = 0.82
STATIC = 0.045
LOW_CONTRAST = 0.10
QUIET = 0.02
SAMPLE_HZ = 5.0

CACHE = Path(os.environ.get("TRIBE_CACHE", Path.home() / "tribe-v2" / "cache"))
YUNET_PATH = os.environ.get("YUNET_PATH", str(CACHE / "face_detection_yunet.onnx"))


def _colorfulness(bgr: np.ndarray) -> float:
    (b, g, r) = bgr[:, :, 0].astype(np.float32), bgr[:, :, 1].astype(np.float32), bgr[:, :, 2].astype(np.float32)
    rg = np.abs(r - g)
    yb = np.abs(0.5 * (r + g) - b)
    return float((np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 255.0)


class _Detectors:
    """Lazily-built face (YuNet), person (HOG) and OCR (Tesseract) detectors."""

    def __init__(self) -> None:
        self._yunet = None
        self._hog = None

    def faces(self, frame: np.ndarray) -> int:
        import cv2

        h, w = frame.shape[:2]
        if self._yunet is None:
            if not Path(YUNET_PATH).exists():
                return 0
            self._yunet = cv2.FaceDetectorYN.create(
                YUNET_PATH, "", (w, h), score_threshold=0.7, nms_threshold=0.3, top_k=50
            )
        self._yunet.setInputSize((w, h))
        try:
            _, det = self._yunet.detect(frame)
        except Exception:
            return 0
        return 0 if det is None else int(len(det))

    def persons(self, frame: np.ndarray) -> int:
        import cv2

        if self._hog is None:
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        try:
            rects, _ = self._hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
        except Exception:
            return 0
        return int(len(rects))

    def ocr(self, frame: np.ndarray) -> str:
        try:
            import cv2
            import pytesseract

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            data = pytesseract.image_to_data(rgb, config="--psm 6", output_type=pytesseract.Output.DICT)
            words = [
                w.strip()
                for w, c in zip(data.get("text", []), data.get("conf", []))
                if str(c).replace("-", "").isdigit() and float(c) > 60 and len(w.strip()) >= 2
            ]
            text = " ".join(words).strip()
            return text[:160]
        except Exception:
            return ""


def _sample_frames(video_path: str, sample_hz: float = SAMPLE_HZ):
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = n / fps if n else 0.0
    if duration <= 0:
        cap.release()
        return []

    times = np.arange(0.0, duration, 1.0 / sample_hz)
    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        small = cv2.resize(frame, (128, 128))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        out.append({
            "t": float(t),
            "gray": gray.astype(np.float32) / 255.0,
            "brightness": float(gray.mean() / 255.0),
            "contrast": float(gray.std() / 255.0),
            "colorfulness": _colorfulness(small),
            "frame": frame,
        })
    cap.release()
    return out


def _audio_rms_per_second(wav_path: str | None, duration: float) -> list[float]:
    if not wav_path or not Path(wav_path).exists():
        return []
    try:
        import soundfile as sf

        data, sr = sf.read(wav_path, dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        per_sec = max(1, int(round(duration)))
        rms = []
        for s in range(per_sec):
            seg = mono[int(s * sr): int((s + 1) * sr)]
            rms.append(float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0)
        peak = max(rms) or 1.0
        return [min(1.0, r / peak) for r in rms]
    except Exception:
        return []


def _observations(brightness: float, contrast: float, motion: float, faces: int,
                  persons: int, cut: bool, audio: float | None, text: str) -> list[str]:
    obs: list[str] = []
    if cut:
        obs.append("hard cut / scene change")
    if motion < STATIC:
        obs.append("static shot (almost no movement)")
    elif motion > CUT_THRESHOLD:
        obs.append("fast motion")
    if brightness < DARK:
        obs.append("dark, low-exposure frame")
    elif brightness > BRIGHT:
        obs.append("very bright / blown-out frame")
    if contrast < LOW_CONTRAST:
        obs.append("flat, low-contrast image")
    if faces == 0 and persons == 0:
        obs.append("no person on screen")
    else:
        obs.append("person on screen")
    if text:
        obs.append(f'on-screen text: "{text}"')
    else:
        obs.append("no on-screen text / captions")
    if audio is not None and audio < QUIET:
        obs.append("quiet / no audio")
    return obs


def extract_features(video_path: str, wav_path: str | None = None,
                     timestamps: list[float] | None = None) -> dict:
    """Return per-second features + events + observations for a clip."""
    frames = _sample_frames(video_path)
    if not frames:
        return {"perSecond": [], "events": [], "observations": {}, "text": "", "available": False}

    det = _Detectors()

    prev_gray = None
    for f in frames:
        motion = 0.0
        if prev_gray is not None and prev_gray.shape == f["gray"].shape:
            motion = float(np.mean(np.abs(f["gray"] - prev_gray)))
        prev_gray = f["gray"]
        f["motion"] = motion

    duration = frames[-1]["t"] + 1.0 / SAMPLE_HZ
    audio_rms = _audio_rms_per_second(wav_path, duration)

    n_sec = max(1, int(np.ceil(duration)))
    per_second = []
    all_text: list[str] = []
    for s in range(n_sec):
        seg = [x for x in frames if s <= x["t"] < s + 1]
        if not seg:
            continue
        # Representative frame for the expensive detectors (closest to bin centre).
        rep = min(seg, key=lambda x: abs(x["t"] - (s + 0.5)))
        faces = det.faces(rep["frame"])
        persons = det.persons(rep["frame"])
        text = det.ocr(rep["frame"])
        if text:
            all_text.append(text)
        cut = any(x["motion"] > CUT_THRESHOLD for x in seg)
        per_second.append({
            "t": s,
            "brightness": round(float(np.mean([x["brightness"] for x in seg])), 3),
            "contrast": round(float(np.mean([x["contrast"] for x in seg])), 3),
            "motion": round(float(np.mean([x["motion"] for x in seg])), 3),
            "maxMotion": round(float(np.max([x["motion"] for x in seg])), 3),
            "faces": int(faces),
            "persons": int(persons),
            "cut": bool(cut),
            "text": text,
            "audio": round(audio_rms[s], 3) if s < len(audio_rms) else None,
        })

    events = [
        {"t": round(x["t"], 2), "type": "cut", "detail": "hard cut / scene change"}
        for x in frames if x["motion"] > CUT_THRESHOLD
    ]

    for sec in per_second:
        sec["observations"] = _observations(
            sec["brightness"], sec["contrast"], sec["motion"],
            sec["faces"], sec["persons"], sec["cut"], sec.get("audio"), sec["text"],
        )

    observations = {str(sec["t"]): sec["observations"] for sec in per_second}
    # De-duplicated on-screen text across the clip (for the critique).
    seen, unique_text = set(), []
    for t in all_text:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique_text.append(t)

    return {
        "perSecond": per_second,
        "events": events,
        "observations": observations,
        "text": " | ".join(unique_text)[:400],
        "available": True,
    }
