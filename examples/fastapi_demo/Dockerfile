# The build context is the repo root (docker-compose.yml sets it), so the demo
# runs the Soniq source in this checkout.
FROM python:3.12-slim
WORKDIR /app
COPY . /soniq-src
RUN pip install --no-cache-dir "/soniq-src[dashboard]" \
    -r /soniq-src/examples/fastapi_demo/requirements.txt
COPY examples/fastapi_demo/main.py /app/main.py
