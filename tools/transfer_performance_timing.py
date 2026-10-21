#!/usr/bin/env python3
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


def speech_spans(audio, sample_rate):
    frame = int(0.02 * sample_rate)
    hop = int(0.01 * sample_rate)
    levels = np.array([
        np.sqrt(np.mean(audio[start:start + frame] ** 2) + 1e-15)
        for start in range(0, len(audio) - frame + 1, hop)
    ])
    silent = levels < 10 ** (-38 / 20)
    silences = []
    start = None
    for index, value in enumerate(silent):
        if value and start is None:
            start = index
        if start is not None and (not value or index == len(silent) - 1):
            end = index if not value else index + 1
            left, right = start * hop, min(len(audio), end * hop)
            if (right - left) / sample_rate >= 0.12:
                silences.append((left, right))
            start = None

    spans = []
    cursor = 0
    for left, right in silences:
        if (left - cursor) / sample_rate >= 0.08:
            spans.append((cursor, left))
        cursor = right
    if (len(audio) - cursor) / sample_rate >= 0.08:
        spans.append((cursor, len(audio)))
    return spans


def split_continuous(audio, count, weights, sample_rate):
    if count == 1:
        return [audio]
    energy = np.convolve(audio ** 2, np.ones(max(1, int(0.02 * sample_rate))), "same")
    boundaries = [0]
    cumulative = np.cumsum(weights) / np.sum(weights)
    for fraction in cumulative[:-1]:
        wanted = int(fraction * len(audio))
        radius = int(0.12 * sample_rate)
        left = max(boundaries[-1] + int(0.08 * sample_rate), wanted - radius)
        right = min(len(audio) - int(0.08 * sample_rate), wanted + radius)
        boundary = left + int(np.argmin(energy[left:right])) if right > left else wanted
        boundaries.append(boundary)
    boundaries.append(len(audio))
    return [audio[left:right] for left, right in zip(boundaries, boundaries[1:])]


def change_tempo(audio, sample_rate, rate, directory, index):
    source = directory / f"chunk_{index:03d}_in.wav"
    output = directory / f"chunk_{index:03d}_out.wav"
    sf.write(source, audio, sample_rate, subtype="FLOAT")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-af", f"atempo={rate:.6f}",
            "-c:a", "pcm_f32le", str(output),
        ],
        check=True,
    )
    changed, _ = sf.read(output, dtype="float32")
    fade = min(len(changed) // 2, int(0.004 * sample_rate))
    if fade:
        changed[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        changed[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata")
    parser.add_argument("output")
    args = parser.parse_args()

    metadata_path = Path(args.metadata)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    carrier, sample_rate = sf.read(metadata["output_path"], dtype="float32")
    if carrier.ndim > 1:
        carrier = carrier.mean(axis=1)

    completed = []
    report = []
    cursor = 0.0
    chunk_index = 0
    with tempfile.TemporaryDirectory(prefix="indextts_timing_") as temporary:
        directory = Path(temporary)
        for segment_index, segment in enumerate(metadata["segments"]):
            duration = float(segment["duration_seconds"])
            left = round(cursor * sample_rate)
            right = round((cursor + duration) * sample_rate)
            female = carrier[left:right]

            performance, performance_rate = sf.read(segment["emotion_path"], dtype="float32")
            if performance.ndim > 1:
                performance = performance.mean(axis=1)
            source_spans = speech_spans(performance, performance_rate)
            female_spans = speech_spans(female, sample_rate)
            source_durations = np.array([
                (end - start) / performance_rate for start, end in source_spans
            ])

            female_chunks = [female[start:end] for start, end in female_spans]
            direct_rates = [
                len(chunk) / sample_rate / source_duration
                for chunk, source_duration in zip(female_chunks, source_durations)
            ]
            direct_alignment = (
                len(female_spans) == len(source_spans)
                and all(0.65 <= rate <= 1.80 for rate in direct_rates)
            )
            if not direct_alignment:
                continuous = np.concatenate([female[start:end] for start, end in female_spans])
                female_chunks = split_continuous(
                    continuous, len(source_spans), source_durations, sample_rate
                )

            parts = []
            leading = source_spans[0][0] / performance_rate
            if leading:
                parts.append(np.zeros(round(leading * sample_rate), dtype=np.float32))
            rates = []
            for index, (source_span, female_chunk) in enumerate(zip(source_spans, female_chunks)):
                target_duration = (source_span[1] - source_span[0]) / performance_rate
                raw_rate = len(female_chunk) / sample_rate / target_duration
                rate = float(np.clip(raw_rate, 0.90, 1.10))
                rates.append({"raw": round(raw_rate, 3), "used": round(rate, 3)})
                parts.append(change_tempo(female_chunk, sample_rate, rate, directory, chunk_index))
                chunk_index += 1
                if index + 1 < len(source_spans):
                    gap = (source_spans[index + 1][0] - source_span[1]) / performance_rate
                    parts.append(np.zeros(round(gap * sample_rate), dtype=np.float32))
            trailing = (len(performance) - source_spans[-1][1]) / performance_rate
            if trailing:
                parts.append(np.zeros(round(trailing * sample_rate), dtype=np.float32))

            result = np.concatenate(parts)
            completed.append(result)
            report.append({
                "segment": segment_index + 1,
                "source_chunks": len(source_spans),
                "female_chunks": len(female_spans),
                "direct_alignment": direct_alignment,
                "rates": rates,
                "duration_seconds": round(len(result) / sample_rate, 3),
            })

            cursor += duration
            if segment_index + 1 < len(metadata["segments"]):
                pause = int(segment["pause_after_ms"])
                completed.append(np.zeros(round(pause / 1000 * sample_rate), dtype=np.float32))
                cursor += pause / 1000

    final = np.concatenate(completed)
    peak = float(np.max(np.abs(final)))
    if peak > 0.98:
        final *= 0.98 / peak
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, final, sample_rate, subtype="PCM_24")
    output.with_suffix(".json").write_text(
        json.dumps({"output": str(output), "segments": report}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"output={output}")
    print(f"duration_seconds={len(final) / sample_rate:.3f}")
    print(f"peak={np.max(np.abs(final)):.6f}")


if __name__ == "__main__":
    main()
