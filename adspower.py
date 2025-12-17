#!/usr/bin/env python3
"""adspower.py
================

Модуль для работы с AdsPower API и пакетной обработки профилей.

Функциональность:
- Подключение к локальному API AdsPower
- Запуск/остановка профилей браузера
- Пакетная обработка нескольких профилей
- Загрузка конфигурации профилей из CSV/JSON файлов
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADSPOWER_API_TIMEOUT = 5.0
DEFAULT_ADSPOWER_BASE_URL = "http://local.adspower.net:50325"

# Селекторы для веб-авторизации Telegram
LOGIN_BY_PHONE_SELECTORS: Sequence[str] = (
    "button:has-text('Log in by phone Number')",
    "button:has-text('phone number')",
    "button:has-text('Log in by phone number')",
    "text=/Log in by phone/i",
    "button:has-text('Войти по номеру телефона')",
    "text=/Войти по номеру телефона/i",
)

PHONE_INPUT_SELECTORS: Sequence[str] = (
    "#sign-in-phone-number",
    "input[data-testid='phone-number-input']",
    ".input-field-phone .input-field-input[contenteditable='true']",
    "input[name='phone_number']",
    "input[type='tel']",
)

CODE_INPUT_SELECTORS: Sequence[str] = (
    "#sign-in-code",
    "input[autocomplete='one-time-code']",
    "input[name*='code' i]",
    ".input-field-input[contenteditable='true'][inputmode='numeric']",
    "input[inputmode='numeric']",
)

PASSWORD_INPUT_SELECTORS: Sequence[str] = (
    "input[type='password']",
    "#sign-in-password",
    "div[contenteditable='true'][data-password='true']",
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AdsPowerProfile:
    """Параметры запущенного профиля AdsPower."""

    ws_endpoint: str
    http_profile: Optional[str]
    browser_pid: Optional[int]


@dataclass
class AdsPowerProfileConfig:
    """Конфигурация профиля AdsPower для пакетной обработки.
    
    Упрощённая версия: только номер профиля и опциональный пароль 2FA.
    Остальные параметры (телефон, сессия) определяются автоматически или в CLI.
    """

    serial_number: str  # Номер профиля в AdsPower (не user_id!)
    two_fa: Optional[str] = None  # Пароль 2FA (опционально)
    user_id: Optional[str] = None  # Заполняется автоматически через API
    session_path: Optional[str] = None  # Путь к сессии (заполняется из CLI/папки)
    
    def __post_init__(self) -> None:
        """Валидация после инициализации."""
        if not self.serial_number:
            raise ValueError("serial_number обязателен")


@dataclass
class BatchResult:
    """Результат обработки одного профиля."""

    serial_number: str
    user_id: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    duration_s: float = 0.0


@dataclass
class BatchSummary:
    """Суммарный результат пакетной обработки."""

    total: int
    success: int
    failed: int
    results: List[BatchResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AdsPower API helpers
# ---------------------------------------------------------------------------


def check_adspower_status(base_url: str = DEFAULT_ADSPOWER_BASE_URL) -> bool:
    """Проверяет доступность API AdsPower."""
    url = f"{base_url.rstrip('/')}/status"
    try:
        with urlopen(url, timeout=ADSPOWER_API_TIMEOUT) as response:
            if response.status == 200:
                return True
    except Exception:
        pass

    # Fallback - проверяем корневой URL
    try:
        with urlopen(base_url, timeout=ADSPOWER_API_TIMEOUT) as response:
            return True
    except Exception:
        return False


def _call_adspower_api(
    base_url: str, path: str, params: Dict[str, str]
) -> Dict[str, Any]:
    """Вспомогательная функция для запросов к локальному API AdsPower."""

    url = f"{base_url.rstrip('/')}{path}?{urlencode(params)}"
    try:
        with urlopen(url) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"AdsPower API HTTP error: {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"AdsPower API connection error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Некорректный ответ AdsPower API") from exc

    code = payload.get("code")
    if code not in (0, "0"):
        message = payload.get("msg") or payload.get("message") or "неизвестная ошибка"
        raise RuntimeError(f"AdsPower API error: {message}")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError("AdsPower API вернул неожиданный формат данных")
    return data


def _normalize_ws_endpoint(endpoint: str) -> str:
    """Приводит websocket endpoint AdsPower к корректному формату."""

    endpoint = endpoint.strip()
    if not endpoint:
        return endpoint

    parsed = urlparse(endpoint)
    if parsed.scheme:
        return endpoint

    if endpoint.startswith("//"):
        return f"ws:{endpoint}"

    return f"ws://{endpoint}"


def _resolve_cdp_from_http(http_url: str) -> Optional[str]:
    """Извлекает CDP endpoint из ``/json/version``."""

    try:
        with urlopen(f"{http_url.rstrip('/')}/json/version") as response:
            payload = json.load(response)
        if isinstance(payload, dict):
            cdp = payload.get("webSocketDebuggerUrl")
            if isinstance(cdp, str) and cdp.strip():
                return cdp.strip()
    except Exception as exc:
        logging.warning("Не удалось получить CDP endpoint через %s: %s", http_url, exc)

    return None


def _ensure_cdp_endpoint(ws_endpoint: str, http_profile: Optional[str]) -> str:
    """Возвращает endpoint, совместимый с Playwright CDP."""

    parsed = urlparse(ws_endpoint)
    if parsed.scheme.startswith("ws") and parsed.path and parsed.path != "/":
        return ws_endpoint

    if http_profile:
        resolved = _resolve_cdp_from_http(http_profile)
        if resolved:
            return resolved

    if parsed.scheme.startswith("ws") and parsed.netloc:
        derived_http = "https" if parsed.scheme == "wss" else "http"
        resolved = _resolve_cdp_from_http(f"{derived_http}://{parsed.netloc}")
        if resolved:
            return resolved

    if parsed.scheme.startswith("ws"):
        return f"{ws_endpoint.rstrip('/')}/devtools/browser"

    return ws_endpoint


def start_adspower_profile(
    base_url: str, profile_id: str
) -> AdsPowerProfile:
    """Запускает профиль AdsPower и возвращает параметры подключения."""

    data = _call_adspower_api(
        base_url,
        "/api/v1/browser/start",
        {
            "user_id": profile_id,
            "launch_args": json.dumps(["--disable-blink-features=AutomationControlled"]),
            "open_tabs": "1",
        },
    )

    ws_endpoint = data.get("ws", {}).get("selenium") or data.get("ws", {}).get(
        "puppeteer"
    )
    if not ws_endpoint:
        ws_endpoint = data.get("ws", {}).get("browser")

    if not ws_endpoint:
        raise RuntimeError("AdsPower не вернул websocket endpoint для профиля")

    http_profile = data.get("http") or data.get("http_proxy")
    ws_endpoint = _normalize_ws_endpoint(ws_endpoint)
    ws_endpoint = _ensure_cdp_endpoint(ws_endpoint, http_profile)

    return AdsPowerProfile(
        ws_endpoint=ws_endpoint,
        http_profile=http_profile,
        browser_pid=data.get("browser_pid"),
    )


def stop_adspower_profile(base_url: str, profile_id: str) -> None:
    """Останавливает профиль AdsPower."""

    try:
        _call_adspower_api(base_url, "/api/v1/browser/stop", {"user_id": profile_id})
    except Exception as exc:
        logging.warning(
            "Не удалось корректно остановить профиль AdsPower %s: %s", profile_id, exc
        )


def get_profile_by_serial_number(base_url: str, serial_number: str) -> Optional[str]:
    """
    Получает user_id профиля по его serial_number через API AdsPower.
    
    Args:
        base_url: Базовый URL AdsPower API
        serial_number: Номер профиля (serial_number)
    
    Returns:
        user_id профиля или None если не найден
    """
    try:
        data = _call_adspower_api(
            base_url,
            "/api/v1/user/list",
            {"serial_number": serial_number},
        )
        
        # API возвращает список профилей
        profiles_list = data.get("list", [])
        if not profiles_list:
            logging.warning("Профиль с serial_number=%s не найден", serial_number)
            return None
        
        # Берём первый найденный профиль
        profile_data = profiles_list[0]
        user_id = profile_data.get("user_id")
        
        if user_id:
            logging.debug(
                "Найден профиль: serial_number=%s -> user_id=%s", 
                serial_number, user_id
            )
            return user_id
        
        return None
        
    except Exception as exc:
        logging.error(
            "Ошибка получения профиля по serial_number=%s: %s", 
            serial_number, exc
        )
        return None


def resolve_profiles_user_ids(
    base_url: str, 
    profiles: List[AdsPowerProfileConfig]
) -> List[AdsPowerProfileConfig]:
    """
    Заполняет user_id для всех профилей через API AdsPower.
    
    Args:
        base_url: Базовый URL AdsPower API
        profiles: Список профилей с serial_number
    
    Returns:
        Список профилей с заполненными user_id
    """
    resolved = []
    
    for config in profiles:
        if config.user_id:
            # user_id уже задан
            resolved.append(config)
            continue
        
        user_id = get_profile_by_serial_number(base_url, config.serial_number)
        if user_id:
            config.user_id = user_id
            resolved.append(config)
        else:
            logging.warning(
                "Пропущен профиль serial_number=%s: не найден в AdsPower",
                config.serial_number
            )
    
    logging.info(
        "Разрешено %d/%d профилей", len(resolved), len(profiles)
    )
    return resolved


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


def load_profiles_from_csv(path: str | Path) -> List[AdsPowerProfileConfig]:
    """
    Загружает конфигурации профилей из CSV файла.
    
    Упрощённый формат - только 2 колонки:
    - serial_number (обязательно) - номер профиля в AdsPower
    - two_fa (опционально) - пароль 2FA
    
    Пример CSV:
    serial_number,two_fa
    1,
    2,mypassword123
    3,
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл профилей не найден: {path}")

    profiles: List[AdsPowerProfileConfig] = []
    
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        
        for row_num, row in enumerate(reader, start=2):
            try:
                serial_number = (row.get("serial_number") or "").strip()
                
                if not serial_number:
                    logging.warning(
                        "Строка %d пропущена: отсутствует serial_number", row_num
                    )
                    continue
                
                two_fa_val = (row.get("two_fa") or "").strip() or None
                
                config = AdsPowerProfileConfig(
                    serial_number=serial_number,
                    two_fa=two_fa_val,
                )
                profiles.append(config)
                
            except (ValueError, KeyError) as exc:
                logging.warning("Ошибка парсинга строки %d: %s", row_num, exc)
                continue

    logging.info("Загружено %d профилей из %s", len(profiles), path)
    return profiles


