#!/usr/bin/env python3
"""A dependency-free local web UI for the existing IndexTTS-2.5 deployment."""

import argparse
import base64
import io
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
import wave
import xml.etree.ElementTree as ET
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from urllib.parse import unquote, urlparse
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parent


def configured_path(name, default):
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


REPO = configured_path("INDEXTTS_REPO", ROOT / "vendor" / "index-tts")
MODEL_DIR = configured_path("INDEXTTS_MODEL_DIR", ROOT / "models" / "IndexTTS-2.5")
HTML_PATH = ROOT / "webui" / "index.html"
OUTPUT_DIR = ROOT / "outputs" / "webui"
UPLOAD_DIR = OUTPUT_DIR / "uploads"
DEFAULT_VOICE = configured_path(
    "INDEXTTS_DEFAULT_VOICE", ROOT / "assets" / "voice" / "default_voice.wav"
)
DEFAULT_EMOTION = configured_path(
    "INDEXTTS_DEFAULT_EMOTION", ROOT / "assets" / "emotion" / "default_emotion.wav"
)
BASELINE_J = configured_path(
    "INDEXTTS_BASELINE_AUDIO", ROOT / "assets" / "baseline" / "baseline.wav"
)
PREFERENCE_PATH = OUTPUT_DIR / "preference_ratings.jsonl"
COMPARISON_PATH = OUTPUT_DIR / "comparison_ratings.jsonl"
EMOTION_LIBRARY_DOCX = (
    ROOT / "assets" / "emotion_library" / "IndexTTS财经口播情绪库录音稿.docx"
)
EMOTION_LIBRARY_RECORDINGS = ROOT / "assets" / "emotion_library" / "recordings"
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_PERFORMANCE_SEGMENTS = 24
EMOTION_LABELS = ("高兴", "愤怒", "悲伤", "害怕", "反感", "低落", "惊讶", "平静")
SMART_BASE_VECTOR = (0.6, 0.0, 0.0, 0.0, 0.0, 0.0, 0.4, 0.7)

ROLE_TEMPLATES = {
    "陈述": (0.55, 0.0, 0.0, 0.0, 0.0, 0.0, 0.30, 0.75),
    "谨慎": (0.35, 0.05, 0.0, 0.05, 0.0, 0.0, 0.35, 0.80),
    "转折": (0.50, 0.0, 0.0, 0.0, 0.0, 0.0, 0.55, 0.70),
    "加速": (0.70, 0.0, 0.0, 0.0, 0.0, 0.0, 0.50, 0.60),
    "提醒": (0.35, 0.10, 0.0, 0.05, 0.0, 0.0, 0.55, 0.80),
    "强调": (0.50, 0.0, 0.0, 0.0, 0.0, 0.0, 0.45, 0.80),
    "收尾": (0.55, 0.0, 0.0, 0.0, 0.0, 0.0, 0.25, 0.85),
    "对话": (0.50, 0.0, 0.0, 0.0, 0.0, 0.0, 0.35, 0.75),
    "疑问": (0.40, 0.0, 0.0, 0.05, 0.0, 0.0, 0.50, 0.75),
    "低声": (0.20, 0.0, 0.15, 0.10, 0.0, 0.15, 0.10, 0.85),
    "悲伤揭示": (0.15, 0.0, 0.55, 0.05, 0.0, 0.35, 0.10, 0.65),
    "惊讶": (0.40, 0.0, 0.0, 0.10, 0.0, 0.0, 0.75, 0.50),
    "呼喊": (0.70, 0.0, 0.0, 0.0, 0.0, 0.0, 0.65, 0.40),
    "开心呼喊": (0.85, 0.0, 0.0, 0.0, 0.0, 0.0, 0.60, 0.35),
    "悬念": (0.15, 0.0, 0.15, 0.15, 0.0, 0.30, 0.45, 0.75),
}

ROLE_PROSODY = {
    "陈述": (1.00, 0, 180, "中"),
    "谨慎": (1.04, 100, 250, "低"),
    "转折": (1.03, 180, 240, "中"),
    "加速": (0.95, 80, 150, "高"),
    "提醒": (1.06, 180, 280, "中高"),
    "强调": (1.04, 120, 220, "中高"),
    "收尾": (1.08, 160, 400, "中"),
    "对话": (1.00, 100, 180, "中"),
    "疑问": (1.02, 100, 230, "中"),
    "低声": (1.08, 160, 300, "低"),
    "悲伤揭示": (1.08, 180, 320, "低"),
    "惊讶": (0.98, 120, 240, "高"),
    "呼喊": (0.96, 100, 240, "高"),
    "开心呼喊": (0.94, 100, 260, "高"),
    "悬念": (1.08, 180, 400, "低"),
}

ROLE_RULES = (
    ("提醒", ("注意", "不要", "不能", "务必", "必须", "警惕", "谨防", "风险")),
    ("加速", ("加速", "增长", "突破", "提升", "扩大", "上升", "利好")),
    ("转折", ("但现在", "但是", "不过", "然而", "相反", "变化", "就在这时")),
    ("强调", ("真正", "重要", "关键", "其实不是", "值得")),
    ("谨慎", ("抓不住", "追高", "下滑", "压力", "困难", "问题", "损失", "失败")),
)

ROLE_LIBRARY_NUMBERS = {
    "陈述": (1, 2, 3, 4, 5, 9, 11, 14, 15),
    "谨慎": (20, 25, 26, 31, 45, 55, 56),
    "转折": (6, 17, 38, 45, 56),
    "加速": (35, 36, 41, 47, 54),
    "提醒": (7, 18, 24, 27, 30),
    "强调": (7, 21, 37, 46, 47),
    "收尾": (16, 48, 63, 64),
    "对话": (22, 40, 49, 61),
    "疑问": (19, 39, 58),
    "低声": (25, 53, 57),
    "悲伤揭示": (51, 52, 57),
    "惊讶": (34, 43),
    "呼喊": (24, 30, 34),
    "开心呼喊": (34, 43, 47),
    "悬念": (22, 24, 58),
}

EXPLICIT_EMOTION_WORDS = {
    0: ("开心", "高兴", "喜悦", "兴奋", "祝贺", "振奋"),
    1: ("愤怒", "生气", "恼火", "可恶"),
    2: ("悲伤", "难过", "伤心", "遗憾", "哭泣"),
    3: ("害怕", "恐惧", "惊恐", "快躲", "危险"),
    4: ("反感", "厌恶", "恶心"),
    5: ("低落", "忧郁", "惆怅", "孤独"),
    6: ("惊讶", "没想到", "竟然", "突然"),
}

MODEL = None
MODEL_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
PREFERENCE_LOCK = threading.Lock()
STATE = {
    "phase": "idle",
    "message": "就绪，模型将在首次生成时加载",
    "model_loaded": False,
    "qwen_loaded": False,
    "current_output": None,
    "last_error": None,
}


