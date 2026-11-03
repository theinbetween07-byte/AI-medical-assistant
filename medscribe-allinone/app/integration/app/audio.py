"""音频管理（FR-01 / BR-01 / BR-02 / 技术设计 4.1）。

- 统一转 16 kHz 单声道 wav（ASR 与评测都以此为输入）
- 时长校验：< 10 s 给出"过短"警告但不阻断；> 15 min 阻断
- 静音校验：整段 RMS 过低或近零样本占比过高 -> 判定无有效语音（AC-11）
"""

from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path

from .config import Settings


class AudioError(RuntimeError):
    pass


def _run_ffmpeg(cfg: Settings, args: list[str]) -> str:
    cmd = [cfg.ffmpeg, "-hide_banner", "-nostdin", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=cfg.ffmpeg_timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AudioError(
            f"找不到 ffmpeg（当前配置：{cfg.ffmpeg}）。请安装 ffmpeg 或设置 MSHK_FFMPEG。"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"ffmpeg 超时（>{cfg.ffmpeg_timeout_s}s）：{exc}") from exc

    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        tail = stderr.strip().splitlines()[-3:]
        raise AudioError("ffmpeg 处理失败：" + " | ".join(tail))
    return stderr


def to_wav16k(cfg: Settings, src: Path, dst: Path) -> None:
    """转码为目标格式；已满足条件时 ffmpeg 仍会快速重写一遍，逻辑更简单。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        cfg,
        [
            "-y",
            "-i", str(src),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-acodec", "pcm_s16le",
            "-f", "wav",
            str(dst),
        ],
    )
    if not dst.is_file() or dst.stat().st_size <= 44:
        raise AudioError("转码后音频为空，请重新录制。")


def analyze_wav(path: Path) -> tuple[int, float, float]:
    """返回 (duration_ms, rms, near_zero_ratio)。"""
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.getnframes()
            raw = wf.readframes(frames)
    except (wave.Error, OSError) as exc:
        raise AudioError(f"无法解析 wav：{exc}") from exc

    if rate <= 0 or frames <= 0:
        return 0, 0.0, 1.0

    duration_ms = int(round(frames * 1000 / rate))
    if width != 2:
        # 非 16-bit：只做时长判断，静音交给"时长 + 空文本"兜底
        return duration_ms, 0.0, 0.0

    total = len(raw) // 2
    if total == 0:
        return duration_ms, 0.0, 1.0

    import array

    samples = array.array("h")
    samples.frombytes(raw[: total * 2])
    if channels > 1:
        samples = samples[::channels]

    # 抽样计算，长音频下保持 O(1) 量级开销
    step = max(1, len(samples) // 200_000)
    picked = samples[::step]
    if not picked:
        return duration_ms, 0.0, 1.0

    peak_sum = 0
    near_zero = 0
    for s in picked:
        peak_sum += s * s
        if abs(s) < 50:
            near_zero += 1

    rms = math.sqrt(peak_sum / len(picked))
    return duration_ms, rms, near_zero / len(picked)


def validate(cfg: Settings, wav_path: Path) -> dict:
    duration_ms, rms, near_zero_ratio = analyze_wav(wav_path)
    reasons: list[str] = []
    warnings: list[str] = []

    silent = rms < cfg.silence_rms or near_zero_ratio >= cfg.silence_ratio
    if silent:
        reasons.append("未检测到有效语音，请重新录制（AC-11）")
    if duration_ms > cfg.max_duration_ms:
        reasons.append(
            f"单次录音超过 {cfg.max_duration_ms // 60000} 分钟上限（BR-01），已拒绝处理"
        )
    if 0 < duration_ms < cfg.min_duration_ms:
        warnings.append(
            f"录音时长 {duration_ms / 1000:.1f}s 少于 {cfg.min_duration_ms // 1000}s，"
            "转写结果可能不完整（PRD 6.2）"
        )

    return {
        "ok": not reasons,
        "duration_ms": duration_ms,
        "rms": round(rms, 1),
        "silent_ratio": round(near_zero_ratio, 4),
        "reasons": reasons,
        "warnings": warnings,
    }
