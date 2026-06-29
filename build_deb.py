import os
import tarfile
import tempfile
import time

# =====================================================
# 1. Ввод IP-адреса компьютера
# =====================================================
ip = input("Введите локальный IP твоего компьютера (например, 192.168.1.100): ").strip()
if not ip:
    print("IP не введён. Выход.")
    exit()

# =====================================================
# 2. Подготовка структуры папок
# =====================================================
deb_name = "com.tuberevival.tweak_1.0_iphoneos-arm.deb"
tmp = tempfile.mkdtemp()
root = os.path.join(tmp, "root")
os.makedirs(os.path.join(root, "DEBIAN"))
os.makedirs(os.path.join(root, "etc"))

# =====================================================
# 3. Создаём файл control
# =====================================================
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

# =====================================================
# 4. Создаём файл hosts с перенаправлением (без f-строки с отступами)
# =====================================================
hosts_content = "127.0.0.1\tlocalhost\n"
hosts_content += "::1\t\tlocalhost\n\n"
hosts_content += "# TubeRevival: перенаправление на твой ПК\n"
hosts_content += f"{ip}\twww.youtube.com\n"
hosts_content += f"{ip}\tyoutube.com\n"
hosts_content += f"{ip}\tm.youtube.com\n"
hosts_content += f"{ip}\tytimg.com\n"
hosts_content += f"{ip}\ts.ytimg.com\n"
hosts_content += f"{ip}\ti.ytimg.com\n"
hosts_content += f"{ip}\twww.googleapis.com\n"
hosts_content += f"{ip}\tgoogleapis.com\n"
hosts_content += f"{ip}\tggpht.com\n"
hosts_content += f"{ip}\tgdata.youtube.com\n"

with open(os.path.join(root, "etc", "hosts"), "w", encoding="utf-8") as f:
    f.write(hosts_content)

# =====================================================
# 5. Собираем .deb вручную (ar + tar)
# =====================================================
print("Собираем .deb...")

# 5.1. data.tar.gz
data_tar = os.path.join(tmp, "data.tar")
with tarfile.open(data_tar, "w:gz") as tar:
    for item in os.listdir(root):
        if item == "DEBIAN":
            continue
        full = os.path.join(root, item)
        tar.add(full, arcname=item)

# 5.2. control.tar.gz
control_tar = os.path.join(tmp, "control.tar")
with tarfile.open(control_tar, "w:gz") as tar:
    tar.add(os.path.join(root, "DEBIAN", "control"), arcname="control")

# 5.3. debian-binary
binary_path = os.path.join(tmp, "debian-binary")
with open(binary_path, "w") as f:
    f.write("2.0\n")

# 5.4. Создаём ar-архив (.deb)
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

    # debian-binary
    with open(binary_path, "rb") as f:
        data = f.read()
    write_ar_header("debian-binary", len(data))
    deb.write(data)

    # control.tar.gz
    with open(control_tar, "rb") as f:
        data = f.read()
    write_ar_header("control.tar.gz", len(data))
    deb.write(data)

    # data.tar.gz
    with open(data_tar, "rb") as f:
        data = f.read()
    write_ar_header("data.tar.gz", len(data))
    deb.write(data)

print(f"Готово! .deb создан: {deb_path}")
print("Перенеси его на iPhone и установи через iFile/Filza.")