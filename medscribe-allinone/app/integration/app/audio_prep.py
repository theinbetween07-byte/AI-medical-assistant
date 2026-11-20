"""ASR 音频预处理：诊断 -> 条件化处理 -> 前后指标对比。

设计原则（对应 PRD FR-01 / 技术设计 4.1）：

1. **先测量后处理**：只有确实存在对应问题才启用算子，避免"每条音频都跑一遍重链"。
2. **只重采样一次**：解码即统一到目标采样率 / 单声道 / 16 bit PCM，后续全部在该域完成。
3. **保守参数**：降噪上限 12 dB；默认只裁首尾静音和超长中间静音；响度目标可配置。
4. **可回退**：预处理失败由调用方回退原始音频，不阻断转写。
5. **可复现**：每一步的判定依据、参数、滤镜串都写进 JSON 报告。

依赖：ffmpeg / ffprobe（随包附带），无 numpy / scipy 依赖。
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from .config import Settings

PREP_VERSION = "1.0"

# 削波判定：峰值贴近满幅，且出现连续相同样本（flat factor）或满幅样本计数明显
CLIP_PEAK_DBFS = -0.3
CLIP_FLAT_FACTOR = 0.001
CLIP_PEAK_COUNT = 16


class PrepError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 进程

def _run(cfg: Settings, args: list[str], timeout: Optional[int] = None) -> str:
    """执行 ffmpeg，合并 stdout/stderr 返回（ametadata 写 stdout，日志写 stderr）。"""
    cmd = [cfg.ffmpeg, "-hide_banner", "-nostdin", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout or cfg.ffmpeg_timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PrepError(
            f"找不到 ffmpeg（当前配置：{cfg.ffmpeg}）。请安装 ffmpeg 或设置 MSHK_FFMPEG。"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PrepError(f"音频预处理超时（>{timeout or cfg.ffmpeg_timeout_s}s）") from exc

    text = (proc.stdout or b"").decode("utf-8", errors="replace")
    text += (proc.stderr or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        tail = text.strip().splitlines()[-4:]
        raise PrepError("ffmpeg 预处理失败：" + " | ".join(tail))
    return text


def _run_probe(cfg: Settings, src: Path) -> dict:
    cmd = [
        cfg.ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(src),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except FileNotFoundError as exc:
        raise PrepError(
            f"找不到 ffprobe（当前配置：{cfg.ffprobe}）。请安装 ffmpeg 或设置 MSHK_FFPROBE。"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PrepError("ffprobe 超时") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise PrepError(f"无法解析音频（ffprobe）：{detail[-200:]}")
    try:
        return json.loads((proc.stdout or b"{}").decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise PrepError("ffprobe 返回内容不是合法 JSON") from exc


# --------------------------------------------------------------------------- 诊断

def probe(cfg: Settings, src: Path) -> dict:
    """读取容器/编码层属性。"""
    data = _run_probe(cfg, src)
    streams = [s for s in (data.get("streams") or []) if s.get("codec_type") == "audio"]
    if not streams:
        raise PrepError("文件中没有音频流")
    audio = streams[0]
    fmt = data.get("format") or {}

    def _f(value, default=0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    bits = audio.get("bits_per_sample") or audio.get("bits_per_raw_sample") or 0
    try:
        bits = int(bits)
    except (TypeError, ValueError):
        bits = 0

    return {
        "path": str(src),
        "size_bytes": int(_f(fmt.get("size"))),
        "format_name": fmt.get("format_name") or "",
        "codec": audio.get("codec_name") or "",
        "sample_fmt": audio.get("sample_fmt") or "",
        "sample_rate": int(_f(audio.get("sample_rate"))),
        "channels": int(_f(audio.get("channels"))),
        "channel_layout": audio.get("channel_layout") or "",
        "bits_per_sample": bits,
        "duration_s": round(_f(fmt.get("duration")) or _f(audio.get("duration")), 3),
        "bit_rate": int(_f(fmt.get("bit_rate"))),
    }


_ASTATS_FIELDS = {
    "peak_dbfs": r"Peak level dB:\s*(-?(?:inf|[\d.]+))",
    "rms_dbfs": r"RMS level dB:\s*(-?(?:inf|[\d.]+))",
    "flat_factor": r"Flat factor:\s*(-?[\d.]+)",
    "peak_count": r"Peak count:\s*(-?[\d.]+)",
    "dc_offset": r"DC offset:\s*(-?[\d.]+)",
    "crest_factor": r"Crest factor:\s*(-?[\d.]+)",
}


def _num(token: str) -> float:
    if token in ("inf", "+inf"):
        return 120.0
    if token == "-inf":
        return -120.0
    try:
        return float(token)
    except ValueError:
        return 0.0


def _last(text: str, pattern: str) -> Optional[float]:
    found = re.findall(pattern, text)
    return _num(found[-1]) if found else None


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * max(0.0, min(1.0, q))
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _silences(text: str) -> list[tuple[float, float]]:
    """从 silencedetect 输出还原静音区间（按出现顺序配对）。"""
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", text)]
    out: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else None
        if end is None or end <= start:
            continue
        out.append((max(0.0, start), end))
    return out


def _adaptive_silence_db(cfg: Settings, m: dict) -> float:
    """VAD 阈值随录音电平自适应。

    固定 -38 dBFS 对轻声录音（整体约 -32 LUFS）会把部分语音误判为静音并裁掉。
    用语音电平（帧 RMS P90）向下 22 dB 作为阈值：正常音量下仍约 -38 dBFS，
    轻声录音自动下探（下限 -55 dBFS）。
    """
    base = float(cfg.prep_silence_db)
    speech = m.get("speech_level_dbfs")
    if speech is None:
        return base
    return max(-55.0, min(-30.0, float(speech) - 22.0))


def _detect_silences(
    cfg: Settings, src: Path, threshold_db: float, min_s: float
) -> list[tuple[float, float]]:
    text = _run(
        cfg,
        [
            "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
            "-af", f"silencedetect=noise={threshold_db:g}dB:d={max(0.15, float(min_s)):g}",
            "-f", "null", "-",
        ],
    )
    return _silences(text)


def _apply_silences(m: dict, total_s: float, silences: list[tuple[float, float]]) -> None:
    """用新的静音检测结果刷新派生指标，保证报告前后一致。"""
    silence_duration = sum(e - s for s, e in silences)
    regions = _speech_regions(total_s, silences)
    lengths = sorted(b - a for a, b in regions)
    m["silence_ratio"] = round(
        max(0.0, min(1.0, silence_duration / total_s)) if total_s else 0.0, 4
    )
    m["silence_segments"] = len(silences)
    m["speech_duration_s"] = round(sum(b - a for a, b in regions), 2)
    m["speech_ratio"] = round((m["speech_duration_s"] / total_s) if total_s else 0.0, 4)
    m["speech_segments"] = len(regions)
    m["median_speech_segment_s"] = round(lengths[len(lengths) // 2], 2) if lengths else 0.0
    m["max_silence_s"] = round(max((e - s for s, e in silences), default=0.0), 2)
    m["_silences"] = silences
    m["_regions"] = regions


def _speech_regions(total_s: float, silences: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    regions: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start > cursor + 0.02:
            regions.append((cursor, min(start, total_s)))
        cursor = max(cursor, end)
    if total_s > cursor + 0.02:
        regions.append((cursor, total_s))
    return [(a, b) for a, b in regions if b - a > 0.02]


def measure(cfg: Settings, src: Path, duration_hint: float = 0.0) -> dict:
    """在统一 16 kHz / 单声道域测量客观指标。

    说明：统一域是为了让"处理前 / 处理后"可比；因此处测得的采样率不是源文件采样率，
    源文件采样率请取 probe()。
    """
    silence_min = max(0.15, float(cfg.prep_silence_min_s))
    filters = (
        "astats=metadata=1:reset=1:length=0.1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-,"
        "ebur128=peak=true:framelog=quiet,"
        f"silencedetect=noise={cfg.prep_silence_db}dB:d={silence_min}"
    )
    text = _run(
        cfg,
        [
            "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000",
            "-af", filters,
            "-f", "null", "-",
        ],
    )

    peak = _last(text, _ASTATS_FIELDS["peak_dbfs"]) or 0.0
    rms = _last(text, _ASTATS_FIELDS["rms_dbfs"]) or 0.0
    flat = _last(text, _ASTATS_FIELDS["flat_factor"]) or 0.0
    peak_count = _last(text, _ASTATS_FIELDS["peak_count"]) or 0.0
    dc = _last(text, _ASTATS_FIELDS["dc_offset"]) or 0.0
    crest = _last(text, _ASTATS_FIELDS["crest_factor"]) or 0.0
    lufs = _last(text, r"I:\s*(-?[\d.]+)\s*LUFS")
    lra = _last(text, r"LRA:\s*(-?[\d.]+)\s*LU")
    true_peak = _last(text, r"Peak:\s*(-?[\d.]+)\s*dBFS")

    frame_rms = [float(x) for x in re.findall(r"RMS_level=(-?(?:inf|[\d.]+))", text)]
    frame_rms = [_num(v) for v in frame_rms]
    frame_rms = [v for v in frame_rms if v > -90.0]
    noise_floor = _percentile(frame_rms, 0.10) if len(frame_rms) >= 10 else None
    speech_level = _percentile(frame_rms, 0.90) if len(frame_rms) >= 10 else None
    snr = None
    if noise_floor is not None and speech_level is not None:
        snr = max(0.0, speech_level - noise_floor)

    silences = _silences(text)
    total_s = float(duration_hint or 0.0)
    if total_s <= 0:
        # 用最后一段静音结束时间兜底（无法精确，但只为比例估算）
        total_s = max([e for _, e in silences], default=0.0)
    silence_duration = sum(e - s for s, e in silences)
    silence_ratio = (silence_duration / total_s) if total_s > 0 else 0.0
    regions = _speech_regions(total_s, silences) if total_s > 0 else []
    speech_duration = sum(b - a for a, b in regions)
    lengths = sorted(b - a for a, b in regions)

    clip_suspect = bool(
        peak >= CLIP_PEAK_DBFS and (flat >= CLIP_FLAT_FACTOR or peak_count >= CLIP_PEAK_COUNT)
    )

    return {
        "peak_dbfs": round(peak, 2),
        "rms_dbfs": round(rms, 2),
        "integrated_lufs": None if lufs is None else round(lufs, 2),
        "true_peak_dbfs": None if true_peak is None else round(true_peak, 2),
        "lra_lu": None if lra is None else round(lra, 2),
        "dc_offset": round(dc, 6),
        "crest_factor": round(crest, 3),
        "flat_factor": round(flat, 6),
        "peak_count": int(round(peak_count)),
        "clip_suspect": clip_suspect,
        "noise_floor_dbfs": None if noise_floor is None else round(noise_floor, 2),
        "speech_level_dbfs": None if speech_level is None else round(speech_level, 2),
        "snr_db_est": None if snr is None else round(snr, 2),
        "silence_ratio": round(max(0.0, min(1.0, silence_ratio)), 4),
        "silence_segments": len(silences),
        "speech_duration_s": round(speech_duration, 2),
        "speech_ratio": round((speech_duration / total_s) if total_s > 0 else 0.0, 4),
        "speech_segments": len(regions),
        "median_speech_segment_s": round(lengths[len(lengths) // 2], 2) if lengths else 0.0,
        "max_silence_s": round(max((e - s for s, e in silences), default=0.0), 2),
        "_silences": silences,
        "_regions": regions,
        "_total_s": round(total_s, 3),
    }


def classify_scene(info: dict, m: dict) -> dict:
    """启发式场景判断。

    这不是说话人分离，也无法真正测量房间混响；只给出"疑似"结论与依据，
    供后续参数选择和人工复核使用。
    """
    signals: list[str] = []
    scores = {"telephone": 0, "far_field": 0, "multi_speaker": 0}

    sr = int(info.get("sample_rate") or 0)
    channels = int(info.get("channels") or 0)
    snr = m.get("snr_db_est")
    median_seg = float(m.get("median_speech_segment_s") or 0.0)
    segments = int(m.get("speech_segments") or 0)
    duration = float(info.get("duration_s") or 0.0)

    if sr and sr <= 8000:
        scores["telephone"] += 3
        signals.append(f"采样率 {sr} Hz：窄带，典型电话/对讲录音")
    if info.get("codec") in {"amr_nb", "amr_wb", "gsm", "g729"}:
        scores["telephone"] += 2
        signals.append(f"编码 {info.get('codec')}：通信语音编码")

    if snr is not None and snr < 15:
        scores["far_field"] += 2
        signals.append(f"估计 SNR {snr} dB：低信噪比，提示远场/嘈杂环境")
    if m.get("silence_ratio", 0) > 0.35 and duration > 30:
        scores["far_field"] += 1
        signals.append(f"静音占比 {round(m.get('silence_ratio', 0) * 100)}%：环境底噪下语音稀疏")

    if segments >= 8 and 0 < median_seg < 2.5:
        scores["multi_speaker"] += 2
        signals.append(f"{segments} 个语音段、中位段长 {median_seg}s：疑似对话/多人交替")
    if channels >= 2:
        signals.append(f"{channels} 声道：可保留多声道信息，但 ASR 仍按单声道输入")

    best = max(scores, key=scores.get)
    score = scores[best]
    if score >= 3:
        label, confidence = best, "medium"
    elif score >= 2:
        label, confidence = best, "low"
    else:
        label, confidence = "near_field_single", "low"
        signals.append("未发现电话/远场/多人的强特征，按近场单人处理")

    return {
        "label": label,
        "confidence": confidence,
        "scores": scores,
        "signals": signals,
        "is_estimate": True,
        "note": "启发式判断，非说话人分离；多人场景请在上游启用 diarization 或人工复核。",
    }


# --------------------------------------------------------------------------- 计划

def _build_pre_chain(cfg: Settings, m: dict, scene: dict) -> tuple[list[str], list[dict]]:
    """返回 (滤镜列表, 决策列表)。只包含 A->A 算子。"""
    filters: list[str] = []
    decisions: list[dict] = []

    hp = int(cfg.prep_highpass_hz)
    if hp > 0:
        filters.append(f"highpass=f={hp}:poles=2")
        decisions.append({
            "step": "highpass",
            "action": "on",
            "tool": "ffmpeg highpass (2 阶 Butterworth)",
            "params": {"f_hz": hp},
            "reason": "去除直流/次声与 80 Hz 以下无语音信息的低频隆隆；对语音基频影响可忽略",
        })
    else:
        decisions.append({"step": "highpass", "action": "off", "reason": "配置关闭"})

    # ---- 降噪：仅当噪声底偏高或 SNR 偏低
    noise_floor = m.get("noise_floor_dbfs")
    snr = m.get("snr_db_est")
    want = str(cfg.prep_denoise or "auto").lower()
    use_denoise = False
    if want == "on":
        use_denoise = True
        reason = "配置强制开启"
    elif want == "off":
        reason = "配置关闭"
    else:
        # 只用"估计 SNR"作为主判据；噪声底绝对值受增益影响，单独使用会误判。
        # 例外：噪声底高于 -30 dBFS 时几乎不可能正常转写，直接降噪。
        low_snr = snr is not None and snr < float(cfg.prep_snr_gate_db)
        very_noisy = noise_floor is not None and noise_floor > -30.0
        use_denoise = bool(low_snr or very_noisy)
        reason = (
            f"估计 SNR {snr} dB（阈值 {cfg.prep_snr_gate_db}）/ "
            f"噪声底 {noise_floor} dBFS（强制线 -30）"
            if (noise_floor is not None and snr is not None)
            else "噪声指标缺失，按保守策略跳过"
        )

    if use_denoise:
        nr = max(0.0, min(12.0, float(cfg.prep_denoise_nr)))
        # afftdn 的 nf 是"初始噪声底"。固定 -45 dB 对高噪声录音（噪声底 -19 dB）
        # 会严重失配；按实测噪声底 +2 dB 初始化，并限制在 [-80, -20]。
        nf = -45.0
        source = "default"
        if noise_floor is not None:
            nf = max(-80.0, min(-20.0, float(noise_floor) + 2.0))
            source = "measured"
        filters.append(f"afftdn=nr={nr:g}:nf={nf:g}:tn=1")
        decisions.append({
            "step": "denoise",
            "action": "on",
            "tool": "ffmpeg afftdn（FFT 谱减 + 噪声跟踪）",
            "params": {
                "nr_db": nr,
                "noise_floor_db": round(nf, 1),
                "noise_floor_source": source,
                "track_noise": 1,
            },
            "reason": reason,
            "risk": "降噪过强会削弱弱辅音/气声；nr 上限 12 dB，且只在噪声确实偏高时启用",
        })
    else:
        decisions.append({"step": "denoise", "action": "off", "reason": reason})

    # ---- 去混响：ffmpeg 无 WPE，用轻量小波降噪抑制混响尾；只在低 SNR 时启用
    want_reverb = str(cfg.prep_dereverb or "auto").lower()
    use_reverb = False
    if want_reverb == "on":
        use_reverb = True
        reason = "配置强制开启"
    elif want_reverb == "off":
        reason = "配置关闭"
    else:
        use_reverb = bool(snr is not None and snr < float(cfg.prep_dereverb_snr_gate_db))
        reason = f"估计 SNR {snr} dB 低于 {cfg.prep_dereverb_snr_gate_db} dB，提示混响/尾音风险"
    if use_reverb:
        filters.append("afwtdn=sigma=0.03:levels=6:percent=0")
        decisions.append({
            "step": "dereverb",
            "action": "on",
            "tool": "ffmpeg afwtdn（小波域抑制）",
            "params": {"sigma": 0.03, "levels": 6, "percent": 0},
            "reason": reason,
            "risk": "ffmpeg 原生无真正的去混响（WPE/NARA）；小波抑制只能减轻尾音，过强会发闷。"
                     "真混响环境建议改用 DeepFilterNet / NARA-WPE 离线处理。",
        })
    else:
        decisions.append({"step": "dereverb", "action": "off", "reason": reason})

    if m.get("clip_suspect"):
        decisions.append({
            "step": "clip_guard",
            "action": "warn",
            "reason": "检测到峰值贴满幅（可能已削波）；削波不可逆，预处理只能避免二次失真",
            "params": {"peak_dbfs": m.get("peak_dbfs"), "true_peak_dbfs": m.get("true_peak_dbfs")},
        })

    return filters, decisions


def _plan_chunks(cfg: Settings, total_s: float, regions: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], dict]:
    """决定保留哪些时间区间（秒）。

    默认只做两件事：
    - 去掉首尾静音（保留 250 ms 余量）
    - 把超过 8 s 的中间静音压缩，两侧各保留 250 ms

    这样既减少 Whisper 在长静音上的幻觉，又不会切断弱起音。
    """
    if not cfg.prep_vad or total_s <= 0 or not regions:
        return [(0.0, total_s)], {"applied": False, "reason": "VAD 关闭或无有效语音段", "time_map": []}

    pad = max(0.0, float(cfg.prep_keep_pad_ms) / 1000.0)
    min_trim = max(0.0, float(getattr(cfg, "prep_min_trim_s", 1.5)))
    # 首尾静音太短时不裁：收益低，但会改变 ASR 分段边界（混响下尤其不稳定）
    start = max(0.0, regions[0][0] - pad) if regions[0][0] >= min_trim else 0.0
    tail = total_s - regions[-1][1]
    end = min(total_s, regions[-1][1] + pad) if tail >= min_trim else total_s

    chunks: list[tuple[float, float]] = []
    removed_s = 0.0
    if cfg.prep_trim_mid:
        gap_threshold = max(1.0, float(cfg.prep_mid_gap_ms) / 1000.0)
        cursor = start
        for i in range(1, len(regions)):
            gap_start = regions[i - 1][1]
            gap_end = regions[i][0]
            if gap_end - gap_start > gap_threshold:
                chunks.append((cursor, min(gap_start + pad, gap_end)))
                cursor = max(cursor, gap_end - pad)
                removed_s += (gap_end - gap_start) - 2 * pad
        chunks.append((cursor, end))
    else:
        chunks.append((start, end))

    chunks = [(round(a, 3), round(b, 3)) for a, b in chunks if b - a > 0.02]
    if not chunks:
        return [(0.0, total_s)], {"applied": False, "reason": "切分结果为空，回退整段", "time_map": []}

    time_map: list[dict] = []
    proc_ms = 0.0
    for a, b in chunks:
        dur_ms = (b - a) * 1000.0
        time_map.append({
            "proc_start_ms": int(round(proc_ms)),
            "proc_end_ms": int(round(proc_ms + dur_ms)),
            "orig_start_ms": int(round(a * 1000)),
            "orig_end_ms": int(round(b * 1000)),
        })
        proc_ms += dur_ms

    return chunks, {
        "applied": len(chunks) > 1 or start > 0.05 or end < total_s - 0.05,
        "reason": "去除首尾静音；压缩超长中间静音",
        "chunks": [[round(a, 3), round(b, 3)] for a, b in chunks],
        "removed_s": round(max(0.0, removed_s), 3),
        "time_map": time_map,
    }


def _loudnorm_measure(cfg: Settings, src: Path, pre_chain: list[str]) -> Optional[dict]:
    chain = ",".join(pre_chain) if pre_chain else "anull"
    params = (
        f"loudnorm=I={cfg.prep_target_lufs:g}:TP={cfg.prep_true_peak:g}:"
        f"LRA={cfg.prep_lra:g}:print_format=json"
    )
    text = _run(
        cfg,
        [
            "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000",
            "-af", f"{chain},{params}",
            "-f", "null", "-",
        ],
    )
    matches = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', text, flags=re.S)
    if not matches:
        return None
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if not all(k in data for k in keys):
        return None
    return data


def _loudnorm_filter(cfg: Settings, measured: Optional[dict]) -> tuple[str, dict]:
    base = f"I={cfg.prep_target_lufs:g}:TP={cfg.prep_true_peak:g}:LRA={cfg.prep_lra:g}"
    if not measured:
        return f"{base}:linear=false", {"mode": "dynamic", "measured": None}
    values = {
        "measured_I": measured.get("input_i"),
        "measured_LRA": measured.get("input_lra"),
        "measured_TP": measured.get("input_tp"),
        "measured_thresh": measured.get("input_thresh"),
        "offset": measured.get("target_offset", "0"),
    }
    parts = ":".join(f"{k}={v}" for k, v in values.items() if v not in (None, ""))
    return f"{base}:linear=true:{parts}", {
        "mode": "linear-preferred",
        "measured": {k: float(v) for k, v in values.items() if v not in (None, "")},
    }


def _build_graph(pre_chain: list[str], chunks: list[tuple[float, float]], loudnorm: str) -> str:
    pre = ",".join(pre_chain) if pre_chain else "anull"
    n = len(chunks)
    head = (
        f"[0:a]{pre},aformat=sample_fmts=fltp:sample_rates=16000:channel_layouts=mono"
    )
    if n == 1:
        start, end = chunks[0]
        return (
            f"{head},atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
            f"loudnorm={loudnorm}[out]"
        )
    outputs = "".join(f"[s{i}]" for i in range(n))
    parts = [f"{head},asplit={n}{outputs}"]
    for i, (start, end) in enumerate(chunks):
        parts.append(
            f"[s{i}]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[c{i}]"
        )
    parts.append("".join(f"[c{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cat]")
    parts.append(f"[cat]loudnorm={loudnorm}[out]")
    return ";".join(parts)


# --------------------------------------------------------------------------- 对外接口

def preprocess(
    cfg: Settings,
    src: Path,
    dst: Path,
    report_path: Optional[Path] = None,
) -> dict:
    """对 src 执行诊断 + 条件化预处理，输出 dst（16 kHz / mono / s16 WAV）。

    返回报告 dict；report_path 非空时同时落盘 JSON。
    """
    src = Path(src)
    dst = Path(dst)
    if not src.is_file():
        raise PrepError(f"找不到输入音频：{src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    info = probe(cfg, src)
    total_s = float(info.get("duration_s") or 0.0)
    if total_s <= 0:
        raise PrepError("无法读取音频时长")
    if total_s > float(cfg.max_duration_ms) / 1000.0 + 1:
        raise PrepError(
            f"音频 {total_s:.1f}s 超过 {cfg.max_duration_ms // 60000} 分钟上限，已拒绝预处理"
        )

    before = measure(cfg, src, total_s)
    # VAD 阈值按录音电平自适应，避免轻声录音被固定 -38 dBFS 误裁
    silence_db = _adaptive_silence_db(cfg, before)
    if abs(silence_db - float(cfg.prep_silence_db)) >= 2.0:
        adaptive_silences = _detect_silences(cfg, src, silence_db, cfg.prep_silence_min_s)
        if adaptive_silences or before.get("silence_segments"):
            _apply_silences(before, total_s, adaptive_silences)
    before["silence_threshold_db"] = round(silence_db, 2)
    scene = classify_scene(info, before)

    # 16 kHz 单声道 PCM 且各指标无需处理时，直接复制，避免二次编码
    already_ok = (
        int(info.get("sample_rate") or 0) == int(cfg.prep_target_sr)
        and int(info.get("channels") or 0) == 1
        and info.get("codec") == "pcm_s16le"
        and abs(float(before.get("integrated_lufs") or -120) - float(cfg.prep_target_lufs)) < 0.5
        and not before.get("clip_suspect")
        # 只有"没有任何需要处理的地方"才直通；有静音需要裁剪时不能跳过
        and before.get("silence_ratio", 0) < 0.05
        and before.get("max_silence_s", 0) < 1.0
        and (
            before.get("snr_db_est") is None
            or float(before["snr_db_est"]) >= float(cfg.prep_snr_gate_db)
        )
    )
    if already_ok:
        dst.write_bytes(src.read_bytes())
        after = measure(cfg, dst, total_s)
        report = _report(
            cfg, info, scene, before, after,
            decisions=[{"step": "passthrough", "action": "copy",
                        "reason": "已是目标格式且响度/峰值达标，跳过重编码"}],
            chain={"filter_complex": "", "pre_filters": [], "loudnorm": {"mode": "none"},
                   "trim": {"applied": False, "time_map": []}},
            chunks=[(0.0, total_s)],
            output=dst,
            elapsed_s=0.0,
        )
        _write_report(report, report_path)
        return report

    pre_chain, decisions = _build_pre_chain(cfg, before, scene)
    chunks, trim = _plan_chunks(cfg, total_s, before["_regions"])

    measured = _loudnorm_measure(cfg, src, pre_chain)
    loudnorm, loudnorm_meta = _loudnorm_filter(cfg, measured)
    decisions.append({
        "step": "loudness",
        "action": "on",
        "tool": "ffmpeg loudnorm（EBU R128，two-pass）",
        "params": {
            "I_lufs": cfg.prep_target_lufs,
            "TP_dbfs": cfg.prep_true_peak,
            "LRA_lu": cfg.prep_lra,
            **loudnorm_meta,
        },
        "reason": "统一不同录音的响度，避免 VAD/能量阈值和音量增益差异影响识别",
        "risk": "若线性增益不足以满足 TP 限制，loudnorm 会回退到动态模式并轻度压缩；已记录并复查 LRA",
    })

    decisions.append({
        "step": "vad_trim",
        "action": "on" if trim.get("applied") else "off",
        "tool": "ffmpeg silencedetect + atrim/concat",
        "params": {
            "silence_threshold_db": round(silence_db, 1),
            "adaptive_threshold": abs(silence_db - float(cfg.prep_silence_db)) >= 2.0,
            "min_silence_s": cfg.prep_silence_min_s,
            "keep_pad_ms": cfg.prep_keep_pad_ms,
            "mid_gap_ms": cfg.prep_mid_gap_ms,
            "chunks": len(chunks),
            "removed_s": trim.get("removed_s", 0.0),
        },
        "reason": trim.get("reason", ""),
        "risk": "裁切过激会切断弱起音；仅处理首尾和 >8 s 的超长静音，并保留 250 ms 余量",
    })

    graph = _build_graph(pre_chain, chunks, loudnorm)
    _run(
        cfg,
        [
            "-y",
            "-i", str(src),
            "-filter_complex", graph,
            "-map", "[out]",
            "-vn",
            "-ar", str(cfg.prep_target_sr),
            "-ac", "1",
            "-c:a", "pcm_s16le",
            str(dst),
        ],
    )
    if not dst.is_file() or dst.stat().st_size <= 44:
        raise PrepError("预处理输出为空，已回退原始音频")

    out_info = probe(cfg, dst)
    after = measure(cfg, dst, float(out_info.get("duration_s") or 0.0))
    report = _report(
        cfg, info, scene, before, after,
        decisions=decisions,
        chain={
            "filter_complex": graph,
            "pre_filters": pre_chain,
            "loudnorm": loudnorm,
            "loudnorm_meta": loudnorm_meta,
            "trim": trim,
            "ffmpeg": cfg.ffmpeg,
            "ffmpeg_cmd": (
                f'"{cfg.ffmpeg}" -y -i "{src}" -filter_complex "{graph}" '
                f'-map "[out]" -vn -ar {cfg.prep_target_sr} -ac 1 -c:a pcm_s16le "{dst}"'
            ),
        },
        chunks=chunks,
        output=dst,
        elapsed_s=0.0,
    )
    _write_report(report, report_path)
    return report


def _report(
    cfg: Settings,
    info: dict,
    scene: dict,
    before: dict,
    after: dict,
    *,
    decisions: list[dict],
    chain: dict,
    chunks: list[tuple[float, float]],
    output: Path,
    elapsed_s: float,
) -> dict:
    out_info = {
        "path": str(output),
        "size_bytes": output.stat().st_size if output.is_file() else 0,
        "sample_rate": int(cfg.prep_target_sr),
        "channels": 1,
        "bits_per_sample": 16,
        "duration_s": round(sum(b - a for a, b in chunks), 3),
    }

    def _delta(key: str) -> Optional[float]:
        a, b = before.get(key), after.get(key)
        if a is None or b is None:
            return None
        return round(float(b) - float(a), 2)

    warnings: list[str] = []
    if before.get("clip_suspect"):
        warnings.append("输入存在削波特征，削波失真不可逆，转写结果可能受影响")
    if before.get("silence_ratio", 0) > 0.6:
        warnings.append("静音占比超过 60%，请确认录音是否有效")
    if before.get("snr_db_est") is not None and before["snr_db_est"] < 10:
        warnings.append("估计 SNR 低于 10 dB，强降噪也难恢复，建议重录或启用更强的去噪模型")
    if before.get("speech_duration_s") and after.get("speech_duration_s"):
        lost = before["speech_duration_s"] - after["speech_duration_s"]
        if lost > max(2.0, before["speech_duration_s"] * 0.2):
            warnings.append(
                f"有效语音时长减少 {lost:.1f}s（>{20}%），请检查 VAD 阈值是否过激"
            )
    if (
        after.get("lra_lu") is not None
        and before.get("lra_lu") is not None
        and before["lra_lu"] > 1
        and after["lra_lu"] < before["lra_lu"] * 0.7
    ):
        warnings.append(
            f"LRA 由 {before['lra_lu']} 降至 {after['lra_lu']} LU，响度归一化可能触发了动态压缩"
        )

    return {
        "version": PREP_VERSION,
        "created_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "input": info,
        "output": out_info,
        "scene": scene,
        "diagnosis": {k: v for k, v in before.items() if not k.startswith("_")},
        "result": {k: v for k, v in after.items() if not k.startswith("_")},
        "delta": {
            "sample_rate": f"{info.get('sample_rate')} -> {cfg.prep_target_sr}",
            "channels": f"{info.get('channels')} -> 1",
            "integrated_lufs": _delta("integrated_lufs"),
            "snr_db_est": _delta("snr_db_est"),
            "silence_ratio": _delta("silence_ratio"),
            "speech_duration_s": _delta("speech_duration_s"),
            "duration_s": round(out_info["duration_s"] - float(info.get("duration_s") or 0), 2),
            "dc_offset": _delta("dc_offset"),
            "max_silence_s": _delta("max_silence_s"),
        },
        "decisions": decisions,
        "chain": chain,
        "safety": {
            "target_format": f"{cfg.prep_target_sr} Hz / mono / pcm_s16le",
            "max_denoise_db": 12,
            "resample_count": 1,
            "notes": [
                "所有处理只做一次重采样，避免多次 resample 累积失真",
                "不启用强压缩/限幅/EQ；仅保留一阶高通与响度归一化",
                "多人说话未做分离，仅标记疑似场景",
            ],
        },
        "warnings": warnings,
        "elapsed_s": round(elapsed_s, 3),
    }


def _write_report(report: dict, path: Optional[Path]) -> None:
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def map_time_ms(ms: int, time_map: list[dict]) -> int:
    """把处理后时间轴映射回原始时间轴（用于补偿 VAD 裁剪）。"""
    if not time_map:
        return int(ms)
    value = float(ms)
    for item in time_map:
        start = float(item.get("proc_start_ms") or 0)
        end = float(item.get("proc_end_ms") or 0)
        if start <= value <= end:
            return int(round(float(item.get("orig_start_ms") or 0) + (value - start)))
    last = time_map[-1]
    offset = float(last.get("orig_end_ms") or 0) - float(last.get("proc_end_ms") or 0)
    return int(round(value + offset))


def apply_time_map(segments: list[dict], time_map: list[dict]) -> list[dict]:
    """就地修正分段起止时间；返回同一列表。"""
    if not time_map:
        return segments
    for seg in segments:
        seg["start_ms"] = map_time_ms(int(seg.get("start_ms") or 0), time_map)
        seg["end_ms"] = max(
            seg["start_ms"],
            map_time_ms(int(seg.get("end_ms") or 0), time_map),
        )
    return segments


def summary(report: dict) -> dict:
    """给审计日志/接口返回用的精简摘要。"""
    before = report.get("diagnosis") or {}
    after = report.get("result") or {}
    return {
        "input": {
            "codec": (report.get("input") or {}).get("codec"),
            "sample_rate": (report.get("input") or {}).get("sample_rate"),
            "channels": (report.get("input") or {}).get("channels"),
            "duration_s": (report.get("input") or {}).get("duration_s"),
        },
        "output": {
            "sample_rate": (report.get("output") or {}).get("sample_rate"),
            "channels": (report.get("output") or {}).get("channels"),
            "duration_s": (report.get("output") or {}).get("duration_s"),
        },
        "scene": (report.get("scene") or {}).get("label"),
        "before": {
            "lufs": before.get("integrated_lufs"),
            "snr_db_est": before.get("snr_db_est"),
            "silence_ratio": before.get("silence_ratio"),
            "speech_duration_s": before.get("speech_duration_s"),
        },
        "after": {
            "lufs": after.get("integrated_lufs"),
            "snr_db_est": after.get("snr_db_est"),
            "silence_ratio": after.get("silence_ratio"),
            "speech_duration_s": after.get("speech_duration_s"),
        },
        "steps": [
            f"{d.get('step')}={d.get('action')}" for d in (report.get("decisions") or [])
        ],
        "warnings": report.get("warnings") or [],
        "time_map": (report.get("chain") or {}).get("trim", {}).get("time_map") or [],
    }
