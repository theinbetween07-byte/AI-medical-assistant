"""CapsWriter-Offline 引擎適配層（可選擴展模塊）。

擴展包自帶完整運行時與模型，安裝在 ``app/models/ext/<dir>``。本模塊負責：

1. 拉起/復用 ``start_server.exe``（127.0.0.1:6016）
2. 用 ``start_client.exe <wav>`` 做文件轉寫，讀取同目錄生成的 ``.txt``
3. 把純文本結果適配成與 faster-whisper 一致的轉寫結構

CapsWriter 輸出不含時間戳，這裡按段落均分音頻時長給出估算的 start/end，
前端只用於定位片段，不做精確對齊。
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
import wave
from pathlib import Path
from typing import Optional

from loguru import logger

_SERVER_READY_TIMEOUT = 180  # 首次啟動需加載 1.4GB 模型
_PORT = 6016


class CapsEngine:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.proc: Optional[subprocess.Popen] = None
        self._started_at = 0.0

    # ------------------------------------------------------------ 進程管理
    @property
    def server_exe(self) -> Path:
        return self.root / "start_server.exe"

    @property
    def client_exe(self) -> Path:
        return self.root / "start_client.exe"

    def _port_open(self, host: str = "127.0.0.1", port: int = _PORT) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0

    def ensure_running(self, timeout: int = _SERVER_READY_TIMEOUT) -> None:
        """確保 CapsWriter 服務在跑；已在跑則直接複用。"""
        if self._port_open():
            return
        if not self.server_exe.exists():
            raise FileNotFoundError(f"扩展模块文件不完整：缺少 {self.server_exe}")

        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        logger.info(f"[CapsWriter] 啟動引擎：{self.server_exe}")
        self.proc = subprocess.Popen(
            [str(self.server_exe)],
            cwd=str(self.root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        self._started_at = time.time()

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._port_open():
                logger.success(
                    f"[CapsWriter] 引擎就緒，耗時 {time.time() - self._started_at:.1f}s"
                )
                return
            if self.proc.poll() is not None:
                raise RuntimeError(
                    "CapsWriter 引擎啟動失敗（进程已退出），请检查扩展是否完整解压。"
                )
            time.sleep(0.5)
        raise RuntimeError(f"CapsWriter 引擎啟動超時（>{timeout}s）")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            logger.info("[CapsWriter] 停止引擎進程")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    # ------------------------------------------------------------ 轉寫
    @staticmethod
    def _wav_duration(path: str) -> float:
        try:
            with wave.open(path, "rb") as wf:
                return wf.getnframes() / float(wf.getframerate() or 16000)
        except Exception:
            return 0.0

    @staticmethod
    def _split_text(text: str) -> list[str]:
        lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
        lines = [ln for ln in lines if ln]
        if len(lines) > 1:
            return lines
        # 單段長文本：按中英文句末標點切分
        parts = re.split(r"(?<=[。！？!?；;])\s*", " ".join(lines))
        return [p.strip() for p in parts if p.strip()]

    def transcribe(self, wav_path: str, language: str = "auto") -> dict:
        if not self.client_exe.exists():
            raise FileNotFoundError(f"扩展模块文件不完整：缺少 {self.client_exe}")
        self.ensure_running()

        duration = self._wav_duration(wav_path)
        # 客户端用 Path.with_suffix('.txt') 落盘，双后缀（如 a.wav.convert.wav）会得到
        # a.wav.convert.txt，因此两种路径都要认。
        candidates = [Path(wav_path + ".txt"), Path(wav_path).with_suffix(".txt")]
        for old in candidates:
            if old.exists():
                old.unlink()

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        t0 = time.time()
        timeout = max(300, int(duration * 4) + 120)
        result = subprocess.run(
            [str(self.client_exe), wav_path],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=flags,
        )
        elapsed = time.time() - t0

        text = ""
        for path in candidates:
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    break
        if not text:
            detail = (result.stderr or result.stdout or "").strip()[-400:]
            raise RuntimeError(f"CapsWriter 未生成转写结果：{detail}")

        parts = self._split_text(text)
        seg_dur = (duration / len(parts)) if parts and duration else 0.0
        segments = []
        for i, part in enumerate(parts):
            start = round(i * seg_dur, 2)
            end = round((i + 1) * seg_dur, 2)
            segments.append({"id": i, "start": start, "end": end, "text": part})

        logger.info(
            f"[CapsWriter] 轉寫完成：{len(segments)} 段，音頻 {duration:.1f}s，耗時 {elapsed:.1f}s"
        )
        return {
            "language": language if language not in (None, "", "auto") else "auto",
            "language_probability": 0.0,
            "duration": round(duration, 2),
            "text": text,
            "segments": segments,
            "elapsed": round(elapsed, 2),
            "template": "",
            "model": "caps-qwen3",
        }
