.PHONY: dev api clean

# 启动 Textual TUI（直接模式，无需 FastAPI 后端）
dev:
	python3 -m vaxport

# 单独启动 FastAPI 后端（如需 HTTP API）
api:
	python3 -m uvicorn vaxport.api.server:app --host 127.0.0.1 --port 8931

# 清理
clean:
	rm -rf src/vaxport/__pycache__ src/vaxport/**/__pycache__