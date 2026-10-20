FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml requirements-dev.lock ./
RUN pip install --no-cache-dir -r requirements-dev.lock
COPY app ./app
RUN pip install --no-cache-dir --no-deps . && useradd --create-home coderisk
USER coderisk
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
