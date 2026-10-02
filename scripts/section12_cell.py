# Описываем подготовленный архив приложения.
REPO = {
    "ссылка_или_путь": "AquaAgro_Smart_repo.zip (архив проекта с README и кодом)",
    "запуск": "Скопировать .env.example в .env, заполнить свои ключи; docker compose up --build",
    "проверка": "После запуска: http://localhost:8000/health и http://localhost:8000/docs → POST /ask",
    "состояние": "SQLite /app/state/aquaagro.sqlite на Docker-томе aquaagro_state; память отдельно по user_id + session_id, последние 6 обменов",
    "секреты": ".env.example: пустые поля ключей, модель, адрес Langfuse и настройки; .env исключён из Git и Docker-контекста",
}
for k, v in REPO.items():
    print(f"{k:18s}: {v}")
