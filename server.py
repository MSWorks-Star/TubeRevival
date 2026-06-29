import os
import logging
import html
import re
from io import BytesIO
from urllib.parse import parse_qs, quote

import httpx
from PIL import Image
from fastapi import FastAPI, Request, Response, HTTPException
from dotenv import load_dotenv

# ============================================================
# 1. ФИЛЬТР ДЛЯ МАСКИРОВКИ API-КЛЮЧА В ЛОГАХ
# ============================================================
class APIKeyFilter(logging.Filter):
    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = re.sub(r'key=AIzaSy[A-Za-z0-9_-]+', 'key=[HIDDEN_KEY]', record.msg)
        if hasattr(record, 'args') and record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    arg = re.sub(r'key=AIzaSy[A-Za-z0-9_-]+', 'key=[HIDDEN_KEY]', arg)
                new_args.append(arg)
            record.args = tuple(new_args)
        return True

# ============================================================
# 2. ЗАГРУЗКА НАСТРОЕК И ЛОГИРОВАНИЕ
# ============================================================
load_dotenv()

API_KEY = os.getenv("YOUTUBE_API_KEY")
if not API_KEY:
    raise RuntimeError("❌ Ошибка: не задан YOUTUBE_API_KEY в файле .env")

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("tube_revival.log"),
        logging.StreamHandler()
    ]
)

# ПРИМЕНЯЕМ ФИЛЬТР КО ВСЕМ ОБРАБОТЧИКАМ
for handler in logging.root.handlers:
    handler.addFilter(APIKeyFilter())

logger = logging.getLogger(__name__)

# ============================================================
# 2. ЗАГРУЗКА НАСТРОЕК И ЛОГИРОВАНИЕ
# ============================================================
load_dotenv()

API_KEY = os.getenv("YOUTUBE_API_KEY")
if not API_KEY:
    raise RuntimeError("❌ Ошибка: не задан YOUTUBE_API_KEY в файле .env")

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("tube_revival.log"),
        logging.StreamHandler()
    ]
)

# Подавляем логи httpx, чтобы ключ не выводился
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Фильтр всё равно можно оставить для остальных логов (на всякий случай)
for handler in logging.root.handlers:
    handler.addFilter(APIKeyFilter())

logger = logging.getLogger(__name__)

# ============================================================
# 2. ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ И КЛИЕНТА HTTP
# ============================================================
app = FastAPI(title="TubeRevival")

# Асинхронный HTTP-клиент (переиспользуется для всех запросов)
http_client = httpx.AsyncClient(timeout=15.0)

# Хранилище сессий: IP-адрес -> Auth-токен (ClientLogin)
sessions = {}

# Кэш аватарок: channel_id -> url
AVATAR_URL_CACHE = {}

# Кэш сконвертированных картинок: url -> bytes (JPEG)
IMAGE_CACHE = {}
MAX_IMAGE_CACHE = 100  # максимальное число картинок в кэше

# URL-ы Google API (старые gdata + новые v3)
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
GDATA_RATINGS_URL = "https://gdata.youtube.com/feeds/api/videos/{video_id}/ratings"
GDATA_COMMENTS_URL = "https://gdata.youtube.com/feeds/api/videos/{video_id}/comments"
GDATA_SUBSCRIPTIONS_URL = "https://gdata.youtube.com/feeds/api/users/default/subscriptions"

# ============================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def get_auth_token(request: Request) -> str:
    """Достаёт токен Auth из сессии по IP клиента"""
    client_ip = request.client.host
    token = sessions.get(client_ip)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return token

def clean_old_cache():
    """Если кэш картинок переполнен, удаляем старые записи"""
    if len(IMAGE_CACHE) > MAX_IMAGE_CACHE:
        # удаляем первый (самый старый) элемент
        first_key = next(iter(IMAGE_CACHE))
        del IMAGE_CACHE[first_key]

