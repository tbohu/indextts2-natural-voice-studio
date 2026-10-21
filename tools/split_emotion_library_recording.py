#!/usr/bin/env python3
import argparse
import hashlib
import json
import wave
from datetime import datetime
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    sequence = plan["recording_sequence"]
    starts = plan["starts_seconds"]
    if len(sequence) != 64 or sorted(sequence) != list(range(1, 65)):
        raise ValueError("recording_sequence must contain each number from 1 to 64 once")
    if len(starts) != 64 or starts != sorted(starts):
        raise ValueError("starts_seconds must contain 64 increasing values")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest = {
        "source": str(args.source.resolve()),
        "source_sha256": sha256(args.source),
        "alignment_method": plan["alignment_method"],
        "clips": [],
    }

    with wave.open(str(args.source), "rb") as source:
        params = source.getparams()
        duration = params.nframes / params.framerate
        ends = starts[1:] + [duration]
        for number, start, end in zip(sequence, starts, ends):
            clip_duration = end - start
            if not 0.6 <= clip_duration <= 15.1:
                raise ValueError(f"clip {number} duration {clip_duration:.3f}s is out of range")
            first_frame = round(start * params.framerate)
            last_frame = round(end * params.framerate)
            source.setpos(first_frame)
            frames = source.readframes(last_frame - first_frame)
            output = args.output_dir / f"{number:02d}_{stamp}_import.wav"
            with wave.open(str(output), "wb") as target:
                target.setparams(params)
                target.writeframes(frames)
            manifest["clips"].append({
                "number": number,
                "start_seconds": round(first_frame / params.framerate, 6),
                "end_seconds": round(last_frame / params.framerate, 6),
                "duration_seconds": round((last_frame - first_frame) / params.framerate, 6),
                "path": str(output.resolve()),
            })

    manifest["source_duration_seconds"] = round(duration, 6)
    manifest_path = args.output_dir / f"import_{stamp}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(manifest_path)


if __name__ == "__main__":
    main()
