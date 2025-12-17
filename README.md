# Telegram-web-login

## Авторизация web.telegram.org через AdsPower

Новый режим `adspower-login` запускает профиль браузера AdsPower, вводит номер телефона из указанного `.session` и автоматически подставляет код авторизации из диалога с официальным ботом Telegram (учтены фильтры, чтобы не брать мусорные сообщения). Если включена 2FA, можно сразу передать пароль.

Пример запуска:

```bash
python telescan.py adspower-login \
  --session ./sessions/example.session \
  --api-id 123456 \
  --api-hash abcd1234abcd1234abcd1234 \
  --profile-id ap_profile_id \
  --two-fa "пароль2FA"
```

Требуются зависимости `telethon`, `tqdm` и `playwright` (последний — для подключения к профилю AdsPower: `pip install playwright && playwright install chromium`).