# ============================================================
# 4. ПРОКСИ ДЛЯ АВАТАРОК (с конвертацией WebP -> JPEG)
# ============================================================
@app.get("/proxy/avatar")
async def proxy_avatar(url: str):
    # Проверка разрешённых доменов
    allowed_domains = ["googleusercontent.com", "ggpht.com", "ytimg.com", "lh3.googleusercontent.com"]
    if not any(domain in url for domain in allowed_domains):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Проверка кэша картинок
    if url in IMAGE_CACHE:
        logger.info(f"[КЭШ] Отдаём аватар из кэша: {url[:50]}...")
        return Response(content=IMAGE_CACHE[url], media_type="image/jpeg")

    try:
        res = await http_client.get(url)
        if res.status_code == 200:
            content_type = res.headers.get('content-type', '').lower()
            if 'webp' in content_type:
                # Конвертируем WebP -> JPEG
                img = Image.open(BytesIO(res.content))
                img = img.convert('RGB')
                output = BytesIO()
                img.save(output, format='JPEG', quality=85)
                image_data = output.getvalue()
                # Сохраняем в кэш
                IMAGE_CACHE[url] = image_data
                clean_old_cache()
                return Response(content=image_data, media_type="image/jpeg")
            else:
                # Если не WebP, возвращаем как есть (но не кэшируем, чтобы не хранить гигантские файлы)
                return Response(content=res.content, media_type=content_type)
        else:
            logger.warning(f"[ПРОКСИ] Не удалось загрузить аватар: статус {res.status_code}")
    except Exception as e:
        logger.error(f"[ПРОКСИ ОШИБКА] {e}")

    # Возвращаем пустую заглушку
    return Response(content=b"", media_type="image/jpeg")

# ============================================================
# 5. ПОЛУЧЕНИЕ URL АВАТАРКИ ПО ID КАНАЛА (с кэшем URL)
# ============================================================
async def get_channel_avatar_url(channel_id: str) -> str:
    if channel_id in AVATAR_URL_CACHE:
        return AVATAR_URL_CACHE[channel_id]

    try:
        params = {
            "part": "snippet",
            "id": channel_id,
            "key": API_KEY
        }
        res = await http_client.get(YOUTUBE_CHANNELS_URL, params=params)
        if res.status_code == 200:
            data = res.json()
            items = data.get("items", [])
            if items:
                avatar_url = items[0]["snippet"]["thumbnails"]["default"]["url"]
                AVATAR_URL_CACHE[channel_id] = avatar_url
                return avatar_url
    except Exception as e:
        logger.error(f"[АВАТАР API ОШИБКА] {e}")

    return "https://yt3.ggpht.com/default"

# ============================================================
# 6. ПОИСК ВИДЕО (возвращает XML для старого YouTube)
# ============================================================
@app.get("/feeds/api/videos")
async def search_videos(request: Request):
    search_query = request.query_params.get("q", "Rick Astley")
    start_index = int(request.query_params.get("start-index", 1))
    max_results = int(request.query_params.get("max-results", 10))

    logger.info(f"[ПОИСК] '{search_query}' (start={start_index}, max={max_results})")
    host = request.headers.get("host", "localhost:8080")

    params = {
        "part": "snippet",
        "q": search_query,
        "type": "video",
        "maxResults": max_results,
        "key": API_KEY
    }

    try:
        res = await http_client.get(YOUTUBE_SEARCH_URL, params=params)
        if res.status_code != 200:
            logger.error(f"[ПОИСК] Ошибка Google API: {res.status_code}")
            return Response(content="<error>Google API error</error>", media_type="text/xml")

        data = res.json()
        items = data.get("items", [])

        # Генерация XML-фида
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml += '<feed xmlns="http://www.w3.org/2005/Atom" '
        xml += 'xmlns:openSearch="http://a9.com/-/spec/opensearchrss/1.0/" '
        xml += 'xmlns:media="http://search.yahoo.com/mrss/" '
        xml += 'xmlns:yt="http://gdata.youtube.com/schemas/2007">\n'
        xml += f'  <openSearch:totalResults>100</openSearch:totalResults>\n'
        xml += f'  <openSearch:startIndex>{start_index}</openSearch:startIndex>\n'
        xml += f'  <openSearch:itemsPerPage>{max_results}</openSearch:itemsPerPage>\n'

        for item in items:
            video_id = item["id"]["videoId"]
            channel_id = item["snippet"]["channelId"]
            title = html.escape(item["snippet"]["title"])
            channel_title = html.escape(item["snippet"]["channelTitle"])

            avatar_url = await get_channel_avatar_url(channel_id)
            proxied_avatar = f"http://{host}/proxy/avatar?url={quote(avatar_url)}"

            xml += '  <entry>\n'
            xml += f'    <id>http://youtube.com/watch?v={video_id}</id>\n'
            xml += f'    <title type="text">{title}</title>\n'
            xml += '    <author>\n'
            xml += f'      <name>{channel_title}</name>\n'
            xml += f'      <link rel="http://gdata.youtube.com/schemas/2007#thumbnail" href="{proxied_avatar}"/>\n'
            xml += '    </author>\n'
            xml += '    <media:group>\n'
            xml += f'      <media:player url="http://youtube.com/watch?v={video_id}"/>\n'
            xml += f'      <media:thumbnail url="http://i.ytimg.com/vi/{video_id}/hqdefault.jpg" height="360" width="480"/>\n'
            xml += '    </media:group>\n'
            xml += '  </entry>\n'

        xml += '</feed>'
        return Response(content=xml, media_type="application/atom+xml; charset=UTF-8")

    except Exception as e:
        logger.error(f"[ПОИСК ИСКЛЮЧЕНИЕ] {e}")
        return Response(content="<error>Internal server error</error>", media_type="text/xml")

