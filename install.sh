#!/usr/bin/env bash
# ============================================================
#  NetTwin — умный установщик (Arch Linux, пользователь semen)
#  Что делает:
#    1. Проверяет docker / docker compose / службу docker.service
#    2. Собирает и запускает контейнер (restart: unless-stopped)
#    3. Ждёт ответа http://localhost:8080
#    4. Создаёт ярлык на рабочем столе
# ============================================================
set -euo pipefail

RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; BLUE=$'\e[34m'; BOLD=$'\e[1m'; RESET=$'\e[0m'

ok()   { printf '%s✔%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '%s⚠ %s %s\n' "$YELLOW" "$RESET" "$1"; }
err()  { printf '%s✖%s %s\n' "$RED" "$RESET" "$1" >&2; }
step() { printf '\n%s━━ %s ━━%s\n' "$BLUE" "$1" "$RESET"; }

cd "$(dirname "$(readlink -f "$0")")"
PROJECT_DIR="$(pwd)"

echo "${BOLD}══════════════════════════════════════════════════"
echo "   NetTwin — установка локального сетевого комбайна"
echo "══════════════════════════════════════════════════${RESET}"

# ------------------------------------------------ 1. Docker
step "Шаг 1 из 4: проверяю Docker"

if ! command -v docker >/dev/null 2>&1; then
  err "Docker не установлен."
  echo ""
  echo "  Установите его командой (Arch Linux):"
  echo "     ${BOLD}sudo pacman -S docker docker-compose${RESET}"
  echo ""
  echo "  На других дистрибутивах — см. https://docs.docker.com/engine/install/"
  exit 1
fi
ok "Docker найден: $(docker --version)"

if command -v systemctl >/dev/null 2>&1; then
  if ! systemctl is-active --quiet docker 2>/dev/null; then
    err "Служба docker.service не запущена."
    echo ""
    echo "  Выполните ОДНУ команду (запустит сейчас + включит при загрузке):"
    echo "     ${BOLD}sudo systemctl enable --now docker${RESET}"
    echo ""
    echo "  и запустите установщик снова:"
    echo "     ./install.sh"
    exit 1
  fi
  ok "Служба docker.service работает."
fi

if ! docker info >/dev/null 2>&1; then
  err "Нет доступа к Docker (ошибка обращения к сокету /var/run/docker.sock)."
  echo ""
  echo "  Скорее всего, ваш пользователь не состоит в группе docker."
  echo "  Выполните один раз:"
  echo "     ${BOLD}sudo usermod -aG docker \$USER${RESET}"
  echo ""
  echo "  Затем ВЫЙДИТЕ из системы и войдите снова (или перезагрузите ноутбук),"
  echo "  после чего запустите ./install.sh повторно."
  echo ""
  echo "  Быстрая проверка без перелогина:   docker ps   (не должно быть 'permission denied')"
  exit 1
fi
ok "Доступ к Docker есть."

# ------------------------------------------------ 2. Compose
step "Шаг 2 из 4: проверяю Docker Compose"

COMPOSE=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  err "Docker Compose не найден."
  echo ""
  echo "  Установите (Arch Linux):   ${BOLD}sudo pacman -S docker-compose${RESET}"
  exit 1
fi
ok "Использую: $COMPOSE"

# ------------------------------------------------ 3. Сборка и запуск
step "Шаг 3 из 4: собираю и запускаю контейнер (первый раз — несколько минут)"

mkdir -p data
$COMPOSE up -d --build

ok "Контейнер запущен (restart: unless-stopped — поднимется сам после перезагрузки)."

# ------------------------------------------------ 4. Проверка + ярлык
step "Шаг 4 из 4: проверяю, что сервер отвечает на http://localhost:8080"

FETCH=""
if command -v curl >/dev/null 2>&1; then
  FETCH="curl -fsS"
elif command -v wget >/dev/null 2>&1; then
  FETCH="wget -qO-"
fi

if [ -z "$FETCH" ]; then
  warn "curl/wget не найдены — пропускаю авто-проверку."
  echo "  Откройте в браузере: ${BOLD}http://localhost:8080${RESET}"
else
  ready=0
  printf '  Жду ответ сервера'
  for _ in $(seq 1 40); do
    if $FETCH http://localhost:8080/api/health >/dev/null 2>&1; then ready=1; break; fi
    printf '.'
    sleep 3
  done
  echo ""
  if [ "$ready" = "1" ]; then
    ok "Сервер отвечает: http://localhost:8080"
  else
    err "Сервер не ответил за 2 минуты. Посмотрите логи:"
    echo ""
    echo "     $COMPOSE logs -f nettwin"
    echo ""
    echo "  Частые причины: нет интернета при первой сборке, занят порт 8080."
    exit 1
  fi
fi

# ------------------------------------------------ Ярлык на рабочем столе
step "Создаю ярлык на рабочем столе"

TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"
[ -z "${TARGET_HOME:-}" ] && TARGET_HOME="$HOME"

DESKTOP_DIR="$TARGET_HOME/Desktop"
mkdir -p "$DESKTOP_DIR" "$TARGET_HOME/.local/share/icons"

cp frontend/nettwin.svg "$TARGET_HOME/.local/share/icons/nettwin.svg"

cat > "$DESKTOP_DIR/NetTwin.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=NetTwin
Comment=Локальный сетевой комбайн: тестер кабеля и карта сети
Comment[ru]=Локальный сетевой комбайн: тестер кабеля и карта сети
Exec=xdg-open http://localhost:8080
Icon=$TARGET_HOME/.local/share/icons/nettwin.svg
Terminal=false
Categories=Network;Utility;
StartupWMClass=NetTwin
EOF

chmod +x "$DESKTOP_DIR/NetTwin.desktop"
chown "$TARGET_USER" "$DESKTOP_DIR/NetTwin.desktop" 2>/dev/null || true
chown "$TARGET_USER" "$TARGET_HOME/.local/share/icons/nettwin.svg" 2>/dev/null || true
# Пометить ярлык доверенным (GNOME/KDE могут спросить при первом запуске — это нормально)
gio set "$DESKTOP_DIR/NetTwin.desktop" metadata::trusted true 2>/dev/null || true

ok "Ярлык создан: $DESKTOP_DIR/NetTwin.desktop"

# ------------------------------------------------ Итог
echo ""
echo "${BOLD}══════════════════════════════════════════════════"
echo "   Готово! 🎉"
echo "══════════════════════════════════════════════════${RESET}"
echo ""
echo "   Открыть NetTwin:      ${BOLD}http://localhost:8080${RESET}"
echo "   или двойной щелчок по ярлыку ${BOLD}NetTwin${RESET} на рабочем столе."
echo ""
echo "   Полезные команды (из папки проекта $PROJECT_DIR):"
echo "     посмотреть логи:      $COMPOSE logs -f nettwin"
echo "     перезапустить:        $COMPOSE restart nettwin"
echo "     остановить:           $COMPOSE down"
echo "     обновить приложение:  ./install.sh   (пересоберёт контейнер)"
echo ""
echo "   Вся карта хранится в файле: $PROJECT_DIR/data/nettwin.db"
echo "   Подробная инструкция:       README_RU.md и вкладка «Справка» в приложении."
echo ""
