FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Method E and the PostgreSQL and ClickHouse drivers. Delete this line for a smaller image.
RUN pip install --no-cache-dir -r requirements-optional.txt

COPY app app
COPY run.py .

RUN mkdir -p /srv/data /srv/uploads
ENV RELINK_DATA_DIR=/srv/data
ENV RELINK_UPLOAD_DIR=/srv/uploads
# Needed so the port mapping can reach the server. Publish on loopback only
# (-p 127.0.0.1:5000:5000). Other hostnames need RELINK_ALLOWED_HOSTS.
ENV RELINK_HOST=0.0.0.0

EXPOSE 5000
VOLUME ["/srv/data", "/srv/uploads"]

CMD ["python", "run.py"]