# ============================================================
# 7. АВТОРИЗАЦИЯ (РЕАЛЬНЫЙ ClientLogin)
# ============================================================
@app.post("/accounts/ClientLogin")
async def client_login(request: Request):
    body = await request.body()
    data = parse_qs(body.decode("utf-8"))
    email = data.get('Email', [''])[0]
    password = data.get('Passwd', [''])[0]

    logger.info(f"[ВХОД] Попытка для {email}")

    # Отправляем запрос к реальному ClientLogin Google
    auth_payload = {
        'Email': email,
        'Passwd': password,
        'service': 'youtube',
        'accountType': 'GOOGLE'
    }

    try:
        resp = await http_client.post(
            "https://www.google.com/accounts/ClientLogin",
            data=auth_payload
        )
        if resp.status_code == 200:
            # Сохраняем весь ответ (там Auth=...)
            client_ip = request.client.host
            sessions[client_ip] = resp.text
            logger.info(f"[ВХОД] Успешно для {email}, IP: {client_ip}")
            return Response(content=resp.text, media_type="text/plain")
        else:
            # Если Google вернул ошибку (например, BadAuthentication)
            logger.warning(f"[ВХОД] Ошибка: {resp.text}")
            return Response(content=resp.text, media_type="text/plain", status_code=resp.status_code)
    except Exception as e:
        logger.error(f"[ВХОД ИСКЛЮЧЕНИЕ] {e}")
        return Response(content="Error=NetworkError", media_type="text/plain", status_code=500)

# ============================================================
# 8. ЛАЙКИ / ДИЗЛАЙКИ (через gdata)
# ============================================================
@app.post("/feeds/api/videos/{video_id}/ratings")
async def rate_video(video_id: str, request: Request):
    body = await request.body()
    data = parse_qs(body.decode("utf-8"))
    rating = data.get('rating', [''])[0]
    if rating not in ['like', 'dislike']:
        return Response(content="Invalid rating", status_code=400)

    token = get_auth_token(request)
    logger.info(f"[ЛАЙК] {video_id} → {rating}")

    gdata_url = GDATA_RATINGS_URL.format(video_id=video_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/atom+xml"
    }
    body_xml = f'<rating xmlns="http://gdata.youtube.com/schemas/2007" value="{rating}"/>'

    try:
        resp = await http_client.post(gdata_url, data=body_xml, headers=headers)
        if resp.status_code in (200, 201):
            return Response(content="", status_code=200)
        else:
            logger.error(f"[ЛАЙК ОШИБКА] {resp.status_code} {resp.text}")
            return Response(content="", status_code=500)
    except Exception as e:
        logger.error(f"[ЛАЙК ИСКЛЮЧЕНИЕ] {e}")
        return Response(content="", status_code=500)

# ============================================================
# 9. КОММЕНТАРИИ (GET и POST)
# ============================================================
@app.get("/feeds/api/videos/{video_id}/comments")
async def get_comments(video_id: str, request: Request):
    token = get_auth_token(request)
    logger.info(f"[КОММЕНТЫ GET] {video_id}")

    gdata_url = GDATA_COMMENTS_URL.format(video_id=video_id)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = await http_client.get(gdata_url, headers=headers)
        return Response(content=resp.text, media_type="application/atom+xml", status_code=resp.status_code)
    except Exception as e:
        logger.error(f"[ОШИБКА КОММЕНТОВ] {e}")
        return Response(content="<error>Could not fetch comments</error>", media_type="text/xml", status_code=500)

