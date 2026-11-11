FROM python:3.11-slim

WORKDIR /srv/site
COPY html ./html
COPY redirects.json ./redirects.json
COPY server.py ./server.py

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/index.html', timeout=3).status==200 else 1)"

# 静态公告站 + 跳转规则：标准库实现，无需任何第三方依赖
CMD ["python", "server.py", "--bind", "0.0.0.0", "--port", "8080"]