def update_state(**changes):
    with STATE_LOCK:
        STATE.update(changes)


def system_memory():
    result = {
        "memory_free_percent": None,
        "swap_used_mib": None,
        "mps_driver_gib": None,
    }
    pressure = subprocess.run(
        ["/usr/bin/memory_pressure"], capture_output=True, text=True, check=False
    ).stdout
    match = re.search(r"System-wide memory free percentage:\s*(\d+)%", pressure)
    if match:
        result["memory_free_percent"] = int(match.group(1))
    swap = subprocess.run(
        ["/usr/sbin/sysctl", "-n", "vm.swapusage"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    match = re.search(r"used = ([\d.]+)M", swap)
    if match:
        result["swap_used_mib"] = float(match.group(1))
    try:
        import torch

        if torch.backends.mps.is_available():
            result["mps_driver_gib"] = torch.mps.driver_allocated_memory() / 2**30
    except Exception:
        pass
    return result


def split_semantic_segments(text):
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        return []
    sentence_end = set("。！？!?；;")
    closing_quotes = set("”’」』》】）)]\"'")
    opening_quotes = set("“‘「『《【（([")
    segments = []
    current = []
    index = 0

    def finish():
        segment = "".join(current).strip()
        if segment:
            segments.append(segment)
        current.clear()

    while index < len(source):
        char = source[index]
        if char == "\n":
            finish()
            index += 1
            continue
        current.append(char)
        if char in sentence_end:
            index += 1
            while index < len(source) and source[index] in sentence_end:
                current.append(source[index])
                index += 1
            while index < len(source) and source[index] in closing_quotes:
                current.append(source[index])
                index += 1
            finish()
            continue
        if char in "：:":
            lookahead = index + 1
            while lookahead < len(source) and source[lookahead].isspace():
                lookahead += 1
            if lookahead >= len(source) or source[lookahead] in opening_quotes:
                finish()
        index += 1
    finish()
    return segments


def prepare_performance_sections(text, target_chars=55):
    source = str(text or "").strip()
    if not source:
        raise ValueError("请先输入需要带读的文案。")
    if len(source) > 5000:
        raise ValueError("单次文案请控制在 5000 字以内。")
    parts = split_semantic_segments(source)
    if not parts:
        raise ValueError("没有识别到可带读的文案。")
    sections = []
    current = []
    current_chars = 0
    for part in parts:
        part_chars = len(re.sub(r"\s+", "", part))
        if current and current_chars + part_chars > target_chars:
            sections.append("\n".join(current))
            current = []
            current_chars = 0
        current.append(part)
        current_chars += part_chars
    if current:
        sections.append("\n".join(current))
    if len(sections) > MAX_PERFORMANCE_SEGMENTS:
        raise ValueError("带读分段过多，请把文案分批生成。")
    return sections


def detect_speaking_role(text, is_last=False, previous_text=""):
    stripped = text.strip()
    opening_quotes = tuple("“‘「『\"'")
    closing_quotes = tuple("”’」』\"'")
    continuing_quote = (
        previous_text.strip().startswith(opening_quotes)
        and not previous_text.strip().endswith(closing_quotes)
    )
    quoted = stripped.startswith(opening_quotes) or continuing_quote
    spoken = stripped.rstrip("”’」』》】）)]\"'")
    if is_last:
        matched = next(
            (marker for marker in ("一个人都没有", "空无一人", "消失了", "没人", "没有。") if marker in stripped),
            None,
        )
        if matched:
            return "悬念", f"末句检测到“{matched}”，建议放慢并留出悬念"
    low_voice_markers = ("低声", "压低声音", "小声说", "轻声说", "悄悄")
    matched = next((marker for marker in low_voice_markers if marker in stripped), None)
    if matched:
        return "低声", f"检测到“{matched}”，建议降低能量并放慢"
    sad_markers = ("去世", "离世", "再也不会", "永远离开", "不在人世")
    emotion_context = previous_text + stripped if continuing_quote else stripped
    matched = next((marker for marker in sad_markers if marker in emotion_context), None)
    if matched:
        return "悲伤揭示", f"检测到“{matched}”，建议克制地揭示悲伤信息"
    if quoted and any(marker in previous_text for marker in low_voice_markers):
        return "低声", "上文提示低声表达，建议对白降低能量并放慢"
    if "喊" in stripped and any(marker in stripped for marker in ("开心", "高兴", "兴奋", "笑着")):
        return "开心呼喊", "检测到积极情绪和呼喊动作，建议提高能量"
    matched = next((marker for marker in ("愣住", "惊呆", "大吃一惊", "不敢相信") if marker in stripped), None)
    if matched:
        return "惊讶", f"检测到“{matched}”，建议短促变化后停顿"
    if any(marker in stripped for marker in ("方向看过去", "回头一看", "打开一看")):
        return "悬念", "检测到悬念铺垫动作，建议放慢并拉开停顿"
    if quoted and spoken.endswith(("！", "!")) and len(stripped) <= 12:
        if any(marker in previous_text for marker in ("开心", "高兴", "兴奋", "笑着")):
            return "开心呼喊", "短句感叹且上文包含积极情绪，建议明亮呼喊"
        return "呼喊", "检测到短句感叹对白，建议提高能量"
    if spoken.endswith(("？", "?")):
        return "疑问", "检测到疑问句，建议稍慢并保留问句起伏"
    if quoted:
        return "对话", "检测到直接引语，建议使用自然对话语气"
    if is_last:
        matched = next(
            (marker for marker in ("真正", "总之", "最终", "因此", "值得我们", "结论") if marker in text),
            None,
        )
        if matched:
            return "收尾", f"末句检测到“{matched}”，建议坚定收尾"
    for role, markers in ROLE_RULES:
        matched = [marker for marker in markers if marker in text]
        if matched:
            return role, f"检测到“{matched[0]}”，建议使用{role}语气"
    return "陈述", "未检测到强烈情绪词，保持自然陈述"


def recommend_vector(text, qwen_signal, is_last=False, previous_text=""):
    role, reason = detect_speaking_role(
        text, is_last=is_last, previous_text=previous_text
    )
    vector = list(ROLE_TEMPLATES[role])
    explicit = []
    for index, words in EXPLICIT_EMOTION_WORDS.items():
        matched = next((word for word in words if word in text), None)
        if matched:
            explicit.append(matched)
            signal = qwen_signal[index] if qwen_signal else 0.0
            vector[index] = round(max(vector[index], 0.55, min(0.8, signal)), 2)
    if explicit:
        reason += f"；同时检测到明确情绪词“{explicit[0]}”"
    return role, reason, vector


def read_preference_ratings():
    if not PREFERENCE_PATH.is_file():
        return []
    records = []
    for line in PREFERENCE_PATH.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("filename") and record.get("rating"):
            records.append(record)
    return records


def latest_preference_ratings():
    latest = {}
    for record in read_preference_ratings():
        latest[record["filename"]] = record
    return latest


def learned_role_prosody():
    latest = latest_preference_ratings()
    accepted = [record for record in latest.values() if record["rating"] == "human"]
    if not accepted:
        accepted = [record for record in latest.values() if record["rating"] == "better"]
    samples = {}
    for record in accepted:
        plan_path = OUTPUT_DIR / Path(record["filename"]).with_suffix(".json").name
        if not plan_path.is_file():
            continue
        try:
            segments = json.loads(plan_path.read_text(encoding="utf-8")).get("segments", [])
        except (json.JSONDecodeError, OSError):
            continue
        for segment in segments:
            role = segment.get("role")
            if role not in ROLE_PROSODY:
                continue
            try:
                values = (
                    float(
                        segment.get(
                            "effective_duration_factor",
                            segment["duration_factor"],
                        )
                    ),
                    int(segment["pause_before_ms"]),
                    int(segment["pause_after_ms"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            samples.setdefault(role, []).append(values)
    learned = {}
    for role, values in samples.items():
        speeds, pauses_before, pauses_after = zip(*values)
        learned[role] = {
            "duration_factor": round(float(median(speeds)), 2),
            "pause_before_ms": int(median(pauses_before)),
            "pause_after_ms": int(median(pauses_after)),
            "sample_count": len(values),
        }
    return learned


def prosody_for_role(role, learned=None):
    duration_factor, pause_before_ms, pause_after_ms, energy = ROLE_PROSODY[role]
    preference = (learned or {}).get(role)
    if preference:
        duration_factor = min(1.2, max(0.8, preference["duration_factor"]))
        pause_before_ms = min(1000, max(0, preference["pause_before_ms"]))
        pause_after_ms = min(1000, max(0, preference["pause_after_ms"]))
    return {
        "duration_factor": duration_factor,
        "pause_before_ms": pause_before_ms,
        "pause_after_ms": pause_after_ms,
        "energy": energy,
        "preference_samples": preference["sample_count"] if preference else 0,
    }


def segment_gap_ms(previous, current):
    return max(int(previous["pause_after_ms"]), int(current["pause_before_ms"]))


def vector_summary(vector, alpha):
    effective = [round(value * alpha, 4) for value in vector]
    total = round(sum(effective), 4)
    active = [
        {"label": EMOTION_LABELS[index], "value": value}
        for index, value in sorted(
            enumerate(vector), key=lambda item: item[1], reverse=True
        )
        if value >= 0.1
    ][:3]
    return effective, total, active


def analyze_emotion_plan(payload):
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ValueError("请先输入需要分析的文案。")
    if len(text) > 5000:
        raise ValueError("单次文案请控制在 5000 字以内。")
    alpha = float(payload.get("emo_alpha", 0.75))
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("情绪强度必须在 0–1 之间。")
    segments = split_semantic_segments(text)
    if not segments:
        raise ValueError("没有识别到可分析的文案。")
    if len(segments) > 80:
        raise ValueError("文案分段过多，请分批处理。")
    if not MODEL_LOCK.acquire(blocking=False):
        raise RuntimeError("模型正在生成，请完成后再分析文案。")

    analyzer = MODEL.qwen_emo if MODEL is not None else None
    learned = learned_role_prosody()
    started = time.perf_counter()
    try:
        update_state(phase="loading", message="正在逐句分析文案情绪…", last_error=None)
        plan = []
        for index, segment in enumerate(segments):
            has_explicit_emotion = any(
                word in segment
                for words in EXPLICIT_EMOTION_WORDS.values()
                for word in words
            )
            qwen_signal = [0.0] * 8
            if analyzer is not None and has_explicit_emotion:
                raw = analyzer.inference(segment)
                qwen_signal = [float(raw[key]) for key in (
                    "happy", "angry", "sad", "afraid", "disgusted",
                    "melancholic", "surprised", "calm"
                )]
            role, reason, vector = recommend_vector(
                segment,
                qwen_signal,
                is_last=index == len(segments) - 1,
                previous_text=segments[index - 1] if index else "",
            )
            effective, total, active = vector_summary(vector, alpha)
            prosody = prosody_for_role(role, learned)
            plan.append(
                {
                    "index": index,
                    "text": segment,
                    "role": role,
                    "reason": reason,
                    "confidence": "高" if role != "陈述" else "中",
                    "vector": vector,
                    "effective_vector": effective,
                    "effective_total": total,
                    "active_emotions": active,
                    "qwen_signal": qwen_signal,
                    "total_warning": total > 1.0,
                    **prosody,
                }
            )
        elapsed = time.perf_counter() - started
        update_state(
            phase="ready",
            message=f"文案情绪分析完成（{elapsed:.1f} 秒）",
            qwen_loaded=analyzer is not None,
        )
        return {
            "text": text,
            "alpha": alpha,
            "base_vector": list(SMART_BASE_VECTOR),
            "segments": plan,
            "elapsed_seconds": elapsed,
            "learned_roles": sorted(learned),
            "note": (
                "使用轻量中文口播规则；仅当情绪模型已加载且句中有明确情绪词时，"
                "才补充 Qwen 信号。"
            ),
        }
    except Exception:
        update_state(phase="error", message="文案情绪分析失败")
        raise
    finally:
        MODEL_LOCK.release()


def get_model():
    global MODEL
    if MODEL is not None:
        return MODEL
    update_state(phase="loading", message="正在加载 IndexTTS-2.5…", last_error=None)
    config_path = MODEL_DIR / "config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(
            "未找到 IndexTTS-2.5 模型。请设置 INDEXTTS_MODEL_DIR，"
            "使其指向包含 config.yaml 的模型目录。"
        )
    if REPO.is_dir() and str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import torch
    from indextts.infer_v2_5 import IndexTTS2

    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS 不可用；已拒绝自动回退到 CPU。")
    started = time.perf_counter()
    MODEL = IndexTTS2(
        cfg_path=str(config_path),
        model_dir=str(MODEL_DIR),
        use_bf16=False,
        device="mps",
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_accel=False,
        use_torch_compile=False,
        use_qwen_emo=False,
    )
    if str(MODEL.device) != "mps":
        MODEL = None
        raise RuntimeError("模型未在 MPS 上加载。")
    elapsed = time.perf_counter() - started
    update_state(
        phase="ready",
        message=f"模型已加载（{elapsed:.1f} 秒）",
        model_loaded=True,
    )
    return MODEL


def ensure_qwen(model):
    if model.qwen_emo is not None:
        return
    update_state(phase="loading", message="正在加载文本情绪模型…")
    from indextts.infer_v2_5 import QwenEmotion

    qwen_dir = MODEL_DIR / model.cfg.qwen_emo_path
    model.qwen_emo = QwenEmotion(str(qwen_dir))
    update_state(phase="ready", message="文本情绪模型已加载", qwen_loaded=True)


def safe_name(value):
    value = re.sub(r"[^\w\-]+", "_", (value or "").strip(), flags=re.UNICODE)
    return value.strip("_")[:60] or "indextts"


def unique_output_path(label):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = OUTPUT_DIR / f"{stamp}_{safe_name(label)}.wav"
    if not base.exists():
        return base
    for index in range(2, 1000):
        candidate = base.with_name(f"{base.stem}_{index}.wav")
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法生成唯一输出文件名。")


def validate_audio_path(value, kind):
    if not value:
        raise ValueError(f"请先选择{kind}。")
    path = Path(value).expanduser().resolve()
    allowed_defaults = {DEFAULT_VOICE.resolve(), DEFAULT_EMOTION.resolve()}
    upload_root = UPLOAD_DIR.resolve()
    library_root = EMOTION_LIBRARY_RECORDINGS.resolve()
    if (
        path not in allowed_defaults
        and upload_root not in path.parents
        and library_root not in path.parents
    ):
        raise ValueError(f"{kind} 只能使用默认参考或本页上传的音频。")
    if not path.is_file():
        raise FileNotFoundError(f"{kind} 不存在：{path}")
    if path.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(f"{kind} 格式不支持。")
    return path


def validate_emotion_plan(text, value):
    if not isinstance(value, list) or not value:
        raise ValueError("请先点击“分析文案情绪”。")
    if len(value) > 80:
        raise ValueError("情绪方案分段过多。")
    expected = re.sub(r"\s+", "", text)
    actual = ""
    plan = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("情绪方案格式无效。")
        segment_text = str(item.get("text", "")).strip()
        vector = item.get("vector")
        if not segment_text or not isinstance(vector, list) or len(vector) != 8:
            raise ValueError(f"第 {index + 1} 段情绪方案不完整。")
        vector = [float(number) for number in vector]
        if any(number < 0 or number > 1.2 for number in vector):
            raise ValueError(f"第 {index + 1} 段情绪值必须在 0–1.2 之间。")
        duration_factor = float(item.get("duration_factor", 1.0))
        pause_before_ms = int(item.get("pause_before_ms", 0))
        pause_after_ms = int(
            item.get(
                "pause_after_ms",
                round(boundary_silence_seconds(segment_text) * 1000),
            )
        )
        if not 0.8 <= duration_factor <= 1.2:
            raise ValueError(f"第 {index + 1} 段语速系数必须在 0.8–1.2 之间。")
        if not 0 <= pause_before_ms <= 1000 or not 0 <= pause_after_ms <= 1000:
            raise ValueError(f"第 {index + 1} 段停顿必须在 0–1000 毫秒之间。")
        actual += re.sub(r"\s+", "", segment_text)
        plan.append(
            {
                "text": segment_text,
                "role": str(item.get("role", "自定义"))[:20],
                "reason": str(item.get("reason", ""))[:200],
                "vector": vector,
                "duration_factor": duration_factor,
                "pause_before_ms": pause_before_ms,
                "pause_after_ms": pause_after_ms,
                "energy": str(item.get("energy", "中"))[:10],
            }
        )
    if actual != expected:
        raise ValueError("文案在分析后发生变化，请重新分析。")
    return plan


def validate_performance_plan(text, value):
    if not isinstance(value, list) or not value:
        raise ValueError("请先准备带读分段并完成每段录音。")
    if len(value) > MAX_PERFORMANCE_SEGMENTS:
        raise ValueError("带读分段过多，请把文案分批生成。")
    expected = re.sub(r"\s+", "", text)
    actual = ""
    plan = []
    import soundfile as sf

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("带读方案格式无效。")
        segment_text = str(item.get("text", "")).strip()
        if not segment_text:
            raise ValueError(f"第 {index + 1} 段文案为空。")
        recording = validate_audio_path(
            item.get("emotion_path", ""), f"第 {index + 1} 段带读录音"
        )
        duration = float(sf.info(recording).duration)
        if duration < 0.6:
            raise ValueError(f"第 {index + 1} 段录音太短，请重新录制。")
        if duration > 15.0:
            raise ValueError(f"第 {index + 1} 段录音超过 15 秒，请重新录制。")
        spoken_chars = len(re.sub(r"[^\w\u4e00-\u9fff]", "", segment_text))
        expected_duration = max(1.0, spoken_chars / 4.5)
        requested_factor = item.get("duration_factor")
        duration_factor = (
            min(1.15, max(0.85, float(requested_factor)))
            if requested_factor is not None
            else min(1.15, max(0.85, duration / expected_duration))
        )
        requested_pause = item.get("pause_after_ms")
        pause_after_ms = (
            min(1500, max(0, round(float(requested_pause))))
            if requested_pause is not None
            else round(boundary_silence_seconds(segment_text) * 1000)
        )
        actual += re.sub(r"\s+", "", segment_text)
        plan.append(
            {
                "text": segment_text,
                "emotion_path": str(recording),
                "source_duration_seconds": round(duration, 3),
                "duration_factor": round(duration_factor, 3),
                "pause_after_ms": pause_after_ms,
            }
        )
    if actual != expected:
        raise ValueError("文案在录音后发生变化，请重新准备带读。")
    return plan


def _library_text_score(text, candidate):
    def normalize(value):
        return re.sub(r"[^\w\u4e00-\u9fff]", "", value)

    wanted = normalize(text)
    available = normalize(candidate["text"])
    wanted_pairs = {wanted[index:index + 2] for index in range(len(wanted) - 1)}
    available_pairs = {
        available[index:index + 2] for index in range(len(available) - 1)
    }
    score = len(wanted_pairs & available_pairs)
    for phrase in ("所以注意", "请注意", "但现在", "真正值得", "更值得", "没想到", "问题来了", "等等"):
        if phrase in text and phrase in candidate["text"]:
            score += 12
    return score


def build_emotion_library_plan(text):
    parts = prepare_performance_sections(text, target_chars=40)
    if not parts:
        raise ValueError("没有识别到可匹配情绪的文案。")
    if len(parts) > MAX_PERFORMANCE_SEGMENTS:
        raise ValueError("文案分段过多，请把文案分批生成。")

    summary = emotion_library_summary()
    if summary["completed"] != summary["total"]:
        raise ValueError("真人情绪库尚未录完。")
    catalog = {item["number"]: item for item in summary["items"]}
    plan = []
    previous = ""
    for index, segment in enumerate(parts):
        role, reason = detect_speaking_role(
            segment,
            is_last=index == len(parts) - 1,
            previous_text=previous,
        )
        if any(phrase in segment for phrase in ("所以注意", "请注意")):
            role = "提醒"
            reason = "检测到明确提醒语，建议先收紧再重点表达"
        elif (
            role != "提醒"
            and any(word in segment for word in ("抓不住", "追高", "下滑", "压力", "困难", "损失", "失败"))
        ):
            role = "谨慎"
            reason = "检测到风险或受挫表达，建议使用谨慎语气"
        candidates = [catalog[number] for number in ROLE_LIBRARY_NUMBERS[role]]
        selected = max(
            candidates,
            key=lambda item: (_library_text_score(segment, item), -item["number"]),
        )
        recording = EMOTION_LIBRARY_RECORDINGS / Path(selected["url"]).name
        plan.append(
            {
                "text": segment,
                "role": role,
                "reason": reason,
                "library_number": selected["number"],
                "library_cue": selected["cue"],
                "emotion_path": str(recording),
                "source_duration_seconds": round(selected["duration_seconds"], 3),
                "duration_factor": 1.0,
                "pause_after_ms": round(boundary_silence_seconds(segment) * 1000),
            }
        )
        previous = segment
    return plan


def trim_generated_edges(audio, sample_rate):
    import numpy as np

    if not len(audio):
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak <= 0:
        return audio
    active = np.flatnonzero(np.abs(audio) >= peak * 0.003)
    if not len(active):
        return audio
    padding = int(sample_rate * 0.06)
    start = max(0, int(active[0]) - padding)
    end = min(len(audio), int(active[-1]) + padding + 1)
    return audio[start:end]


def boundary_silence_seconds(text):
    text = text.rstrip()
    if text.endswith(("……", "…", "...")):
        return 0.32
    if text.endswith(("。", ".")):
        return 0.22
    if text.endswith(("！", "!", "？", "?")):
        return 0.24
    if text.endswith(("；", ";")):
        return 0.16
    return 0.12


def generate_smart_script(model, kwargs, plan, output_path):
    import numpy as np
    import soundfile as sf
    import torch

    segment_results = []
    pieces = []
    sample_rate = None
    with tempfile.TemporaryDirectory(prefix="smart_emo_", dir=str(OUTPUT_DIR)) as temp_dir:
        for index, item in enumerate(plan):
            if index:
                gap_ms = segment_gap_ms(plan[index - 1], item)
                pieces.append(
                    np.zeros(int(sample_rate * gap_ms / 1000), dtype="float32")
                )
            segment_path = Path(temp_dir) / f"segment_{index + 1:03d}.wav"
            segment_duration_factor = min(
                1.2, max(0.8, kwargs["duration_factor"] * item["duration_factor"])
            )
            torch.mps.synchronize()
            started = time.perf_counter()
            model.infer(
                spk_audio_prompt=kwargs["spk_audio_prompt"],
                text=item["text"],
                output_path=str(segment_path),
                lang=kwargs["lang"],
                emo_audio_prompt=None,
                emo_alpha=kwargs["emo_alpha"],
                emo_vector=item["vector"],
                use_emo_text=False,
                emo_text=None,
                use_random=False,
                duration_factor=segment_duration_factor,
                verbose=True,
            )
            torch.mps.synchronize()
            elapsed = time.perf_counter() - started
            audio, current_rate = sf.read(segment_path, dtype="float32")
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)
            if sample_rate is None:
                sample_rate = int(current_rate)
            elif int(current_rate) != sample_rate:
                raise RuntimeError("分段音频采样率不一致。")
            audio = trim_generated_edges(audio, sample_rate)
            pieces.append(audio)
            segment_results.append(
                {
                    **item,
                    "effective_duration_factor": segment_duration_factor,
                    "duration_seconds": len(audio) / sample_rate,
                    "inference_seconds": elapsed,
                }
            )
        final_pause_ms = plan[-1]["pause_after_ms"]
        if final_pause_ms:
            pieces.append(
                np.zeros(int(sample_rate * final_pause_ms / 1000), dtype="float32")
            )

    if not pieces or sample_rate is None:
        raise RuntimeError("没有产生可合并的分段音频。")
    combined = np.concatenate(pieces)
    sf.write(output_path, combined, sample_rate, subtype="PCM_16")
    plan_path = output_path.with_suffix(".json")
    plan_path.write_text(
        json.dumps(
            {
                "output_path": str(output_path),
                "sample_rate": sample_rate,
                "duration_seconds": len(combined) / sample_rate,
                "fixed_parameters": {
                    "emo_alpha": kwargs["emo_alpha"],
                    "use_emo_text": False,
                    "use_random": False,
                    "duration_factor": kwargs["duration_factor"],
                },
                "segments": segment_results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return plan_path, segment_results


def generate_performance_script(model, kwargs, plan, output_path, mode="performance_script"):
    import numpy as np
    import soundfile as sf
    import torch

    segment_results = []
    pieces = []
    sample_rate = None
    with tempfile.TemporaryDirectory(prefix="performance_", dir=str(OUTPUT_DIR)) as temp_dir:
        for index, item in enumerate(plan):
            segment_path = Path(temp_dir) / f"segment_{index + 1:03d}.wav"
            torch.mps.synchronize()
            started = time.perf_counter()
            model.infer(
                spk_audio_prompt=kwargs["spk_audio_prompt"],
                text=item["text"],
                output_path=str(segment_path),
                lang=kwargs["lang"],
                emo_audio_prompt=item["emotion_path"],
                emo_alpha=kwargs["emo_alpha"],
                emo_vector=None,
                use_emo_text=False,
                emo_text=None,
                use_random=False,
                duration_factor=item["duration_factor"],
                verbose=True,
            )
            torch.mps.synchronize()
            elapsed = time.perf_counter() - started
            audio, current_rate = sf.read(segment_path, dtype="float32")
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)
            if sample_rate is None:
                sample_rate = int(current_rate)
            elif int(current_rate) != sample_rate:
                raise RuntimeError("分段音频采样率不一致。")
            audio = trim_generated_edges(audio, sample_rate)
            pieces.append(audio)
            if index < len(plan) - 1:
                pieces.append(
                    np.zeros(
                        int(sample_rate * item["pause_after_ms"] / 1000),
                        dtype="float32",
                    )
                )
            segment_results.append(
                {
                    **item,
                    "duration_seconds": len(audio) / sample_rate,
                    "inference_seconds": elapsed,
                }
            )

    if not pieces or sample_rate is None:
        raise RuntimeError("没有产生可合并的带读音频。")
    combined = np.concatenate(pieces)
    sf.write(output_path, combined, sample_rate, subtype="PCM_16")
    plan_path = output_path.with_suffix(".json")
    plan_path.write_text(
        json.dumps(
            {
                "output_path": str(output_path),
                "sample_rate": sample_rate,
                "duration_seconds": len(combined) / sample_rate,
                "mode": mode,
                "fixed_parameters": {
                    "emo_alpha": kwargs["emo_alpha"],
                    "use_emo_text": False,
                    "use_random": False,
                },
                "segments": segment_results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return plan_path, segment_results


def build_infer_kwargs(payload, output_path):
    text = str(payload.get("text", "")).strip()
    if not text:
        raise ValueError("请先输入需要生成的文案。")
    if len(text) > 5000:
        raise ValueError("单次文案请控制在 5000 字以内。")
    voice = validate_audio_path(payload.get("voice_path", ""), "音色参考")
    mode = payload.get("mode", "emotion_audio")
    if mode not in {
        "voice_only", "emotion_audio", "text_auto", "manual_vector",
        "smart_script", "performance_script", "emotion_library",
    }:
        raise ValueError("未知的情绪模式。")
    alpha = float(payload.get("emo_alpha", 0.75))
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("情绪强度必须在 0–1 之间。")
    duration_factor = float(payload.get("duration_factor", 1.0))
    if not 0.8 <= duration_factor <= 1.2:
        raise ValueError("速度系数必须在 0.8–1.2 之间。")
    lang = payload.get("lang", "ZH")
    if lang not in {"ZH", "EN", "JA"}:
        raise ValueError("语言参数无效。")

    kwargs = {
        "spk_audio_prompt": str(voice),
        "text": text,
        "output_path": str(output_path),
        "lang": lang,
        "emo_audio_prompt": None,
        "emo_alpha": alpha,
        "emo_vector": None,
        "use_emo_text": False,
        "emo_text": None,
        "use_random": False,
        "duration_factor": duration_factor,
        "verbose": True,
    }
    if mode == "emotion_audio":
        emotion = validate_audio_path(payload.get("emotion_path", ""), "情绪参考")
        kwargs["emo_audio_prompt"] = str(emotion)
    elif mode == "text_auto":
        kwargs["use_emo_text"] = True
    elif mode == "manual_vector":
        vector = payload.get("emo_vector")
        if not isinstance(vector, list) or len(vector) != 8:
            raise ValueError("手动情绪向量必须包含 8 个数值。")
        vector = [float(value) for value in vector]
        if any(value < 0 or value > 1.2 for value in vector):
            raise ValueError("手动情绪值必须在 0–1.2 之间。")
        kwargs["emo_vector"] = vector
    return mode, kwargs


def generate(payload):
    if not MODEL_LOCK.acquire(blocking=False):
        raise RuntimeError("已有一条任务在生成，请等它完成。")
    output_path = None
    plan_path = None
    try:
        output_path = unique_output_path(payload.get("output_name", "indextts"))
        mode, kwargs = build_infer_kwargs(payload, output_path)
        emotion_plan = (
            validate_emotion_plan(kwargs["text"], payload.get("emotion_plan"))
            if mode == "smart_script"
            else None
        )
        performance_plan = (
            validate_performance_plan(kwargs["text"], payload.get("performance_plan"))
            if mode == "performance_script"
            else None
        )
        library_plan = (
            build_emotion_library_plan(kwargs["text"])
            if mode == "emotion_library"
            else None
        )
        model = get_model()
        if mode == "text_auto":
            ensure_qwen(model)
        update_state(
            phase="generating",
            message="正在生成语音，请不要关闭启动窗口…",
            current_output=str(output_path),
            last_error=None,
        )
        import torch

        torch.mps.synchronize()
        started = time.perf_counter()
        if mode == "smart_script":
            plan_path, _ = generate_smart_script(
                model, kwargs, emotion_plan, output_path
            )
        elif mode == "performance_script":
            plan_path, _ = generate_performance_script(
                model, kwargs, performance_plan, output_path
            )
        elif mode == "emotion_library":
            plan_path, library_plan = generate_performance_script(
                model, kwargs, library_plan, output_path, mode="emotion_library"
            )
        else:
            model.infer(**kwargs)
        torch.mps.synchronize()
        elapsed = time.perf_counter() - started
        if not output_path.is_file() or output_path.stat().st_size <= 44:
            raise RuntimeError("推理结束但未产生有效音频。")
        try:
            import soundfile as sf

            info = sf.info(output_path)
            duration = float(info.duration)
        except Exception:
            duration = None
        memory = system_memory()
        update_state(
            phase="ready",
            message=f"生成完成（{elapsed:.1f} 秒）",
            current_output=str(output_path),
        )
        return {
            "path": str(output_path),
            "url": f"/media/{output_path.name}",
            "filename": output_path.name,
            "duration_seconds": duration,
            "elapsed_seconds": elapsed,
            "rtf": elapsed / duration if duration else None,
            "memory": memory,
            "plan_url": f"/media/{plan_path.name}" if plan_path else None,
            "library_matches": [
                {
                    "role": item["role"],
                    "number": item["library_number"],
                    "cue": item["library_cue"],
                }
                for item in (library_plan or [])
            ],
        }
    except Exception as exc:
        update_state(
            phase="error",
            message=str(exc),
            last_error=traceback.format_exc(),
            current_output=str(output_path) if output_path else None,
        )
        if output_path and output_path.exists() and output_path.stat().st_size <= 44:
            output_path.unlink()
        raise
    finally:
        MODEL_LOCK.release()


def decode_upload(payload):
    encoded = str(payload.get("data", ""))
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    try:
        content = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("上传数据无效。") from exc
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("音频必须在 30 MB 以内。")
    return content


def save_upload(payload):
    filename = Path(str(payload.get("filename", "audio.wav"))).name
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError("仅支持 WAV、MP3、M4A、FLAC 和 OGG。")
    content = decode_upload(payload)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = UPLOAD_DIR / f"{stamp}_{safe_name(Path(filename).stem)}{extension}"
    path.write_bytes(content)
    return {"path": str(path), "filename": filename, "bytes": len(content)}


def emotion_library_catalog():
    if not EMOTION_LIBRARY_DOCX.is_file():
        raise FileNotFoundError("情绪库录音稿不存在。")
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with ZipFile(EMOTION_LIBRARY_DOCX) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(
            node.text or "" for node in paragraph.iter(namespace + "t")
        ).strip()
        if text:
            paragraphs.append(text)

    items = []
    part = ""
    pending = None
    for text in paragraphs:
        if re.match(r"^第[一二三四]部分\s", text):
            part = text
            continue
        match = re.match(r"^(\d{2})\s+表演提示\s+不朗读\s+(.+)$", text)
        if match:
            pending = {
                "number": int(match.group(1)),
                "part": part,
                "cue": match.group(2),
            }
            continue
        if pending:
            pending["text"] = text
            items.append(pending)
            pending = None
    if len(items) != 64 or [item["number"] for item in items] != list(range(1, 65)):
        raise ValueError("情绪库录音稿编号不完整。")
    return items


def wav_duration(source):
    try:
        with wave.open(source, "rb") as audio:
            rate = audio.getframerate()
            return audio.getnframes() / rate if rate else 0.0
    except (EOFError, wave.Error) as exc:
        raise ValueError("录音不是有效的 WAV 文件。") from exc


def emotion_library_summary():
    recordings = {}
    if EMOTION_LIBRARY_RECORDINGS.is_dir():
        for path in EMOTION_LIBRARY_RECORDINGS.glob("[0-9][0-9]_*.wav"):
            number = int(path.name[:2])
            current = recordings.get(number)
            if current is None or path.stat().st_mtime > current.stat().st_mtime:
                recordings[number] = path
    items = []
    for item in emotion_library_catalog():
        result = dict(item)
        path = recordings.get(item["number"])
        result.update({"recorded": bool(path), "duration_seconds": None, "url": None})
        if path:
            result["duration_seconds"] = wav_duration(str(path))
            result["url"] = f"/emotion-library/{path.name}"
        items.append(result)
    return {
        "items": items,
        "completed": sum(item["recorded"] for item in items),
        "total": len(items),
    }


def save_emotion_library_recording(payload):
    try:
        number = int(payload.get("number", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("录音编号无效。") from exc
    catalog = emotion_library_catalog()
    if number < 1 or number > len(catalog):
        raise ValueError("录音编号无效。")
    content = decode_upload(payload)
    duration = wav_duration(io.BytesIO(content))
    if duration < 0.6:
        raise ValueError("这一条录音太短，请重新录制。")
    if duration > 15.1:
        raise ValueError("这一条超过 15 秒，请重新录制。")
    EMOTION_LIBRARY_RECORDINGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = EMOTION_LIBRARY_RECORDINGS / f"{number:02d}_{stamp}.wav"
    path.write_bytes(content)
    return {
        "number": number,
        "path": str(path),
        "filename": path.name,
        "url": f"/emotion-library/{path.name}",
        "duration_seconds": duration,
        "bytes": len(content),
    }


def preference_summary():
    latest = latest_preference_ratings()
    counts = {rating: 0 for rating in ("worse", "same", "better", "human")}
    for record in latest.values():
        if record["rating"] in counts:
            counts[record["rating"]] += 1
    return {
        "baseline_url": "/baseline-j.wav" if BASELINE_J.is_file() else None,
        "ratings": {name: record["rating"] for name, record in latest.items()},
        "counts": counts,
        "learned_roles": learned_role_prosody(),
        "comparison": comparison_summary(),
    }


def read_comparison_ratings():
    if not COMPARISON_PATH.is_file():
        return []
    records = []
    for line in COMPARISON_PATH.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("choice") in {"a", "b", "neither"}:
            records.append(record)
    return records


def latest_comparison_ratings():
    latest = {}
    for record in read_comparison_ratings():
        key = (
            record.get("dimension", "pipeline"),
            record.get("a_filename"),
            record.get("b_filename"),
        )
        latest[key] = record
    return list(latest.values())


def prepare_spoken_text(text):
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if not lines:
        raise ValueError("请先输入需要整理的文案。")
    list_pattern = re.compile(r"^第[一二三四五六七八九十百零〇两0-9]+[、，,]")
    total_items = sum(bool(list_pattern.match(line)) for line in lines)
    item_index = 0
    prepared = []
    for line in lines:
        if list_pattern.match(line):
            item_index += 1
            body = re.sub(r"[。！？!?；;]+$", "", line)
            ending = "。" if item_index % 5 == 0 or item_index == total_items else "；"
            prepared.append(body + ending)
            continue
        if total_items and not prepared:
            title = re.sub(r"[。！？!?；;…]+$", "", line)
            prepared.append(title + "——")
            continue
        if not line.endswith(("。", "！", "？", "；", "…", ".", "!", "?", ";")):
            line += "。"
        prepared.append(line)
    return "\n".join(prepared)


def comparison_summary():
    counts = {"continuous": 0, "smart": 0, "neither": 0}
    pace_counts = {"0.95": 0, "1.05": 0, "neither": 0}
    punctuation_counts = {"original": 0, "structured": 0, "neither": 0}
    for record in latest_comparison_ratings():
        dimension = record.get("dimension", "pipeline")
        choice = record["choice"]
        if dimension == "punctuation":
            if choice == "neither":
                punctuation_counts["neither"] += 1
                continue
            selected_value = record.get(f"{choice}_value")
            if selected_value in punctuation_counts:
                punctuation_counts[selected_value] += 1
            continue
        if dimension == "pace":
            if choice == "neither":
                pace_counts["neither"] += 1
                continue
            try:
                selected_value = f'{float(record[f"{choice}_value"]):.2f}'
            except (KeyError, TypeError, ValueError):
                continue
            if selected_value in pace_counts:
                pace_counts[selected_value] += 1
            continue
        if choice == "neither":
            counts["neither"] += 1
            continue
        selected_mode = record.get(f"{choice}_mode")
        if selected_mode in {"continuous", "smart"}:
            counts[selected_mode] += 1
    recommended = None
    if counts["continuous"] > counts["smart"]:
        recommended = "emotion_audio"
    elif counts["smart"] > counts["continuous"]:
        recommended = "smart_script"
    recommended_duration = 1.0
    if pace_counts["0.95"] > pace_counts["1.05"]:
        recommended_duration = 0.95
    elif pace_counts["1.05"] > pace_counts["0.95"]:
        recommended_duration = 1.05
    pipeline_rounds = sum(counts.values())
    pace_rounds = sum(pace_counts.values())
    punctuation_rounds = sum(punctuation_counts.values())
    if not pipeline_rounds:
        next_dimension = "pipeline"
    elif not pace_rounds:
        next_dimension = "pace"
    elif not punctuation_rounds:
        next_dimension = "punctuation"
    else:
        next_dimension = "complete"
    recommended_text_style = "original"
    if punctuation_counts["structured"] > punctuation_counts["original"]:
        recommended_text_style = "structured"
    return {
        "counts": counts,
        "pace_counts": pace_counts,
        "punctuation_counts": punctuation_counts,
        "recommended_mode": recommended,
        "recommended_duration": recommended_duration,
        "recommended_text_style": recommended_text_style,
        "next_dimension": next_dimension,
    }


def rate_comparison(payload):
    choice = str(payload.get("choice", ""))
    if choice not in {"a", "b", "neither"}:
        raise ValueError("A/B 选择无效。")
    dimension = str(payload.get("dimension", "pipeline"))
    if dimension not in {"pipeline", "pace", "punctuation"}:
        raise ValueError("A/B 校准类型无效。")
    record = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "choice": choice,
        "dimension": dimension,
    }
    for side in ("a", "b"):
        raw_filename = str(payload.get(f"{side}_filename", ""))
        filename = Path(raw_filename).name
        mode = str(payload.get(f"{side}_mode", ""))
        if not filename or filename != raw_filename:
            raise ValueError("A/B 音频文件名无效。")
        if mode not in {"continuous", "smart"}:
            raise ValueError("A/B 生成方式无效。")
        wav_path = OUTPUT_DIR / filename
        if wav_path.suffix.lower() != ".wav" or not wav_path.is_file():
            raise FileNotFoundError("找不到 A/B 对比音频。")
        record[f"{side}_filename"] = filename
        record[f"{side}_mode"] = mode
        if dimension == "pace":
            value = float(payload.get(f"{side}_value", 0))
            if value not in {0.95, 1.05}:
                raise ValueError("A/B 语速校准值无效。")
            record[f"{side}_value"] = value
        elif dimension == "punctuation":
            value = str(payload.get(f"{side}_value", ""))
            if value not in {"original", "structured"}:
                raise ValueError("A/B 标点校准值无效。")
            record[f"{side}_value"] = value
    if dimension == "pipeline" and record["a_mode"] == record["b_mode"]:
        raise ValueError("生成路线 A/B 必须使用不同方式。")
    if dimension == "pace":
        if record["a_mode"] != "continuous" or record["b_mode"] != "continuous":
            raise ValueError("语速 A/B 必须使用整段连贯方式。")
        if record["a_value"] == record["b_value"]:
            raise ValueError("A/B 语速必须不同。")
    if dimension == "punctuation":
        if record["a_mode"] != "continuous" or record["b_mode"] != "continuous":
            raise ValueError("标点 A/B 必须使用整段连贯方式。")
        if record["a_value"] == record["b_value"]:
            raise ValueError("A/B 标点方案必须不同。")
    COMPARISON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PREFERENCE_LOCK:
        with COMPARISON_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return comparison_summary()


def rate_output(payload):
    raw_filename = str(payload.get("filename", ""))
    filename = Path(raw_filename).name
    if not filename or filename != raw_filename:
        raise ValueError("音频文件名无效。")
    rating = str(payload.get("rating", ""))
    if rating not in {"worse", "same", "better", "human"}:
        raise ValueError("评分选项无效。")
    wav_path = OUTPUT_DIR / filename
    if wav_path.suffix.lower() != ".wav" or not wav_path.is_file():
        raise FileNotFoundError("找不到需要评分的音频。")
    record = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "filename": filename,
        "rating": rating,
    }
    PREFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PREFERENCE_LOCK:
        with PREFERENCE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return preference_summary()


def history():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ratings = latest_preference_ratings()
    files = sorted(
        (
            path
            for path in OUTPUT_DIR.glob("*.wav")
            if path.is_file() and path.stat().st_size > 44
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:12]
    return [
        {
            "filename": path.name,
            "url": f"/media/{path.name}",
            "size_mib": path.stat().st_size / 2**20,
            "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%m-%d %H:%M"
            ),
            "rating": ratings.get(path.name, {}).get("rating"),
        }
        for path in files
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "IndexTTSEasyUI/1.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_bytes(self, content, content_type, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, value, status=HTTPStatus.OK):
        self.send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD_BYTES * 2:
            raise ValueError("请求大小无效。")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/status":
            with STATE_LOCK:
                state = dict(STATE)
            state["memory"] = system_memory()
            state["default_voice"] = str(DEFAULT_VOICE) if DEFAULT_VOICE.is_file() else None
            state["default_emotion"] = (
                str(DEFAULT_EMOTION) if DEFAULT_EMOTION.is_file() else None
            )
            self.send_json(state)
            return
        if parsed.path == "/api/history":
            self.send_json({"items": history()})
            return
        if parsed.path == "/api/preferences":
            self.send_json(preference_summary())
            return
        if parsed.path == "/api/emotion-library":
            self.send_json(emotion_library_summary())
            return
        if parsed.path == "/baseline-j.wav":
            if not BASELINE_J.is_file():
                self.send_json({"error": "J 基线不存在。"}, HTTPStatus.NOT_FOUND)
                return
            self.send_bytes(BASELINE_J.read_bytes(), "audio/wav")
            return
        if parsed.path.startswith("/media/"):
            name = Path(unquote(parsed.path.removeprefix("/media/"))).name
            path = OUTPUT_DIR / name
            if not path.is_file() or path.parent.resolve() != OUTPUT_DIR.resolve():
                self.send_json({"error": "文件不存在。"}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_bytes(path.read_bytes(), content_type)
            return
        if parsed.path.startswith("/emotion-library/"):
            name = Path(unquote(parsed.path.removeprefix("/emotion-library/"))).name
            path = EMOTION_LIBRARY_RECORDINGS / name
            if (
                not path.is_file()
                or path.parent.resolve() != EMOTION_LIBRARY_RECORDINGS.resolve()
            ):
                self.send_json({"error": "录音不存在。"}, HTTPStatus.NOT_FOUND)
                return
            self.send_bytes(path.read_bytes(), "audio/wav")
            return
        self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        try:
            payload = self.read_json()
            if self.path == "/api/generate":
                self.send_json({"ok": True, "result": generate(payload)})
                return
            if self.path == "/api/analyze-emotion":
                self.send_json({"ok": True, "result": analyze_emotion_plan(payload)})
                return
            if self.path == "/api/prepare-text":
                self.send_json({"ok": True, "result": {"text": prepare_spoken_text(payload.get("text"))}})
                return
            if self.path == "/api/prepare-performance":
                self.send_json(
                    {
                        "ok": True,
                        "result": {
                            "text": str(payload.get("text", "")).strip(),
                            "segments": [
                                {"text": text}
                                for text in prepare_performance_sections(payload.get("text"))
                            ],
                        },
                    }
                )
                return
            if self.path == "/api/upload":
                self.send_json({"ok": True, "result": save_upload(payload)})
                return
            if self.path == "/api/emotion-library-recording":
                self.send_json(
                    {"ok": True, "result": save_emotion_library_recording(payload)}
                )
                return
            if self.path == "/api/rate-output":
                self.send_json({"ok": True, "result": rate_output(payload)})
                return
            if self.path == "/api/rate-comparison":
                self.send_json({"ok": True, "result": rate_comparison(payload)})
                return
            if self.path == "/api/open-output":
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                subprocess.run(["/usr/bin/open", str(OUTPUT_DIR)], check=False)
                self.send_json({"ok": True})
                return
            if self.path == "/api/open-emotion-library":
                EMOTION_LIBRARY_RECORDINGS.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["/usr/bin/open", str(EMOTION_LIBRARY_RECORDINGS)], check=False
                )
                self.send_json({"ok": True})
                return
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main():
    parser = argparse.ArgumentParser(description="IndexTTS-2.5 easy local web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7865)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("为了安全，简易界面只允许绑定本机回环地址。")
    if not HTML_PATH.is_file():
        raise FileNotFoundError(HTML_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"\nIndexTTS 简易界面已启动：{url}")
    print("关闭这个终端窗口即可停止服务。\n")
    if args.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
