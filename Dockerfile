FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN SECRET_KEY=build-only-static-key DEBUG=True python manage.py collectstatic --noinput \
    && addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app app \
    && mkdir -p /app/media \
    && chown -R app:app /app

USER 10001:10001

EXPOSE 8000

CMD ["gunicorn", "Edulink.wsgi:application", "--bind", "0.0.0.0:8000"]
