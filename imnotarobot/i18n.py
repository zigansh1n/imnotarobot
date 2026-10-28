"""The app speaks the system language: Russian on a Russian Mac, English everywhere else.

Strings are written in Russian at the call site and looked up here by that exact text; a missing
translation falls back to the Russian original, so a new message never breaks anything.
"""

import os
import subprocess


def system_language() -> str:
    forced = os.environ.get("IMNOTAROBOT_LANG", "")
    if forced:
        return forced
    try:
        from Foundation import NSLocale

        first = str(NSLocale.preferredLanguages()[0])
    except Exception:
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, check=False).stdout
        except OSError:  # not a Mac: the usual LANG variable decides
            out = os.environ.get("LANG", "")
        first = out.replace("(", "").replace('"', "").strip().split(",")[0].strip() or "en"
    return "ru" if first.lower().startswith("ru") else "en"


LANG = system_language()


def tr(phrase: str, /, **values) -> str:
    template = phrase if LANG == "ru" else EN.get(phrase, phrase)
    return template.format(**values) if values else template


EN: dict[str, str] = {
    # app and menus
    "Я не робот": "I'm Not a Robot",
    "О программе «{app}»": "About {app}",
    "Скрыть окно": "Hide Window",
    "Выйти из «{app}»": "Quit {app}",
    "Правка": "Edit", "Отменить": "Undo", "Повторить": "Redo", "Вырезать": "Cut", "Копировать": "Copy",
    "Вставить": "Paste", "Выбрать всё": "Select All", "Окно": "Window", "Закрыть": "Close", "Свернуть": "Minimize",
    # updates
    "Вышла версия {version}. Обновить можно в окне программы.": "Version {version} is out. Update it from the app window.",
    "Скачиваю версию {version}. Программа перезапустится сама.": "Downloading version {version}. The app restarts by itself.",
    "обновлений нет": "no updates",
    "скачанный файл повреждён, попробуйте ещё раз": "the download is damaged, try again",
    "нет прав менять {folder} — скачайте новую версию вручную": "no permission to change {folder}, download the new version by hand",
    "в файле обновления нет приложения": "the update file has no app inside",
    # layers
    "Локальная модель": "Local model",
    "Браузер (галочки: reCAPTCHA, hCaptcha, Turnstile)": "Browser (checkboxes: reCAPTCHA, hCaptcha, Turnstile)",
    "Браузер": "Browser",
    "Люди": "Humans",
    "Люди ({name})": "Humans ({name})",
    "Модель {name}": "Model {name}",
    "нет ключа": "no key",
    "нужен Google Chrome": "needs Google Chrome",
    "текст на картинке, слайдеры · бесплатно, доли секунды": "text on a picture, sliders · free, a fraction of a second",
    "всё, включая reCAPTCHA, hCaptcha, Turnstile · платно, 10–60 секунд": "everything, including reCAPTCHA, hCaptcha, Turnstile · paid, 10–60 seconds",
    "текст, сетки картинок, клики · центы, 5–30 секунд": "text, picture grids, clicks · cents, 5–30 seconds",
    "жмёт галочку, картинки отдаёт моделям выше · бесплатно, 10–60 секунд": "ticks the box, hands pictures to the models above · free, 10–60 seconds",
    # task types
    "Текст на картинке": "Text on a picture", "Сетка картинок": "Picture grid", "Клик по картинке": "Click on a picture",
    "Слайдер": "Slider", "Поворот": "Rotate",
    # efforts
    "как у поставщика": "provider default", "нет": "none", "мало": "low", "средне": "medium", "много": "high",
    # chain notes
    "его ответ на эту картинку уже отвергли": "its answer to this picture was already rejected",
    "пропущен: большинство его ответов на такие капчи отвергли": "skipped: most of its answers to such captchas were rejected",
    "исчерпан дневной лимит {limit}": "daily limit {limit} is used up",
    "исчерпан общий дневной лимит облачных моделей ${limit}": "the shared daily limit of cloud models ${limit} is used up",
    "тот же сервис только что не ответил": "the same service has just failed to answer",
    "не уверен ({confidence})": "unsure ({confidence})",
    "ни один слой не умеет {type}": "no step can solve {type}",
    # answers in the journal
    "клетки {tiles}": "tiles {tiles}", "сдвиг {x}": "shift {x}", "токен получен": "token received",
    "решатель на паузе": "the solver is paused",
    # vision models
    "модель ответила не в нужном виде: {text}": "the model answered in the wrong form: {text}",
    "нет ответа": "no answer",
    "ответ {text} не подходит под условия задачи": "answer {text} does not fit the task",
    "модель не выбрала ни одной клетки": "the model picked no tile",
    "модель не указала точек внутри картинки": "the model gave no point inside the picture",
    "модель не обвела ни одной цели": "the model boxed no target",
    "сервис ответил HTTP {status}: {text}": "the service answered HTTP {status}: {text}",
    "сервис ответил не JSON: {text}": "the service did not answer JSON: {text}",
    # browser
    "не указан websiteKey": "websiteKey is missing",
    "не указан websiteURL": "websiteURL is missing",
    "в сборке нет браузерного модуля": "this build has no browser module",
    "браузер не получил ответ за {seconds} с": "the browser got no answer in {seconds} s",
    "браузер: {error}": "browser: {error}",
    "не прошёл за {rounds} заданий с картинками": "not passed after {rounds} picture tasks",
    "нет моделей для картинок": "no models for pictures",
    "Google считает запросы автоматическими и не показывает задание": "Google treats the requests as automated and shows no task",
    "модель не указала, что и куда тащить": "the model did not say what to drag and where",
    # the client's own browser over CDP
    "нужен cdpEndpoint браузера, где открыта капча": "needs cdpEndpoint of the browser that shows the captcha",
    "cdpEndpoint должен начинаться с http:// или ws://": "cdpEndpoint must start with http:// or ws://",
    "cdpEndpoint должен указывать на этот Мак: 127.0.0.1, ::1 или localhost": "cdpEndpoint must point at this Mac: 127.0.0.1, ::1 or localhost",
    "не подключиться к браузеру по {endpoint}: {error}": "cannot connect to the browser at {endpoint}: {error}",
    "в браузере нет открытых вкладок": "the browser has no open tabs",
    "ни на одной открытой вкладке нет этой капчи": "no open tab shows this captcha",
    "DataDome не принял слайдер за {n} попытки": "DataDome did not accept the slider in {n} attempts",
    "DataDome заблокировал этот IP{ip} без капчи: смените прокси": "DataDome blocked this IP{ip} with no captcha: change the proxy",
    "слайдер DataDome не появился": "the DataDome slider did not appear",
    "капча DataDome ушла, но cookie datadome нет": "the DataDome captcha is gone but there is no datadome cookie",
    "капча DataDome ушла, а cookie datadome не поменялся": "the DataDome captcha went away but the datadome cookie did not change",
    "на картинке DataDome нет кусочка пазла": "the DataDome picture has no puzzle piece",
    "игра Arkose не появилась": "the Arkose game did not appear",
    "варианты Arkose не пошли по кругу": "the Arkose options did not come round",
    "капча Arkose ушла, а токен не появился": "the Arkose captcha went away but no token appeared",
    "модель не выбрала картинку": "the model picked no picture",
    "для слайдера нужен piece — картинка кусочка": "a slider needs piece, the picture of the piece",
    # humans
    "ключ не подходит": "the key does not fit",
    "на балансе нет денег": "the balance is empty",
    "сервис перегружен, повторите позже": "the service is overloaded, try later",
    "работники не смогли решить": "the workers could not solve it",
    "в настройках сервиса запрещён ваш IP": "your IP is not allowed in the service settings",
    "ваш IP заблокирован сервисом": "your IP is banned by the service",
    "картинка слишком большая": "the picture is too big",
    "картинка пустая": "the picture is empty",
    "неподходящий формат картинки": "unsupported picture format",
    "сервис не решает такой вид капчи": "the service does not solve this kind of captcha",
    "сервису не хватило данных о капче": "the service lacks data about the captcha",
    "не указан адрес страницы с капчей": "the captcha page address is missing",
    "ошибка сервиса": "service error",
    "нет ответа от {url} за {seconds} с": "no answer from {url} in {seconds} s",
    # controller
    "порты {first}–{last} заняты другими программами": "ports {first}–{last} are taken by other programs",
    "Порт {port} занят другой программой — работаю на {actual}. Направьте программы сюда.":
        "Port {port} is taken by another program, working on {actual}. Point your programs here.",
    "Сегодня: решено {solved} · {spend}": "Today: solved {solved} · {spend}",
    "модели ${usd}": "models ${usd}",
    "люди {money}": "humans {money}",
    "⚠ нет ключа": "⚠ no key",
    "неизвестный поставщик {provider}; есть: {known}": "unknown provider {provider}; known: {known}",
    "модель с именем {name} уже есть": "a model named {name} already exists",
    "укажите название модели": "enter the model name",
    "сервис {provider} не знаю; есть: {known}": "unknown service {provider}; known: {known}",
    "лимит не может быть отрицательным": "a limit cannot be negative",
    "Люди ({name}): введите ключ ещё раз": "Humans ({name}): enter the key again",
    "Люди ({name}): нет ключа": "Humans ({name}): no key",
    "Люди ({name}): нет основного ключа, капчи берут запасные": "Humans ({name}): no main key, the spares take the captchas",
    " — пора пополнить": ", time to top up",
    "Люди ({name}): ✓ баланс {money}{low}": "Humans ({name}): ✓ balance {money}{low}",
    "Люди ({name}): ⚠ {error}": "Humans ({name}): ⚠ {error}",
    "Люди ({name}): ключ есть": "Humans ({name}): key saved",
    "пустой ключ": "the key is empty",
    "начинаю поиск поставщика": "looking for the provider",
    "ключа {name} нет": "there is no key {name}",
    "сравнение остановлено до первого ответа": "the comparison was stopped before the first answer",
    "модели {model} у ключа {key} нет": "key {key} has no model {model}",
    "неизвестный уровень раздумий": "unknown thinking level",
    "сначала включите модель": "turn the model on first",
    "сравнение уже идёт": "a comparison is already running",
    "нет моделей с картинками для сравнения": "no vision models to compare",
    "На {service} осталось {money} — пора пополнить.": "{money} left on {service}, time to top up.",
    "такого ключа нет": "no such key",
    "слой людей выключен": "the humans step is off",
    "Подпись сохранена.": "Label saved.",
    "Запасной ключ {service} работает, баланс {money}.": "The spare {service} key works, balance {money}.",
    "Запасной ключ удалён.": "Spare key removed.",
    "{apps}: приложение держит запущенным.": "{apps}: the app keeps it running.",
    "{apps}: приложение больше не запускает само.": "{apps}: the app no longer starts it.",
    "За {minutes} мин людям ушло {count} капч — похоже, модели перестали отвечать.":
        "{count} captchas went to human workers in {minutes} min — the models have probably stopped answering.",
    "капча ещё решается — вызовите get_result с этим task_id": "the captcha is still being solved — call get_result with this task_id",
    "Люди: исчерпан дневной лимит {money}. До завтра капчи им не отправляются.":
        "Humans: the daily limit {money} is used up. No captchas go to them until tomorrow.",
    "Облачные модели: исчерпан дневной лимит ${limit}.": "Cloud models: the daily limit ${limit} is used up.",
    "Модель {name} ошибается: {error}": "Model {name} fails: {error}",
    "регион не поддерживается — нужен прокси/VPN у посредника": "the region is not supported — the reseller needs a proxy/VPN",
    "исчерпана квота запросов": "the request quota is used up",
    "{model} не принимает картинки — выключена": "{model} does not take pictures — switched off",
    "Сравнение моделей готово. Лучшая: {best}.": "Model comparison is done. Best: {best}.",
    "Сравнение моделей закончилось.": "Model comparison has finished.",
    "набора {name} нет": "there is no set {name}",
    "уже скачивается": "already downloading",
    "{model} на разных уровнях раздумий": "{model} at different thinking levels",
    # discovery
    "{base} не принял ключ (HTTP {status})": "{base} rejected the key (HTTP {status})",
    "проверяю {name}": "checking {name}",
    "проверяю посредника {host}": "checking the reseller {host}",
    "ни один известный поставщик не принял этот ключ; если это посредник — укажите его адрес":
        "no known provider accepted this key; if it is a reseller, enter its address",
    "DashScope (Китай)": "DashScope (China)",
    # window actions
    "нет доступа": "no access",
    "нужна ссылка": "a link is needed",
    "сначала сравните модели": "compare the models first",
    "Скачиваю модель клеток — ход виден в разделе «Путь капчи».": "Downloading the tile model, the progress is in «Captcha path».",
    "Модель клеток удалена: сетки reCAPTCHA снова решает облачная модель.": "The tile model is removed: the cloud model solves reCAPTCHA grids again.",
    "Открыл заготовку на GitHub: проверьте и нажмите «Submit new issue».": "A draft is open on GitHub: check it and press «Submit new issue».",
    "запись не найдена": "entry not found",
    "это доступно только в приложении": "this works only in the app",
    "посредник": "reseller", "поставщик": "provider",
    "моделей с картинками: {n}": "vision models: {n}",
    "включены {models} — можно решать капчи": "{models} switched on, captchas can be solved",
    "список моделей недоступен — впишите модель вручную": "the model list is unavailable, enter a model by hand",
    "Ключ добавлен: {where} {name}, {hint}.": "Key added: {where} {name}, {hint}.",
    "Список моделей {name} обновлён: {n} с картинками.": "Model list of {name} refreshed: {n} with vision.",
    "Ключ {name} удалён вместе с его моделями.": "Key {name} removed with its models.",
    "{model}: включена.": "{model}: on.",
    "{model}: выключена.": "{model}: off.",
    "впишите название модели": "enter the model name",
    "{model} добавлена и включена.": "{model} added and turned on.",
    "Слои обновлены.": "Steps updated.",
    "Порядок обновлён.": "Order updated.",
    "вставьте ключ": "paste the key",
    "ключ сохранён, но {service} его не принял: {error}": "the key is saved, but {service} rejected it: {error}",
    "Ключ работает, баланс {money}.": "The key works, balance {money}.",
    "Ключ людей удалён, слой людей выключен.": "The humans key is removed, the humans step is off.",
    "Сервис: {service}. Валюта: {currency}.": "Service: {service}. Currency: {currency}.",
    " Ключ подходит.": " The key fits.",
    " Этот ключ к нему не подходит — вставьте ключ этого сервиса.": " This key does not fit it, paste a key of this service.",
    "нужно число": "a number is needed",
    "число не может быть отрицательным": "the number cannot be negative",
    "курс должен быть больше нуля": "the rate must be greater than zero",
    "без лимита": "no limit",
    "{money} в день": "{money} a day",
    "люди — {value}": "humans: {value}",
    "модели — {value}": "models: {value}",
    "курс — {value}": "rate: {value}",
    "Лимиты сохранены: {parts}.": "Limits saved: {parts}.",
    "Баланс {money}.": "Balance {money}.",
    "Решатель на паузе.": "The solver is paused.",
    "Решатель работает.": "The solver is running.",
    "Будет запускаться при входе.": "It will start at login.",
    "Автозапуск выключен.": "Start at login is off.",
    "Сравнение запущено.": "Comparison started.",
    "у этой капчи нет ответа, сообщать нечего": "this captcha has no answer, nothing to report",
    "Записал: этот слой на такой картинке больше не используется. Если решали люди — сервису отправлена жалоба.":
        "Noted: this step is no longer used on this picture. If humans solved it, a complaint went to the service.",
    "Ключ доступа скопирован.": "Access key copied.",
    "Открыл папку расширения в Finder.": "The extension folder is revealed in Finder.",
    "в этой сборке нет папки расширения": "this build has no extension folder",
    "Адрес {address} скопирован.": "Address {address} copied.",
    "Новый ключ доступа создан. Старый больше не работает — обновите его в своих программах.":
        "A new access key is created. The old one no longer works, update it in your programs.",
    "Программы должны присылать ключ доступа.": "Programs must send the access key.",
    "Ключ доступа выключен: решатель примет запрос от любой программы на этом Маке.":
        "The access key is off: the solver accepts requests from any program on this Mac.",
    "программа получила бы ошибку: {error}": "a program would get an error: {error}",
    "ответ верный": "the answer is right", "ответ неверный": "the answer is wrong",
    "Подключение работает: тестовую капчу решил слой «{layer}» за {seconds} с, {verdict}.":
        "The connection works: step «{layer}» solved a test captcha in {seconds} s, {verdict}.",
    "{model}: решено {ok} из {n}, в среднем {seconds} с на капчу.": "{model}: solved {ok} of {n}, {seconds} s per captcha on average.",
    "распознавания": "recognition", "кликов": "clicks",
    "{model}: раздумья для {what} — {level}.": "{model}: thinking for {what}: {level}.",
    "Сравнение уровней раздумий запущено.": "Thinking level comparison started.",
    "Останавливаю: дорешаю начатые капчи и покажу то, что успели.": "Stopping: finishing the started captchas and showing what is done.",
    "Скачиваю набор — прогресс виден в разделе сравнения.": "Downloading the set, the progress is in the comparison section.",
    "Набор удалён.": "Set removed.",
    "{name} теперь первая среди моделей.": "{name} is now the first model.",
    "неизвестное действие {action}": "unknown action {action}",
    # macOS permissions
    "macOS не разрешает приложению работать в фоне — после включения Мака оно не запустится.":
        "macOS does not allow the app to run in the background — it will not start after the Mac is switched on.",
    "Уведомления выключены — приложение не сможет предупредить о неполадках.":
        "Notifications are off — the app cannot warn you about problems.",
    # test sets
    "Настоящие капчи с сайтов и приложений: текст, клики по иконкам и словам, задачи на смысл":
        "Real captchas from sites and apps: text, clicks on icons and words, reasoning tasks",
    "лицензия не указана — только для личной проверки": "no license given, for personal testing only",
    # clients
    "«Я не робот» не запущено — откройте программу из «Программ»": "I'm Not a Robot is not running, open it from Applications",
    "нет ответа за {seconds} с": "no answer in {seconds} s",
    "не получилось: {error}": "failed: {error}",
    "Связка ключей не сохранила ключ: {error}": "the Keychain did not save the key: {error}",
}
