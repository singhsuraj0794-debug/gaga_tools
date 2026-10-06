#!/usr/bin/env python
"""Meta Mind Map — TRIBE v2 analysis CLI (local CPU).

Pipeline:
  1. normalise/trim the clip (ffmpeg) to <= max seconds
  2. TRIBE v2 predict (video + audio; text is auto-dropped when no word events)
  3. map fsaverage5 vertices -> content dimensions, compute works score + flags
  4. render cortical frames per timestep (left/right)
  5. write result.json + progress.json into the output dir

Invoked by the Node API with the dedicated TRIBE venv interpreter:
    TRIBE_PYTHON=~/tribe-v2/venv/bin/python _tribe_analyze.py <video> <outdir> ...

Progress is written to <outdir>/progress.json as
    {"stage": str, "pct": int, "message": str, "done": bool, "error": str|null}
so the API can stream it over SSE without parsing noisy model logs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CACHE = Path(os.environ.get("TRIBE_CACHE", Path.home() / "tribe-v2" / "cache"))

DEFAULT_MAX_SECONDS = float(os.environ.get("TRIBE_MAX_SECONDS", "8"))


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


class Progress:
    def __init__(self, outdir: Path):
        self.path = outdir / "progress.json"
        self.done = False
        self.error: str | None = None

    def write(self, stage: str, pct: int, message: str = "") -> None:
        payload = {
            "stage": stage,
            "pct": int(max(0, min(100, pct))),
            "message": message,
            "done": self.done,
            "error": self.error,
            "ts": time.time(),
        }
        try:
            self.path.write_text(json.dumps(payload))
        except OSError:
            pass
        print(f"@@TRIBE {stage} {pct} {message}", flush=True)

    def finish(self) -> None:
        self.done = True
        self.write("done", 100, "complete")

    def fail(self, message: str) -> None:
        self.error = message
        self.done = True
        payload = {
            "stage": "error", "pct": 100, "message": message,
            "done": True, "error": message, "ts": time.time(),
        }
        try:
            self.path.write_text(json.dumps(payload))
        except OSError:
            pass
        print(f"@@TRIBE error 100 {message}", flush=True)


def normalize_clip(src: str, dst: Path, max_seconds: float) -> float:
    """Trim/normalise to a small mp4. Returns the resulting duration (seconds)."""
    ff = _ffmpeg()
    vf = (
        "scale='if(gt(iw,ih),256,-2)':'if(gt(iw,ih),-2,256)',"
        "pad=ceil(iw/2)*2:ceil(ih/2)*2"
    )
    cmd = [
        ff, "-y", "-i", src, "-t", str(max_seconds),
        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    # duration via ffmpeg stderr
    info = subprocess.run([ff, "-i", str(dst)], capture_output=True, text=True)
    dur = 0.0
    for line in info.stderr.splitlines():
        if "Duration:" in line:
            hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
            dur = int(hh) * 3600 + int(mm) * 60 + float(ss)
            break
    return dur


def load_model(device: str):
    from tribev2.demo_utils import TribeModel

    cfg_dev = device
    if device == "mps":
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        cfg_dev = "cuda"  # remap handled by caller-side patch if ever used
    config_update = {
        "data.text_feature.device": cfg_dev,
        "data.audio_feature.device": cfg_dev,
        "data.video_feature.image.device": cfg_dev,
        "data.image_feature.image.device": cfg_dev,
    }
    return TribeModel.from_pretrained(
        "facebook/tribev2",
        cache_folder=str(CACHE),
        device=device,
        config_update=config_update,
    )


def render_frames(preds, outdir: Path, max_frames: int = 16) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from tribev2.plotting import PlotBrain

    pb = PlotBrain(mesh="fsaverage5")
    names: list[str] = []
    n = preds.shape[0]
    step = max(1, int((n + max_frames - 1) // max_frames))
    for i in range(0, n, step):
        fig, axs = plt.subplots(1, 2, figsize=(10, 4))
        pb.plot_surf(preds[i], axes=list(axs), views=["left", "right"], norm_percentile=95)
        for ax, lbl in zip(axs, ("Left", "Right")):
            ax.set_title(f"t = {i}s  ·  {lbl}", fontsize=10)
        name = f"frame_{i:03d}.png"
        fig.savefig(outdir / name, dpi=90, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        names.append(name)
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("outdir")
    ap.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    ap.add_argument("--device", default=os.environ.get("TRIBE_DEVICE", "cpu"))
    ap.add_argument("--max-frames", type=int, default=16)
    ap.add_argument("--preds-npy", default=None, help="dev: reuse saved predictions")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prog = Progress(outdir)

    try:
        if args.device == "mps":
            # MPS is unstable on this machine; refuse rather than risk an OOM crash.
            raise RuntimeError("MPS is disabled (crashed the host). Use --device cpu.")

        prog.write("normalize", 3, "Preparing clip")
        clip = outdir / "clip.mp4"
        dur = normalize_clip(args.video, clip, args.max_seconds)

        import numpy as np

        if args.preds_npy:
            prog.write("predict", 75, f"[dev] using saved predictions {args.preds_npy}")
            preds = np.load(args.preds_npy)
        else:
            prog.write("load", 8, "Loading TRIBE v2")
            t0 = time.time()
            model = load_model(args.device)
            prog.write("load", 12, f"Model ready in {time.time()-t0:.0f}s")

            import pandas as pd
            from tribev2.demo_utils import get_audio_and_text_events

            prog.write("events", 15, "Extracting audio/video events")
            event = {
                "type": "Video", "filepath": str(clip), "start": 0,
                "timeline": "default", "subject": "default",
            }
            events = get_audio_and_text_events(pd.DataFrame([event]), audio_only=True)

            prog.write("predict", 20, "Predicting brain responses (CPU, this is slow)")
            t0 = time.time()
            preds, _segments = model.predict(events, verbose=False)
            preds = np.asarray(preds)
            prog.write(
                "predict", 75,
                f"Predicted {preds.shape[0]} timesteps in {time.time()-t0:.0f}s",
            )

        prog.write("features", 78, "Analyzing video content (cuts, motion, faces, audio)")
        from _tribe_features import extract_features

        wav = outdir / "clip.wav"
        try:
            features = extract_features(str(clip), str(wav) if wav.exists() else None)
        except Exception as fe:
            print(f"@@TRIBE features 78 feature extraction failed: {fe}", flush=True)
            features = None

        prog.write("score", 80, "Scoring dimensions")
        from _tribe_regions import compute_analysis

        ts = np.arange(preds.shape[0], dtype=float)
        analysis = compute_analysis(preds, ts_seconds=ts, features=features)

        prog.write("render", 85, "Rendering cortical frames")
        frames = render_frames(preds, outdir, max_frames=args.max_frames)

        result = {
            "durationSec": round(dur, 2),
            "clipFileName": "clip.mp4",
            "frames": frames,
            "device": args.device,
            **analysis,
        }
        (outdir / "result.json").write_text(json.dumps(result))
        prog.finish()
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        prog.fail(f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
