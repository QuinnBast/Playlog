# The playtime server (the agents run on the gaming devices, not here).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY playtime_server/ playtime_server/

# Log lines appear right away in `kubectl logs`
ENV PYTHONUNBUFFERED=1
USER 65534:65534
EXPOSE 8080
CMD ["python", "-m", "playtime_server.app"]
