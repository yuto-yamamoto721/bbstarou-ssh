FROM python:3.11-slim

WORKDIR /app

# 依存ライブラリのインストール
RUN pip install --no-cache-dir paramiko

# ファイルのコピー
COPY app.py .

# ポートの公開と実行
EXPOSE 8080
CMD ["python3", "app.py"]
