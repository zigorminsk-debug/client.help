# AGENTS.md — как продолжить работу над этим репозиторием

Этот документ позволяет любому агенту/разработчику быстро понять устройство проекта
и безопасно вносить изменения. Репозиторий — клиент заявок в техподдержку (Jira
`h.csl.by`) для Windows, распространяемый через GPO с автообновлением из GitHub.

## 1. Что это и как работает (кратко)

- Приложение = один самодостаточный `CSLHelpdesk.exe` (Python + PyInstaller, tkinter GUI).
- Через GPO-логон-скрипт EXE копируется в `%LOCALAPPDATA%\CSL\HelpdeskClient`, а общий
  конфиг (логин/пароль Jira, настройки) — в `C:\ProgramData\CSL\HelpdeskClient\config.json`.
  У каждого пользователя создаётся ярлык на Рабочем столе и в меню Пуск.
- Приложение собирает сведения о ПК (WMI/psutil), AnyDesk/RustDesk ID, спрашивает телефон
  (один раз), позволяет отметить неисправности, приложить скриншот и голос, и отправляет
  заявку в Jira под ЕДИНЫМ логином организации (Basic Auth, Jira Server).
- Автообновление: клиент опрашивает последний GitHub Release и при наличии новой версии
  заменяет свой EXE (файл лежит в профиле пользователя → права админа не нужны).

## 2. Структура

```
src/
  helpdesk_client.py   Главное окно (tkinter): телефон, чекбоксы, скриншот, голос, отправка, обновления
  config.py            Чтение админ-конфига (HKLM/ProgramData/env) и пользовательских данных
  system_info.py       Сбор сведений о ПК (psutil + WMI) и автоопределение AnyDesk/RustDesk
  jira_client.py       REST-клиент Jira (создание задачи + вложения, Basic Auth)
  screenshot.py        Захват скриншота (PIL.ImageGrab)
  audio_recorder.py    Запись голоса с микрофона (sounddevice + WAV)
  updater.py           Самообновление из GitHub Releases (загрузка + замена EXE через .bat)
  _version.py          ГЕНЕРИРУЕТСЯ CI (APP_VERSION/BUILD_DATE/GITHUB_REPO). Локально = "0"
deploy/
  admin-config.json.template  Шаблон админ-конфига (заполняет админ 1 раз)
  Install-HelpdeskClient.ps1  GPO Logon-скрипт: копирует EXE+конфиг, создаёт ярлык
  Uninstall-HelpdeskClient.ps1 Удаление
  build-exe.ps1              Локальная сборка EXE на Windows
tests/
  test_logic.py        Юнит-тесты логики (config, system_info, jira_client, updater) — без GUI
  .github/workflows/build.yml  CI: проверка кода (check) → сборка EXE на windows-latest + Release с порядковым номером
assets/
  icon.png, icon.ico       Иконка приложения (жёлтый треугольник с «!»), используется в EXE и окне
DEPLOYMENT.md           Подробный гайд развёртывания на клиенте (GPO)
README.md               Обзор, сценарии реализации, сборка, безопасность
CHANGELOG.md            История релизов (генерируется CI + ручные заметки)
```

## 3. Ветки и CI

- Рабочая ветка проекта: `arena/ff36115b-client-help` (только в неё коммитим и пушим).
- Любой пуш в эту ветку **автоматически** запускает `.github/workflows/build.yml`.
- Воркфлоу **сначала проверяет код** (job `check`): `compileall` (синтаксис) → `flake8`
  (lint, конфиг `.flake8`) → `pytest` (тесты). **Релиз выходит только после успешного
  прохождения проверки** — job `build` имеет `needs: check`. Если проверка падает —
  сборка/релиз не выполняются.
- Если проверка прошла: ставит Python, собирает EXE (PyInstaller, `--onefile --windowed`,
  `--icon assets/icon.png`), **вычисляет порядковый номер версии** = (число существующих
  релизов) + 1, прописывает его в `src/_version.py`, создаёт GitHub Release `vN` с
  вложением `CSLHelpdesk.exe` и выкладывает артефакт. Не пушит код обратно.