def load_profiles_from_json(path: str | Path) -> List[AdsPowerProfileConfig]:
    """
    Загружает конфигурации профилей из JSON файла.
    
    Упрощённый формат:
    [
        {"serial_number": "1"},
        {"serial_number": "2", "two_fa": "mypassword"},
        {"serial_number": "3"}
    ]
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл профилей не найден: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON должен содержать массив профилей")

    profiles: List[AdsPowerProfileConfig] = []
    
    for idx, item in enumerate(data):
        try:
            if not isinstance(item, dict):
                logging.warning("Элемент %d не является объектом, пропущен", idx)
                continue
            
            serial_number = str(item.get("serial_number", "")).strip()
            
            if not serial_number:
                logging.warning(
                    "Элемент %d пропущен: отсутствует serial_number", idx
                )
                continue

            config = AdsPowerProfileConfig(
                serial_number=serial_number,
                two_fa=item.get("two_fa"),
            )
            profiles.append(config)
            
        except (ValueError, KeyError) as exc:
            logging.warning("Ошибка парсинга элемента %d: %s", idx, exc)
            continue

    logging.info("Загружено %d профилей из %s", len(profiles), path)
    return profiles


def load_profiles(path: str | Path) -> List[AdsPowerProfileConfig]:
    """
    Загружает профили из файла, автоматически определяя формат по расширению.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    
    if suffix == ".json":
        return load_profiles_from_json(path)
    elif suffix in (".csv", ".txt"):
        return load_profiles_from_csv(path)
    else:
        # Пробуем CSV по умолчанию
        logging.warning(
            "Неизвестное расширение '%s', пробуем загрузить как CSV", suffix
        )
        return load_profiles_from_csv(path)


