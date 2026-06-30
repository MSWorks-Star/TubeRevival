import os
import tarfile
import tempfile
import time
import socket

# ============================================================
# 1. ВЫБОР ЯЗЫКА
# ============================================================
def get_lang():
    # Попробуем прочитать из .env (если есть)
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("APP_LANG="):
                    lang = line.strip().split("=")[1].strip().lower()
                    if lang in ("ru", "en"):
                        return lang
    # Если нет, спрашиваем
    print("Select language / Выберите язык:")
    print("1. Русский")
    print("2. English")
    choice = input("Enter 1 or 2 / Введите 1 или 2: ").strip()
    if choice == "1":
        return "ru"
    else:
        return "en"

LANG = get_lang()

# ============================================================
# 2. ТЕКСТЫ
# ============================================================
TEXTS = {
    "ru": {
        "ip_detected": "Ваш текущий IP: {ip}. Использовать его? (Y/N)",
        "ip_manual": "Введите IP вручную (например, 192.168.1.100): ",
        "ip_invalid": "Неверный IP. Попробуйте снова.",
        "building": "Собираем .deb...",
        "done": "Готово! .deb создан: {path}",
        "transfer": "Перенеси его на iPhone и установи через iFile/Filza.",
        "no_ip": "IP не введён. Выход.",
        "no_env": "Нет файла .env для сохранения языка.",
    },
    "en": {
        "ip_detected": "Your current IP: {ip}. Use it? (Y/N)",
        "ip_manual": "Enter IP manually (e.g., 192.168.1.100): ",
        "ip_invalid": "Invalid IP. Try again.",
        "building": "Building .deb...",
        "done": "Done! .deb created: {path}",
        "transfer": "Transfer it to iPhone and install via iFile/Filza.",
        "no_ip": "No IP entered. Exiting.",
        "no_env": "No .env file to save language.",
    }
}

# ============================================================
# 3. ОПРЕДЕЛЕНИЕ IP
# ============================================================
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

# ============================================================
# 4. ОСНОВНАЯ ЛОГИКА
# ============================================================
ip = get_local_ip()
print(TEXTS[LANG]["ip_detected"].format(ip=ip))
choice = input().strip().lower()
if choice == 'y':
    final_ip = ip
else:
    while True:
        final_ip = input(TEXTS[LANG]["ip_manual"]).strip()
        if final_ip:
            break
        print(TEXTS[LANG]["ip_invalid"])

if not final_ip:
    print(TEXTS[LANG]["no_ip"])
    exit()

# ============================================================
# 5. ПОДГОТОВКА СТРУКТУРЫ
# ============================================================
deb_name = "com.tuberevival.tweak_1.0_iphoneos-arm.deb"
tmp = tempfile.mkdtemp()
root = os.path.join(tmp, "root")
os.makedirs(os.path.join(root, "DEBIAN"))
os.makedirs(os.path.join(root, "etc"))

# ============================================================
# 6. ФАЙЛ CONTROL
# ============================================================
control_content = f"""Package: com.tuberevival.tweak
Name: TubeRevival
Version: 1.0
Architecture: iphoneos-arm
Description: Restores full YouTube on iOS 6 with login, likes, comments, subscriptions
Author: TubeRevival
Maintainer: TubeRevival
Depends: firmware (>= 6.0)
Section: Tweaks
"""
with open(os.path.join(root, "DEBIAN", "control"), "w", encoding="utf-8") as f:
    f.write(control_content)

# ============================================================
# 7. ФАЙЛ HOSTS
# ============================================================
hosts_content = "127.0.0.1\tlocalhost\n"
hosts_content += "::1\t\tlocalhost\n\n"
hosts_content += "# TubeRevival: перенаправление на твой ПК\n"
hosts_content += f"{final_ip}\twww.youtube.com\n"
hosts_content += f"{final_ip}\tyoutube.com\n"
hosts_content += f"{final_ip}\tm.youtube.com\n"
hosts_content += f"{final_ip}\tytimg.com\n"
hosts_content += f"{final_ip}\ts.ytimg.com\n"
hosts_content += f"{final_ip}\ti.ytimg.com\n"
hosts_content += f"{final_ip}\twww.googleapis.com\n"
hosts_content += f"{final_ip}\tgoogleapis.com\n"
hosts_content += f"{final_ip}\tggpht.com\n"
hosts_content += f"{final_ip}\tgdata.youtube.com\n"

with open(os.path.join(root, "etc", "hosts"), "w", encoding="utf-8") as f:
    f.write(hosts_content)

# ============================================================
# 8. СБОРКА .deb
# ============================================================
print(TEXTS[LANG]["building"])

data_tar = os.path.join(tmp, "data.tar")
with tarfile.open(data_tar, "w:gz") as tar:
    for item in os.listdir(root):
        if item == "DEBIAN":
            continue
        full = os.path.join(root, item)
        tar.add(full, arcname=item)

control_tar = os.path.join(tmp, "control.tar")
with tarfile.open(control_tar, "w:gz") as tar:
    tar.add(os.path.join(root, "DEBIAN", "control"), arcname="control")

binary_path = os.path.join(tmp, "debian-binary")
with open(binary_path, "w") as f:
    f.write("2.0\n")

deb_path = os.path.join(os.getcwd(), deb_name)
with open(deb_path, "wb") as deb:
    deb.write(b"!<arch>\n")

    def write_ar_header(name, size):
        name = name.ljust(16)[:16].encode()
        mtime = str(int(time.time())).ljust(12).encode()
        uid = b"0".ljust(6)
        gid = b"0".ljust(6)
        mode = b"100644".ljust(8)
        size_str = str(size).ljust(10).encode()
        magic = b"`\n"
        header = name + mtime + uid + gid + mode + size_str + magic
        deb.write(header)

    with open(binary_path, "rb") as f:
        data = f.read()
    write_ar_header("debian-binary", len(data))
    deb.write(data)

    with open(control_tar, "rb") as f:
        data = f.read()
    write_ar_header("control.tar.gz", len(data))
    deb.write(data)

    with open(data_tar, "rb") as f:
        data = f.read()
    write_ar_header("data.tar.gz", len(data))
    deb.write(data)

print(TEXTS[LANG]["done"].format(path=deb_path))
print(TEXTS[LANG]["transfer"])

# ============================================================
# 9. СОХРАНЯЕМ ЯЗЫК В .env (если его нет)
# ============================================================
if not os.path.exists(".env"):
    with open(".env", "w", encoding="utf-8") as f:
        f.write(f"APP_LANG={LANG}\n")
        f.write("YOUTUBE_API_KEY=ваш_ключ_сюда\n")
        f.write("GOOGLE_CLIENT_ID=ваш_client_id_сюда\n")
        f.write("GOOGLE_CLIENT_SECRET=ваш_client_secret_сюда\n")
else:
    # Проверим, есть ли APP_LANG, если нет – добавим
    with open(".env", "r", encoding="utf-8") as f:
        content = f.read()
    if "APP_LANG" not in content:
        with open(".env", "a", encoding="utf-8") as f:
            f.write(f"\nAPP_LANG={LANG}\n")