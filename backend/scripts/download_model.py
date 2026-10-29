"""
獨立模型下載腳本 - 用 hf_transfer 多線程加速

用法：
    python scripts/download_model.py [model_id]

需要先安裝：
    pip install hf_transfer
"""

import os
import sys
import time
from pathlib import Path

# 確保項目根目錄在 sys.path 中
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def main():
    # 啟用 hf_transfer 多線程下載（必須在 import huggingface_hub 之前）
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")

    from huggingface_hub import snapshot_download

    model_id = sys.argv[1] if len(sys.argv) > 1 else "Systran/faster-whisper-medium"
    print(f"[download_model] 開始下載 {model_id}")
    print(f"[download_model] hf_transfer 多線程模式已啟用")
    print(f"[download_model] 預計大小：~1.5GB，預計耗時 3-10 分鐘")

    t0 = time.time()
    try:
        path = snapshot_download(
            repo_id=model_id,
            cache_dir=os.path.expanduser("~/.cache/huggingface/hub"),
            local_dir_use_symlinks=False,
        )
        elapsed = time.time() - t0
        print(f"[download_model] ✓ 下載完成，耗時 {elapsed/60:.1f} 分鐘")
        print(f"[download_model] 路徑：{path}")

        total = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                total += os.path.getsize(fp)
        print(f"[download_model] 總大小：{total/1024/1024:.1f} MB")
    except KeyboardInterrupt:
        print("[download_model] 用戶中斷")
        sys.exit(1)
    except Exception as e:
        print(f"[download_model] ✗ 下載失敗：{e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
