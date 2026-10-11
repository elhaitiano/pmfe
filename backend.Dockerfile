FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PMF_DATA_DIR=/data
WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 pmf \
    && useradd --uid 10001 --gid pmf --no-create-home pmf \
    && mkdir /data \
    && chown pmf:pmf /data
COPY app/ /app/
COPY docker/startup.py /opt/pmf-docker/startup.py
USER 10001:10001
EXPOSE 8000
CMD ["python", "/opt/pmf-docker/startup.py"]
