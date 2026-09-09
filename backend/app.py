"""Compatibility entry point: run the enterprise API as ``backend.app:app``."""
import os
import sys

# 确保项目根目录在 Python 路径中（直接运行 python backend/app.py 时需要）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.main import app

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn
    # 监听 127.0.0.1:8000 — 仅本机访问，避免 0.0.0.0 在浏览器中报 ERR_ADDRESS_INVALID
    # 如需局域网内其他机器访问，把 host 改回 "0.0.0.0"
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8000, reload=False)
