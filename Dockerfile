FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 RIDECAST_HOME=/data TF_CPP_MIN_LOG_LEVEL=2
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install '.[ml]' && useradd --create-home --uid 10001 ridecast && mkdir /data && chown ridecast /data
USER ridecast
EXPOSE 8000
CMD ["ridecast", "serve", "--host", "0.0.0.0", "--port", "8000"]
