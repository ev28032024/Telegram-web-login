# Telegram Web Login Automation (Telescan)

Инструмент для автоматической авторизации в Telegram Web (web.telegram.org) через браузерный антидетект **AdsPower**. Поддерживает как одиночный вход, так и массовую пакетную обработку профилей.

## Основные возможности

*   **Интеграция с AdsPower**: Автоматический запуск и остановка профилей через локальный API.
*   **Умный Логин**: Ввод номера телефона, перехват кода подтверждения через Telethon (из сессии), ввод 2FA пароля.
*   **Пакетный режим (Batch Mode)**: Обработка сотен профилей по списку из CSV/JSON.
*   **Гибкое управление сессиями**:
    *   **Фарминг**: Одна Telethon-сессия управляет множеством AdsPower профилей.
    *   **1 к 1**: Массовый логин, где каждому AdsPower профилю соответствует свой `.session` файл.
*   **Упрощённая конфигурация**: Минимум данных в конфиге (только порядковые номера профилей).

## Установка

1.  Установите зависимости:
    ```bash
    pip install telethon playwright tqdm
    ```
2.  Установите браузеры для Playwright (если еще не установлены):
    ```bash
    playwright install chromium
    ```

## Использование

Основная команда для работы с AdsPower — `adspower-login`.

### 1. Одиночный режим
Логин в один конкретный профиль AdsPower.

```bash
python telescan.py adspower-login \
    --session ./sessions/account.session \
    --profile-id "ads_user_id" \
    --two-fa "mypassword"
```
*   `--profile-id`: ID пользователя в AdsPower (например, `j8d9s7x`).
*   `--api-id` / `--api-hash`: Можно не указывать, если они есть в `config/api_keys.json`.

### 2. Пакетный режим: Фарминг (1 сессия -> N профилей)
Используется, когда один "мамка"-аккаунт имеет доступ к номерам телефонов для множества профилей, или просто для массовых действий с одного аккаунта.

**Подготовка `config/profiles.csv`:**
```csv
serial_number,two_fa
1,pass123
2,
3,pass456
```
(Достаточно указать только порядковый номер профиля в AdsPower, `user_id` определится автоматически).

**Запуск:**
```bash
python telescan.py adspower-login \
    --session ./sessions/farmer.session \
    --profiles ./config/profiles.csv \
    --delay-between 5
```

### 3. Пакетный режим: Массовый перенос (1 сессия -> 1 профиль)
Используется, когда у вас есть папка с `.session` файлами, и их нужно "рассадить" по браузерным профилям.

**Запуск:**
```bash
python telescan.py adspower-login \
    --session ./sessions_folder/ \
    --profiles ./config/profiles.csv
```
*   Скрипт берёт все `.session` файлы из папки, сортирует их.
*   Сортирует профили из CSV по `serial_number`.
*   Сопоставляет их 1 к 1.

## Аргументы командной строки

| Аргумент | Описание |
| :--- | :--- |
| `--session` | Путь к `.session` файлу (одиночный/фарминг) ИЛИ папке с сессиями (режим 1:1). |
| `--profiles` | Путь к CSV/JSON файлу со списком профилей (включает пакетный режим). |
| `--profile-id` | `user_id` профиля AdsPower (для одиночного запуска без CSV). |
| `--two-fa` | Пароль 2FA (для одиночного режима). |
| `--api-id`, `--api-hash` | Telethon credentials (опционально, если есть `config/api_keys.json`). |
| `--report` | Путь для сохранения CSV отчета (по умолчанию: `./reports/adspower_batch_report.csv`). |
| `--concurrency` | Количество одновременных потоков (пока рекомендуется `1` для стабильности). |
| `--delay-between` | Пауза (сек) между запуском профилей. |

## Формат конфигов

### profiles.csv
Простой формат, заголовки обязательны.
```csv
serial_number,two_fa
101,password
102,
```

### profiles.json
```json
[
  {"serial_number": "101", "two_fa": "password"},
  {"serial_number": "102"}
]
```

## Устранение проблем

*   **"AdsPower API error: Too many request"**: Скрипт сам делает паузы и повторные попытки. Если ошибка частая, увеличьте `--delay-between`.
*   **API ключи**: Чтобы не вводить их каждый раз, создайте файл `config/api_keys.json`:
    ```json
    [{"api_id": 12345, "api_hash": "abcdef...", "label": "default"}]
    ```