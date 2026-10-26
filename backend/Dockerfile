FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml ./
COPY app ./app
COPY mock_services ./mock_services
COPY verify_api ./verify_api
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install -e .
# Settings come from the environment (compose passes .env); .env itself is never baked into the image.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
