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
# 1. ЗАГРУЗКА .env И ВЫБОР ЯЗЫКА
# ============================================================
load_dotenv()

# Проверяем наличие обязательных переменных
API_KEY = os.getenv("YOUTUBE_API_KEY")
if not API_KEY:
    raise RuntimeError("❌ YOUTUBE_API_KEY не задан в .env")

# Загружаем опциональные (пока не используются, но сохраняем)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Определяем язык
LANG = os.getenv("APP_LANG", "").lower()
if LANG not in ("ru", "en"):
    print("Select language / Выберите язык:")
    print("1. Русский")
    print("2. English")
    choice = input("Enter 1 or 2 / Введите 1 или 2: ").strip()
    LANG = "ru" if choice == "1" else "en"
    # Сохраняем выбор в .env
    with open(".env", "a", encoding="utf-8") as f:
        f.write(f"\nAPP_LANG={LANG}\n")
    print(f"Language set to / Язык установлен: {LANG}")

# ============================================================
# 2. ТЕКСТЫ СООБЩЕНИЙ
# ============================================================
TEXTS = {
    "ru": {
        "start": "Запуск TubeRevival сервера на http://0.0.0.0:8080",
        "no_key": "Ошибка: не задан YOUTUBE_API_KEY в файле .env",
        "search": "[ПОИСК] '{query}' (start={start}, max={max})",
        "login_attempt": "[ВХОД] Попытка для {email}",
        "login_success": "[ВХОД] Успешно для {email}, IP: {ip}",
        "login_error": "[ВХОД] Ошибка: {error}",
        "like": "[ЛАЙК] {video_id} → {rating}",
        "comment_get": "[КОММЕНТЫ GET] {video_id}",
        "comment_post": "[КОММЕНТ POST] {video_id}: {text}...",
        "subscription_get": "[ПОДПИСКИ GET]",
        "subscription_post": "[ПОДПИСКА POST] {uri}",
        "subscription_delete": "[ОТПИСКА] ID: {id}",
        "proxy_error": "[ПРОКСИ ОШИБКА] {error}",
        "avatar_api_error": "[АВАТАР API ОШИБКА] {error}",
        "search_error": "[ОШИБКА ПОИСКА] {error}",
        "rating_error": "[ОШИБКА РЕЙТИНГА] {error}",
        "comment_error": "[ОШИБКА КОММЕНТОВ] {error}",
        "subscription_error": "[ОШИБКА ПОДПИСОК] {error}",
        "internal_error": "Внутренняя ошибка сервера",
        "not_authenticated": "Не авторизован",
        "invalid_rating": "Некорректный рейтинг",
        "missing_content": "Отсутствует содержимое комментария",
        "missing_uri": "Отсутствует URI канала",
        "forbidden_domain": "Домен запрещён",
    },
    "en": {
        "start": "Starting TubeRevival server at http://0.0.0.0:8080",
        "no_key": "Error: YOUTUBE_API_KEY not set in .env",
        "search": "[SEARCH] '{query}' (start={start}, max={max})",
        "login_attempt": "[LOGIN] Attempt for {email}",
        "login_success": "[LOGIN] Success for {email}, IP: {ip}",
        "login_error": "[LOGIN] Error: {error}",
        "like": "[LIKE] {video_id} → {rating}",
        "comment_get": "[COMMENTS GET] {video_id}",
        "comment_post": "[COMMENT POST] {video_id}: {text}...",
        "subscription_get": "[SUBSCRIPTIONS GET]",
        "subscription_post": "[SUBSCRIPTION POST] {uri}",
        "subscription_delete": "[UNSUBSCRIBE] ID: {id}",
        "proxy_error": "[PROXY ERROR] {error}",
        "avatar_api_error": "[AVATAR API ERROR] {error}",
        "search_error": "[SEARCH ERROR] {error}",
        "rating_error": "[RATING ERROR] {error}",
        "comment_error": "[COMMENTS ERROR] {error}",
        "subscription_error": "[SUBSCRIPTIONS ERROR] {error}",
        "internal_error": "Internal server error",
        "not_authenticated": "Not authenticated",
        "invalid_rating": "Invalid rating",
        "missing_content": "Missing comment content",
        "missing_uri": "Missing channel URI",
        "forbidden_domain": "Domain forbidden",
    }
}

# ============================================================
# 3. ФИЛЬТР ДЛЯ МАСКИРОВКИ API-КЛЮЧА В ЛОГАХ
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
# 4. НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("tube_revival.log"),
        logging.StreamHandler()
    ]
)

# Применяем фильтр ко всем обработчикам
for handler in logging.root.handlers:
    handler.addFilter(APIKeyFilter())

# Отключаем логи httpx (чтобы не светить ключ)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ============================================================
# 5. ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ И КЛИЕНТА HTTP
# ============================================================
app = FastAPI(title="TubeRevival")
http_client = httpx.AsyncClient(timeout=15.0)