@app.post("/feeds/api/videos/{video_id}/comments")
async def post_comment(video_id: str, request: Request):
    body = await request.body()
    body_str = body.decode("utf-8")
    match = re.search(r"<content>(.*?)</content>", body_str, re.DOTALL)
    if not match:
        return Response(content="Missing content", status_code=400)
    comment_text = html.unescape(match.group(1).strip())

    token = get_auth_token(request)
    logger.info(f"[КОММЕНТ POST] {video_id}: {comment_text[:50]}...")

    gdata_url = GDATA_COMMENTS_URL.format(video_id=video_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/atom+xml"
    }
    comment_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <content>{html.escape(comment_text)}</content>
</entry>'''

    try:
        resp = await http_client.post(gdata_url, data=comment_xml, headers=headers)
        if resp.status_code in (200, 201):
            return Response(content="", status_code=200)
        else:
            logger.error(f"[КОММЕНТ ОШИБКА] {resp.status_code} {resp.text}")
            return Response(content="", status_code=500)
    except Exception as e:
        logger.error(f"[КОММЕНТ ИСКЛЮЧЕНИЕ] {e}")
        return Response(content="", status_code=500)

# ============================================================
# 10. ПОДПИСКИ (GET, POST, DELETE)
# ============================================================
@app.get("/feeds/api/users/default/subscriptions")
async def get_subscriptions(request: Request):
    token = get_auth_token(request)
    logger.info("[ПОДПИСКИ GET]")

    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = await http_client.get(GDATA_SUBSCRIPTIONS_URL, headers=headers)
        return Response(content=resp.text, media_type="application/atom+xml", status_code=resp.status_code)
    except Exception as e:
        logger.error(f"[ОШИБКА ПОДПИСОК] {e}")
        return Response(content="<error>Could not fetch subscriptions</error>", media_type="text/xml", status_code=500)

@app.post("/feeds/api/users/default/subscriptions")
async def subscribe(request: Request):
    body = await request.body()
    body_str = body.decode("utf-8")
    match = re.search(r"<uri>(.*?)</uri>", body_str, re.DOTALL)
    if not match:
        return Response(content="Missing uri", status_code=400)
    channel_uri = match.group(1).strip()

    token = get_auth_token(request)
    logger.info(f"[ПОДПИСКА POST] {channel_uri}")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/atom+xml"
    }
    sub_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <uri>{html.escape(channel_uri)}</uri>
</entry>'''

    try:
        resp = await http_client.post(GDATA_SUBSCRIPTIONS_URL, data=sub_xml, headers=headers)
        if resp.status_code in (200, 201):
            return Response(content="", status_code=200)
        else:
            logger.error(f"[ПОДПИСКА ОШИБКА] {resp.status_code} {resp.text}")
            return Response(content="", status_code=500)
    except Exception as e:
        logger.error(f"[ПОДПИСКА ИСКЛЮЧЕНИЕ] {e}")
        return Response(content="", status_code=500)

@app.delete("/feeds/api/users/default/subscriptions/{subscription_id}")
async def unsubscribe(subscription_id: str, request: Request):
    token = get_auth_token(request)
    logger.info(f"[ОТПИСКА] ID: {subscription_id}")

    gdata_url = f"https://gdata.youtube.com/feeds/api/users/default/subscriptions/{subscription_id}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = await http_client.delete(gdata_url, headers=headers)
        if resp.status_code == 200:
            return Response(content="", status_code=200)
        else:
            logger.error(f"[ОТПИСКА ОШИБКА] {resp.status_code}")
            return Response(content="", status_code=500)
    except Exception as e:
        logger.error(f"[ОТПИСКА ИСКЛЮЧЕНИЕ] {e}")
        return Response(content="", status_code=500)

# ============================================================
# 11. ЗАПУСК
# ============================================================
if __name__ == "__main__":
    import uvicorn
    logger.info(" Запуск TubeRevival сервера на http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)