"""vaxport API 服务入口 — 供 Tauri sidecar 调用"""
import uvicorn
from vaxport.api.server import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8931, log_level="warning")