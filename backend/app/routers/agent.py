"""Генератор скриптов-агентов для флешки (Ventoy) и приёмник паспортов /api/ingest."""
import logging

from fastapi import APIRouter, Response

from ..config import PORT
from ..models import AgentPayload
from ..services import netinfo, passports

log = logging.getLogger("nettwin.agent")
router = APIRouter()


def _api_base(override: str = "") -> str:
    if override:
        base = override.strip().rstrip("/")
        return base if base.startswith("http") else f"http://{base}"
    ip = netinfo.primary_lan_ip()
    return f"http://{ip or 'localhost'}:{PORT}"


WIN_TEMPLATE = r"""<# :
@echo off
chcp 65001 >nul
title NetTwin Agent - паспорт ПК
rem ============================================================
rem  NetTwin Agent (Windows) - собирает паспорт этого ПК и
rem  отправляет его на сервер NetTwin (или сохраняет JSON-файл).
rem  Запуск: двойной щелчок. Ничего не устанавливает.
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "$self='%~f0'; iex ([IO.File]::ReadAllText($self, [Text.Encoding]::UTF8))"
echo.
pause
exit /b
#>

# ---------------- PowerShell ----------------
$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Api = '{API}'
$Dir = Split-Path -Parent '%~f0'
if ($Api -notmatch '^https?://') { $Api = 'http://' + $Api }

Write-Host ''
Write-Host '  NetTwin Agent — сбор паспорта этого ПК' -ForegroundColor Cyan
Write-Host '  ====================================' -ForegroundColor DarkGray
Write-Host ''

$csprod = Get-CimInstance Win32_ComputerSystemProduct
$cs     = Get-CimInstance Win32_ComputerSystem
$bios   = Get-CimInstance Win32_BIOS
$osq    = Get-CimInstance Win32_OperatingSystem
$cpuq   = Get-CimInstance Win32_Processor | Select-Object -First 1
$ramSum = 0
Get-CimInstance Win32_PhysicalMemory | ForEach-Object { $ramSum += $_.Capacity }

$macs = @()
Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True' |
  Where-Object { $_.MACAddress } |
  ForEach-Object { $macs += @{ mac = $_.MACAddress; name = $_.NetConnectionID; desc = $_.Description } }

$data = [ordered]@{
  hostname = $env:COMPUTERNAME
  model    = ('{0} {1}' -f $csprod.Vendor, $csprod.Name).Trim()
  serial   = $bios.SerialNumber
  cpu      = $cpuq.Name
  ram_gb   = [math]::Round($ramSum / 1GB, 1)
  os       = '{0} (сборка {1})' -f $osq.Caption, $osq.BuildNumber
  username = $cs.UserName
  agent    = 'windows'
  macs     = $macs
}
$json = $data | ConvertTo-Json -Depth 4
Write-Host ('  ПК:      ' + $data.hostname)
Write-Host ('  Модель:  ' + $data.model)
Write-Host ('  CPU:     ' + $data.cpu)
Write-Host ('  RAM:     ' + $data.ram_gb + ' ГБ')
Write-Host ('  Сервер:  ' + $Api)
Write-Host ''

$ok = $false
try {
  $resp = Invoke-RestMethod -Uri ($Api.TrimEnd('/') + '/api/ingest') -Method Post `
     -ContentType 'application/json; charset=utf-8' `
     -Body ([Text.Encoding]::UTF8.GetBytes($json)) -TimeoutSec 6
  $ok = ($null -ne $resp)
} catch { $ok = $false }

if ($ok) {
  Write-Host ('  OK! Паспорт отправлен на сервер ' + $Api) -ForegroundColor Green
  Write-Host '  Он сразу появится на ноутбуке NetTwin, вкладка «Паспорта».' -ForegroundColor DarkGray
} else {
  $f = Join-Path $Dir ('nettwin_' + $env:COMPUTERNAME + '.json')
  [IO.File]::WriteAllText($f, $json, [Text.Encoding]::UTF8)
  Write-Host '  Сервер недоступен. Паспорт сохранён в файл:' -ForegroundColor Yellow
  Write-Host ('  ' + $f) -ForegroundColor Yellow
  Write-Host '  Переправьте этот файл на ноутбук и импортируйте во вкладке «Паспорта».' -ForegroundColor DarkGray
}
Write-Host ''
"""

