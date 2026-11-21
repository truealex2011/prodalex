FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY data data
COPY db db
COPY wsgi.py .
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/readyz')"
CMD ["gunicorn", "-w", "2", "--threads", "4", "--timeout", "240", "-b", "0.0.0.0:8000", "--graceful-timeout", "20", "wsgi:app"]