# Хранилище сессий: IP-адрес -> Auth-токен
sessions = {}
AVATAR_URL_CACHE = {}
IMAGE_CACHE = {}
MAX_IMAGE_CACHE = 100

# URL-ы Google API
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
GDATA_RATINGS_URL = "https://gdata.youtube.com/feeds/api/videos/{video_id}/ratings"
GDATA_COMMENTS_URL = "https://gdata.youtube.com/feeds/api/videos/{video_id}/comments"
GDATA_SUBSCRIPTIONS_URL = "https://gdata.youtube.com/feeds/api/users/default/subscriptions"

# ============================================================
# 6. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def get_auth_token(request: Request) -> str:
    client_ip = request.client.host
    token = sessions.get(client_ip)
    if not token:
        raise HTTPException(status_code=401, detail=TEXTS[LANG]["not_authenticated"])
    return token

def clean_old_cache():
    if len(IMAGE_CACHE) > MAX_IMAGE_CACHE:
        first_key = next(iter(IMAGE_CACHE))
        del IMAGE_CACHE[first_key]

# ============================================================
# 7. ПРОКСИ АВАТАРОК
# ============================================================
@app.get("/proxy/avatar")
async def proxy_avatar(url: str):
    allowed_domains = ["googleusercontent.com", "ggpht.com", "ytimg.com", "lh3.googleusercontent.com"]
    if not any(domain in url for domain in allowed_domains):
        raise HTTPException(status_code=403, detail=TEXTS[LANG]["forbidden_domain"])

    if url in IMAGE_CACHE:
        return Response(content=IMAGE_CACHE[url], media_type="image/jpeg")

    try:
        res = await http_client.get(url)
        if res.status_code == 200:
            content_type = res.headers.get('content-type', '').lower()
            if 'webp' in content_type:
                img = Image.open(BytesIO(res.content))
                img = img.convert('RGB')
                output = BytesIO()
                img.save(output, format='JPEG', quality=85)
                image_data = output.getvalue()
                IMAGE_CACHE[url] = image_data
                clean_old_cache()
                return Response(content=image_data, media_type="image/jpeg")
            else:
                return Response(content=res.content, media_type=content_type)
        else:
            logger.warning(f"[PROXY] Status {res.status_code}")
    except Exception as e:
        logger.error(TEXTS[LANG]["proxy_error"].format(error=str(e)))
    return Response(content=b"", media_type="image/jpeg")

# ============================================================
# 8. ПОЛУЧЕНИЕ АВАТАРКИ ПО КАНАЛУ
# ============================================================
async def get_channel_avatar_url(channel_id: str) -> str:
    if channel_id in AVATAR_URL_CACHE:
        return AVATAR_URL_CACHE[channel_id]

    try:
        params = {"part": "snippet", "id": channel_id, "key": API_KEY}
        res = await http_client.get(YOUTUBE_CHANNELS_URL, params=params)
        if res.status_code == 200:
            data = res.json()
            items = data.get("items", [])
            if items:
                avatar_url = items[0]["snippet"]["thumbnails"]["default"]["url"]
                AVATAR_URL_CACHE[channel_id] = avatar_url
                return avatar_url
    except Exception as e:
        logger.error(TEXTS[LANG]["avatar_api_error"].format(error=str(e)))
    return "https://yt3.ggpht.com/default"

# ============================================================
# 9. ПОИСК ВИДЕО
# ============================================================
@app.get("/feeds/api/videos")
async def search_videos(request: Request):
    search_query = request.query_params.get("q", "Rick Astley")
    start_index = int(request.query_params.get("start-index", 1))
    max_results = int(request.query_params.get("max-results", 10))

    logger.info(TEXTS[LANG]["search"].format(query=search_query, start=start_index, max=max_results))
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
            logger.error(f"[SEARCH] Google API error: {res.status_code}")
            return Response(content="<error>Google API error</error>", media_type="text/xml")

        data = res.json()
        items = data.get("items", [])

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
        logger.error(TEXTS[LANG]["search_error"].format(error=str(e)))
        return Response(content="<error>Internal server error</error>", media_type="text/xml")

# ============================================================
# 10. АВТОРИЗАЦИЯ (ClientLogin)
# ============================================================
@app.post("/accounts/ClientLogin")
async def client_login(request: Request):
    body = await request.body()
    data = parse_qs(body.decode("utf-8"))
    email = data.get('Email', [''])[0]
    password = data.get('Passwd', [''])[0]

    logger.info(TEXTS[LANG]["login_attempt"].format(email=email))

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
            client_ip = request.client.host
            sessions[client_ip] = resp.text
            logger.info(TEXTS[LANG]["login_success"].format(email=email, ip=client_ip))
            return Response(content=resp.text, media_type="text/plain")
        else:
            logger.warning(TEXTS[LANG]["login_error"].format(error=resp.text))
            return Response(content=resp.text, media_type="text/plain", status_code=resp.status_code)
    except Exception as e:
        logger.error(TEXTS[LANG]["login_error"].format(error=str(e)))
        return Response(content="Error=NetworkError", media_type="text/plain", status_code=500)