LINUX_TEMPLATE = """#!/usr/bin/env bash
# ============================================================
#  NetTwin Agent (Linux) — собирает паспорт этого ПК и
#  отправляет его на сервер NetTwin (или сохраняет JSON-файл).
#  Запуск:  bash nettwin_agent.sh
#  Подсказка: запустите от root (sudo), чтобы увидеть серийный номер.
# ============================================================
API='{API}'

if ! command -v python3 >/dev/null 2>&1; then
  echo "Требуется python3 (обычно уже установлен). Установите: sudo pacman -S python3"
  exit 1
fi

exec python3 - "$API" "$0" <<'PYEOF'
import glob, json, os, socket, sys, urllib.request

api, self_path = sys.argv[1], sys.argv[2]
if not api.startswith('http'):
    api = 'http://' + api

def read(path, default=''):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read().strip()
    except Exception:
        return default

print()
print('  NetTwin Agent — сбор паспорта этого ПК')
print('  ====================================')
print()

dmi = '/sys/class/dmi/id'
model = (read(dmi + '/product_name') + ' ' + read(dmi + '/product_version')).strip()
serial = read(dmi + '/product_serial') or read(dmi + '/board_serial') or read(dmi + '/chassis_serial')

cpu = ''
try:
    for line in open('/proc/cpuinfo'):
        if line.startswith('model name'):
            cpu = line.split(':', 1)[1].strip()
            break
except Exception:
    pass
if not cpu:
    import platform
    cpu = platform.processor() or platform.machine()

ram_gb = 0.0
try:
    for line in open('/proc/meminfo'):
        if line.startswith('MemTotal'):
            ram_gb = round(int(line.split()[1]) / 1024 ** 2, 1)
            break
except Exception:
    pass

os_name = ''
for line in read('/etc/os-release').splitlines():
    if line.startswith('PRETTY_NAME='):
        os_name = line.split('=', 1)[1].strip('"')
        break

macs = []
for p in sorted(glob.glob('/sys/class/net/*/address')):
    name = p.split('/')[-2]
    mac = read(p)
    if mac and mac != '00:00:00:00:00:00' and name != 'lo':
        macs.append({'mac': mac, 'name': name,
                     'desc': read(os.path.dirname(p) + '/device/uevent', '')[:60]})

data = {
    'hostname': socket.gethostname(),
    'model': model,
    'serial': serial,
    'cpu': cpu,
    'ram_gb': ram_gb,
    'os': os_name or 'Linux',
    'username': os.environ.get('USER', os.environ.get('LOGNAME', '')),
    'agent': 'linux',
    'macs': macs,
}
body = json.dumps(data, ensure_ascii=False).encode('utf-8')
print('  ПК:      ' + data['hostname'])
print('  Модель:  ' + (model or '—'))
print('  CPU:     ' + cpu)
print('  RAM:     ' + str(ram_gb) + ' ГБ')
print('  Сервер:  ' + api)
print()

ok = False
try:
    req = urllib.request.Request(api.rstrip('/') + '/api/ingest', data=body,
                                 headers={'Content-Type': 'application/json; charset=utf-8'})
    with urllib.request.urlopen(req, timeout=6) as r:
        ok = (r.status == 200)
except Exception:
    ok = False

if ok:
    print('  OK! Паспорт отправлен на сервер ' + api)
    print('  Он сразу появится на ноутбуке NetTwin, вкладка «Паспорта».')
else:
    out = os.path.join(os.path.dirname(os.path.abspath(self_path)),
                       'nettwin_%s.json' % data['hostname'])
    with open(out, 'w', encoding='utf-8') as f:
        f.write(body.decode('utf-8'))
    print('  Сервер недоступен. Паспорт сохранён в файл:')
    print('    ' + out)
    print('  Переправьте файл на ноутбук и импортируйте во вкладке «Паспорта».')
print()
try:
    input('Нажмите Enter для выхода... ')
except EOFError:
    pass
PYEOF
"""


@router.get("/agent/windows")
def agent_windows(api_base: str = ""):
    script = WIN_TEMPLATE.replace("{API}", _api_base(api_base))
    return Response(script, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=nettwin_agent.bat"})


@router.get("/agent/linux")
def agent_linux(api_base: str = ""):
    script = LINUX_TEMPLATE.replace("{API}", _api_base(api_base))
    return Response(script, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=nettwin_agent.sh"})


@router.post("/ingest")
def ingest(payload: AgentPayload):
    return passports.ingest_payload(payload.model_dump())