def save_batch_report(
    results: Sequence[BatchResult], output_path: str | Path
) -> Path:
    """Сохраняет отчёт о пакетной обработке в CSV файл."""
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["serial_number", "user_id", "success", "error", "duration_s"],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))
    
    logging.info("Отчёт сохранён: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


async def process_single_profile(
    config: AdsPowerProfileConfig,
    base_url: str,
    login_handler: Callable,
    api_id: int,
    api_hash: str,
    code_timeout: int = 30,
    poll_interval: float = 1.0,
    connect_timeout: int = 20,
) -> BatchResult:
    """
    Обрабатывает один профиль AdsPower.
    """
    started = time.monotonic()
    serial_number = config.serial_number
    user_id = config.user_id
    
    logging.info(
        "[%s] Начинаем обработку профиля (user_id=%s)", serial_number, user_id
    )
    
    if not user_id:
        return BatchResult(
            serial_number=serial_number,
            user_id=None,
            success=False,
            error="user_id не определён для профиля",
            duration_s=round(time.monotonic() - started, 2),
        )
        
    if not config.session_path:
        return BatchResult(
            serial_number=serial_number,
            user_id=user_id,
            success=False,
            error="Не назначен session_path для профиля",
            duration_s=round(time.monotonic() - started, 2),
        )

    session_path = Path(config.session_path)
    if not session_path.exists():
        return BatchResult(
            serial_number=serial_number,
            user_id=user_id,
            success=False,
            error=f"Файл сессии не найден: {session_path}",
            duration_s=round(time.monotonic() - started, 2),
        )
    
    try:
        # Запускаем профиль AdsPower
        profile = start_adspower_profile(base_url, user_id)
        logging.info("[%s] Профиль AdsPower запущен", serial_number)
        
        try:
            # Вызываем handler для логина
            # ВАЖНО: login_handler должен принимать session_path, так как мы его передаём
            result_code = await login_handler(
                profile=profile,
                session_path=session_path, # Передаём путь к сессии
                two_fa=config.two_fa,
                code_timeout=code_timeout,
                poll_interval=poll_interval,
            )
            
            success = result_code == 0
            error = None if success else f"Login handler returned {result_code}"
            
        finally:
            stop_adspower_profile(base_url, user_id)
            logging.info("[%s] Профиль AdsPower остановлен", serial_number)
        
        duration = time.monotonic() - started
        return BatchResult(
            serial_number=serial_number,
            user_id=user_id,
            success=success,
            error=error,
            duration_s=round(duration, 2),
        )
        
    except Exception as exc:
        duration = time.monotonic() - started
        error_msg = str(exc)
        logging.error("[%s] Ошибка обработки профиля: %s", serial_number, error_msg)
        
        return BatchResult(
            serial_number=serial_number,
            user_id=user_id,
            success=False,
            error=error_msg,
            duration_s=round(duration, 2),
        )


async def process_profiles_batch(
    profiles: Sequence[AdsPowerProfileConfig],
    base_url: str,
    login_handler: Callable,
    api_id: int,
    api_hash: str,
    concurrency: int = 1,
    delay_between: float = 5.0,
    code_timeout: int = 30,
    poll_interval: float = 1.0,
) -> BatchSummary:
    """
    Пакетная обработка нескольких профилей AdsPower.
    """
    if not profiles:
        logging.warning("Список профилей пуст")
        return BatchSummary(total=0, success=0, failed=0, results=[])
    
    # Разрешаем user_id для всех профилей
    resolved_profiles = resolve_profiles_user_ids(base_url, list(profiles))
    
    if not resolved_profiles:
        logging.error("Не удалось разрешить ни одного профиля")
        return BatchSummary(total=len(profiles), success=0, failed=len(profiles), results=[])
    
    concurrency = max(1, min(5, concurrency))
    results: List[BatchResult] = []
    
    # Сортируем по serial_number
    sorted_profiles = sorted(resolved_profiles, key=lambda p: int(p.serial_number) if p.serial_number.isdigit() else p.serial_number)
    
    logging.info(
        "Запуск пакетной обработки: %d профилей, concurrency=%d, delay=%.1fs",
        len(sorted_profiles), concurrency, delay_between
    )
    
    # Последовательная обработка (concurrency=1 для надёжности)
    for idx, config in enumerate(sorted_profiles):
        if idx > 0 and delay_between > 0:
            logging.info("Пауза %.1f сек перед следующим профилем...", delay_between)
            await asyncio.sleep(delay_between)
        
        result = await process_single_profile(
            config=config,
            base_url=base_url,
            login_handler=login_handler,
            api_id=api_id,
            api_hash=api_hash,
            code_timeout=code_timeout,
            poll_interval=poll_interval,
        )
        results.append(result)
    
    success_count = sum(1 for r in results if r.success)
    failed_count = len(results) - success_count
    
    logging.info(
        "Пакетная обработка завершена: %d/%d успешно",
        success_count, len(results)
    )
    
    return BatchSummary(
        total=len(results),
        success=success_count,
        failed=failed_count,
        results=list(results),
    )


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def normalize_phone(phone: str) -> str:
    """Нормализует номер телефона для ввода."""
    phone = phone.strip()
    if not phone.startswith("+"):
        phone = f"+{phone}"
    return phone


def parse_profiles_string(profiles_str: str) -> List[AdsPowerProfileConfig]:
    """
    Парсит строку профилей в формате: 1,2,3 или 1:password,2,3:pass2
    
    Формат: serial_number[:two_fa],serial_number[:two_fa],...
    """
    profiles: List[AdsPowerProfileConfig] = []
    
    for item in profiles_str.split(","):
        item = item.strip()
        if not item:
            continue
        
        parts = item.split(":")
        serial_number = parts[0].strip()
        two_fa = parts[1].strip() if len(parts) > 1 else None
        
        if not serial_number:
            continue
        
        profiles.append(AdsPowerProfileConfig(
            serial_number=serial_number,
            two_fa=two_fa if two_fa else None,
        ))
    
    return profiles