- Для ручного запуска: вкладка Actions → «Build & Release CSL Helpdesk Client» → Run workflow.

### Важно про нумерацию версий
Номер берётся из **количества существующих релизов**, а не из файла/тега в коде.
Поэтому: не удаляйте релизы (иначе нумерация «сдвинется» и клиенты могут не увидеть
обновление как более новое); не создавайте релизы вручную мимо CI (сломаете счётчик).

## 4. Как собрать локально (Windows, с Python)

```powershell
python -m pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name CSLHelpdesk src/helpdesk_client.py
# либо: powershell -File deploy\build-exe.ps1
```
Результат: `dist\CSLHelpdesk.exe`. Локально `APP_VERSION="0"` → автообновление отключено
(это нормально; в CI оно перезаписывается).

## 5. Как тестировать логику (можно на Linux/macOS)

```bash
python -m pip install -r requirements.txt pytest
python tests/test_logic.py
```
Покрыто: разбор конфигов, сбор системной информации, создание заявки и ошибка
аутентификации (через локальный mock-Jira), парсинг версий и опрос релиза updater'ом.

⚠️ **GUI (tkinter) нельзя протестировать на Linux-агенте** (tkinter не установлен, нет
дисплея). Логика вокруг GUI (config/updater/jira/system_info) покрыта тестами. Визуальную
проверку окна делайте на Windows. Не пытайтесь собрать Windows-GUI EXE на Linux — используйте
CI (windows-latest) или машину администратора.

## 6. Как вносить изменения — чек-лист

1. Работайте в ветке `arena/9ba65963-client-help`.
2. Правки в поведении → сначала добавьте/обновите тест в `tests/test_logic.py`, запустите.
3. Если меняете список неисправностей или параметры Jira — они задаются в админ-конфиге
   (`deploy/admin-config.json.template`), НЕ в коде. Менять код не нужно.
4. Если добавляете новый модуль — добавьте его в `--hidden-import` в `build.yml` и
   `build-exe.ps1`, и импортируйте из `helpdesk_client.py` (иначе не попадёт в EXE).
5. Коммит → пуш в ветку → CI сам соберёт и пронумерует релиз.
6. Обновите `CHANGELOG.md` и, при необходимости, `DEPLOYMENT.md`/`README.md`.

## 7. Тонкие места и «грабли»

- **Backslash в `config.py`**: пути Windows (`C:\ProgramData`, `SOFTWARE\CSL\Helpdesk`)
  в обычных (не raw) строках ломают парсинг. В `_read_registry_config` путь — raw-строка
  `r"SOFTWARE\CSL\Helpdesk"` (ОДИН обратный слэш). В docstring'ах — префикс `r"""`.
  При правках не добавляйте двойные `\\` в raw-строки и не убирайте `r` у путей реестра.
- **Общий пароль**: хранится в `config.json`/реестре, читаемом обычным пользователем.
  Используйте сервисный аккаунт Jira только с правом создавать заявки. Не давайте
  пользователям права на запись конфига.
- **Место установки EXE**: должно быть в профиле пользователя (`LocalAppData`), иначе
  автообновление не сможет перезаписать файл без прав админа. Не меняйте логон-скрипт
  так, чтобы он клал EXE в ProgramData, если нужно автообновление.
- **Jira Server vs Cloud**: `h.csl.by` — Jira Server (Dashboard.jspa), Basic Auth по
  логину/паролю работает. Если перейдёте на Cloud — нужен API-токен вместо пароля
  (доработайте `jira_client.JiraClient`, возможно, через `/rest/servicedeskapi/request`).

## 8. Идеи для расширения

- Поддержка Jira Service Management `/rest/servicedeskapi/request` (кастомерские поля).
- MSI-пакет через WiX вместо логон-скрипта (для Software Installation GPO).
- Внутренний «брокер» обновлений/приёма заявок, чтобы не хранить пароль на клиентах.
- ADMX-шаблон для тонкой настройки списка неисправностей прямо в редакторе GPO.
- Шифрование голосового WAV; централизованная инвентаризация парка ПК.
