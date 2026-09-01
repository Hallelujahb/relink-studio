FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional Method E (splink). Comment out to keep the image smaller if you
# don't need it -- Method E just returns a clear error until it's installed.
COPY requirements-optional.txt .
RUN pip install --no-cache-dir splink==4.0.16

COPY app app
COPY run.py .

RUN mkdir -p /srv/data /srv/uploads
ENV RELINK_DATA_DIR=/srv/data
ENV RELINK_UPLOAD_DIR=/srv/uploads
# Set these at `docker run` time as needed:
#   RELINK_CORS_ORIGINS, RELINK_REQUIRE_AUTH, RELINK_UPLOAD_EXPIRY_DAYS,
#   RELINK_FRONTEND_DIST (mount a built frontend and point this at it to
#   serve API + frontend from one container)

EXPOSE 5000
VOLUME ["/srv/data", "/srv/uploads"]

CMD ["python", "run.py"]
