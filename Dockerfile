FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Fubon Neo is distributed by Fubon as an official Linux wheel, not through
# PyPI. Pin the reviewed version so Railway builds are reproducible.
ARG FUBON_SDK_URL=https://www.fbs.com.tw/TradeAPI_SDK/fubon_binary/fubon_neo-2.2.9-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.zip
ARG FUBON_SDK_SHA256=9592e7afb9eba2412ac4a5852df0a138850f6c9803136e7dae503d3606d3b432
RUN python -c "import hashlib,pathlib,urllib.request,zipfile; p=pathlib.Path('/tmp/fubon.zip'); urllib.request.urlretrieve('${FUBON_SDK_URL}',p); actual=hashlib.sha256(p.read_bytes()).hexdigest(); assert actual=='${FUBON_SDK_SHA256}', 'Fubon SDK checksum mismatch'; zipfile.ZipFile(p).extractall('/tmp/fubon-sdk')" \
    && pip install --no-cache-dir /tmp/fubon-sdk/*.whl \
    && rm -rf /tmp/fubon.zip /tmp/fubon-sdk

COPY . .

EXPOSE 8080
CMD ["python", "live_api.py"]