# ============================================================
# 11. ЛАЙКИ
# ============================================================
@app.post("/feeds/api/videos/{video_id}/ratings")
async def rate_video(video_id: str, request: Request):
    body = await request.body()
    data = parse_qs(body.decode("utf-8"))
    rating = data.get('rating', [''])[0]
    if rating not in ['like', 'dislike']:
        return Response(content=TEXTS[LANG]["invalid_rating"], status_code=400)

    token = get_auth_token(request)
    logger.info(TEXTS[LANG]["like"].format(video_id=video_id, rating=rating))

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
            logger.error(TEXTS[LANG]["rating_error"].format(error=resp.text))
            return Response(content="", status_code=500)
    except Exception as e:
        logger.error(TEXTS[LANG]["rating_error"].format(error=str(e)))
        return Response(content="", status_code=500)

# ============================================================
# 12. КОММЕНТАРИИ
# ============================================================
@app.get("/feeds/api/videos/{video_id}/comments")
async def get_comments(video_id: str, request: Request):
    token = get_auth_token(request)
    logger.info(TEXTS[LANG]["comment_get"].format(video_id=video_id))

    gdata_url = GDATA_COMMENTS_URL.format(video_id=video_id)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = await http_client.get(gdata_url, headers=headers)
        return Response(content=resp.text, media_type="application/atom+xml", status_code=resp.status_code)
    except Exception as e:
        logger.error(TEXTS[LANG]["comment_error"].format(error=str(e)))
        return Response(content="<error>Could not fetch comments</error>", media_type="text/xml", status_code=500)

@app.post("/feeds/api/videos/{video_id}/comments")
async def post_comment(video_id: str, request: Request):
    body = await request.body()
    body_str = body.decode("utf-8")
    match = re.search(r"<content>(.*?)</content>", body_str, re.DOTALL)
    if not match:
        return Response(content=TEXTS[LANG]["missing_content"], status_code=400)
    comment_text = html.unescape(match.group(1).strip())

    token = get_auth_token(request)
    logger.info(TEXTS[LANG]["comment_post"].format(video_id=video_id, text=comment_text[:50]))

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
            logger.error(TEXTS[LANG]["comment_error"].format(error=resp.text))
            return Response(content="", status_code=500)
    except Exception as e:
        logger.error(TEXTS[LANG]["comment_error"].format(error=str(e)))
        return Response(content="", status_code=500)

# ============================================================
# 13. ПОДПИСКИ
# ============================================================
@app.get("/feeds/api/users/default/subscriptions")
async def get_subscriptions(request: Request):
    token = get_auth_token(request)
    logger.info(TEXTS[LANG]["subscription_get"])

    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = await http_client.get(GDATA_SUBSCRIPTIONS_URL, headers=headers)
        return Response(content=resp.text, media_type="application/atom+xml", status_code=resp.status_code)
    except Exception as e:
        logger.error(TEXTS[LANG]["subscription_error"].format(error=str(e)))
        return Response(content="<error>Could not fetch subscriptions</error>", media_type="text/xml", status_code=500)

@app.post("/feeds/api/users/default/subscriptions")
async def subscribe(request: Request):
    body = await request.body()
    body_str = body.decode("utf-8")
    match = re.search(r"<uri>(.*?)</uri>", body_str, re.DOTALL)
    if not match:
        return Response(content=TEXTS[LANG]["missing_uri"], status_code=400)
    channel_uri = match.group(1).strip()

    token = get_auth_token(request)
    logger.info(TEXTS[LANG]["subscription_post"].format(uri=channel_uri))

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
            logger.error(TEXTS[LANG]["subscription_error"].format(error=resp.text))
            return Response(content="", status_code=500)
    except Exception as e:
        logger.error(TEXTS[LANG]["subscription_error"].format(error=str(e)))
        return Response(content="", status_code=500)

@app.delete("/feeds/api/users/default/subscriptions/{subscription_id}")
async def unsubscribe(subscription_id: str, request: Request):
    token = get_auth_token(request)
    logger.info(TEXTS[LANG]["subscription_delete"].format(id=subscription_id))

    gdata_url = f"https://gdata.youtube.com/feeds/api/users/default/subscriptions/{subscription_id}"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = await http_client.delete(gdata_url, headers=headers)
        if resp.status_code == 200:
            return Response(content="", status_code=200)
        else:
            logger.error(TEXTS[LANG]["subscription_error"].format(error=resp.status_code))
            return Response(content="", status_code=500)
    except Exception as e:
        logger.error(TEXTS[LANG]["subscription_error"].format(error=str(e)))
        return Response(content="", status_code=500)

# ============================================================
# 14. ЗАПУСК
# ============================================================
if __name__ == "__main__":
    import uvicorn
    logger.info(TEXTS[LANG]["start"])
    uvicorn.run(app, host="0.0.0.0", port=8080)