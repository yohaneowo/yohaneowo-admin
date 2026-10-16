# Backend image for Coolify: docker/backend/Dockerfile plus the frontend dependency manifest that
# publish.sh used to copy in by hand (the "项目依赖" page reads /home/frontend-manifest/).
# Build context is the repo root; backend.Dockerfile.dockerignore next to this file applies.
FROM python:3.12-slim

ARG DB=mysql

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    DB=${DB}

WORKDIR /home

COPY ./backend/requirements/ /home/requirements/
RUN pip install --no-cache-dir \
      -r /home/requirements/base.txt \
      -r /home/requirements/db/${DB}.txt \
      -r /home/requirements/storage.txt

COPY ./backend/ /home/
COPY frontend/web/package.json frontend/web/pnpm-lock.yaml /home/frontend-manifest/

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /home/logs /home/static/upload /home/alembic/versions \
 && chown -R app:app /home
# Secrets come from Doppler at start when DOPPLER_TOKEN (a service token for the prd config) is set.
COPY --from=dopplerhq/cli:3 /bin/doppler /usr/local/bin/doppler

USER app

EXPOSE 8001

ENTRYPOINT ["/bin/sh", "-c", "if [ -n \"$DOPPLER_TOKEN\" ]; then exec doppler run -- \"$@\"; else exec \"$@\"; fi", "--"]
CMD ["python", "main.py", "run", "--env=prod"]
