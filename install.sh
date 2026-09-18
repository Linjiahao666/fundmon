#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/Linjiahao666/fundmon.git"
PIP_INDEX="${PIP_INDEX:-https://mirrors.aliyun.com/pypi/simple}"
SERVICE_NAME="fundmon"

if [[ ! -e /dev/tty ]]; then
  echo "请在交互式终端中执行本脚本。"
  exit 1
fi

say() {
  printf '%s\n' "$*" > /dev/tty
}

ask() {
  local prompt="$1"
  local __var="$2"
  local default="${3-}"
  if [[ -n "$default" ]]; then
    printf '%s' "$prompt [$default]: " > /dev/tty
  else
    printf '%s' "$prompt: " > /dev/tty
  fi
  local value=""
  IFS= read -r value < /dev/tty || true
  if [[ -z "$value" ]]; then
    value="$default"
  fi
  printf -v "$__var" '%s' "$value"
}

ask_secret() {
  local prompt="$1"
  local __var="$2"
  printf '%s' "$prompt: " > /dev/tty
  local value=""
  IFS= read -rs value < /dev/tty || true
  printf '\n' > /dev/tty
  printf -v "$__var" '%s' "$value"
}

pause() {
  printf '%s' "${1:-按回车继续}" > /dev/tty
  IFS= read -r _ < /dev/tty || true
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

install_packages() {
  local packages=("$@")
  say "需要安装系统软件：${packages[*]}"
  local answer=""
  ask "是否现在安装" answer "Y"
  if [[ ! "$answer" =~ ^[Yy]$ ]]; then
    echo "缺少系统软件，已中止。"
    exit 1
  fi
  if need_cmd apt-get; then
    as_root apt-get update -y
    as_root apt-get install -y "${packages[@]}"
  elif need_cmd dnf; then
    as_root dnf install -y "${packages[@]}"
  elif need_cmd yum; then
    as_root yum install -y "${packages[@]}"
  else
    echo "无法识别包管理器，请先手动安装：${packages[*]}"
    exit 1
  fi
}

python_has_venv() {
  "$1" -c 'import venv, ensurepip' 2>/dev/null
}

pick_python() {
  local candidate
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if need_cmd "$candidate" && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      if python_has_venv "$candidate"; then
        PYTHON="$candidate"
        return 0
      fi
    fi
  done
  return 1
}

ensure_python() {
  if pick_python; then
    return 0
  fi
  local candidate pkg
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if ! need_cmd "$candidate"; then
      continue
    fi
    if ! "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      continue
    fi
    pkg="$("$candidate" -c 'import sys; print("python%d.%d-venv" % (sys.version_info.major, sys.version_info.minor))')"
    if need_cmd apt-get; then
      install_packages "$pkg"
    elif need_cmd dnf; then
      install_packages python3-pip
    fi
    if python_has_venv "$candidate"; then
      PYTHON="$candidate"
      return 0
    fi
  done
  return 1
}

write_env() {
  local target="$1"
  local app_id="$2"
  local app_secret="$3"
  umask 077
  printf 'FEISHU_APP_ID=%s\nFEISHU_APP_SECRET=%s\n' "$app_id" "$app_secret" >"$target"
  chmod 600 "$target"
}

trim() {
  local text="$1"
  text="${text#"${text%%[![:space:]]*}"}"
  text="${text%"${text##*[![:space:]]}"}"
  printf '%s' "$text"
}

collect_credentials() {
  APP_ID=""
  APP_SECRET=""
  while [[ -z "$APP_ID" ]]; do
    ask "FEISHU_APP_ID" APP_ID
    APP_ID="$(trim "$APP_ID")"
    if [[ -z "$APP_ID" ]]; then
      say "App ID 不能为空。"
    fi
  done
  while [[ -z "$APP_SECRET" ]]; do
    ask_secret "FEISHU_APP_SECRET" APP_SECRET
    APP_SECRET="$(trim "$APP_SECRET")"
    if [[ -z "$APP_SECRET" ]]; then
      say "App Secret 不能为空。"
    fi
  done
  write_env .env "$APP_ID" "$APP_SECRET"
}

install_systemd() {
  local dir="$1"
  local python_bin="$2"
  local unit="/etc/systemd/system/${SERVICE_NAME}.service"
  if [[ "$(id -u)" -ne 0 ]]; then
    say "接下来需要管理员权限，用来安装 systemd 服务。"
  fi
  as_root tee "$unit" >/dev/null <<EOF
[Unit]
Description=Fund intra-day estimate Feishu bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$dir
Environment=PYTHONUNBUFFERED=1
Environment=TZ=Asia/Shanghai
ExecStart=$python_bin -m fundmon
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  as_root systemctl daemon-reload
  as_root systemctl enable "$SERVICE_NAME"
  as_root systemctl restart "$SERVICE_NAME"
}

say ""
say "基金盘中估值飞书提醒 安装向导"
say "本脚本会拉取仓库、安装依赖、写入飞书凭证，并注册为开机自启服务。"
say ""

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "当前只支持 Linux。"
  exit 1
fi

missing=()
need_cmd git || missing+=(git)
need_cmd curl || missing+=(curl)
if ((${#missing[@]})); then
  install_packages "${missing[@]}"
fi
if ! ensure_python; then
  echo "未找到可用的 Python 3.11 或更高版本，或缺少 venv 组件。"
  echo "Debian 或 Ubuntu 可先执行：apt install python3.12-venv 或 apt install python3.14-venv"
  exit 1
fi

if [[ -d /www/wwwroot ]]; then
  DEFAULT_DIR="/www/wwwroot/fundmon"
elif [[ "$(id -u)" -eq 0 ]]; then
  DEFAULT_DIR="/opt/fundmon"
else
  DEFAULT_DIR="$HOME/fundmon"
fi

INSTALL_DIR=""
ask "安装目录" INSTALL_DIR "$DEFAULT_DIR"
if [[ -z "$INSTALL_DIR" ]]; then
  echo "安装目录不能为空。"
  exit 1
fi
mkdir -p "$(dirname "$INSTALL_DIR")"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  say "目录已存在仓库，正在更新代码。"
  git -C "$INSTALL_DIR" fetch --prune
  git -C "$INSTALL_DIR" pull --ff-only
elif [[ -e "$INSTALL_DIR" ]] && [[ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]]; then
  echo "目录非空且不是本仓库：$INSTALL_DIR"
  exit 1
else
  say "正在克隆 $REPO_URL"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
say "正在创建虚拟环境并安装依赖，使用 $($PYTHON --version 2>&1)。"
rm -rf .venv
"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip -q
python -m pip install -e . -i "$PIP_INDEX"

say ""
say "下面配置飞书企业自建应用。请用浏览器打开飞书开放平台完成这些步骤："
say "1. 打开 https://open.feishu.cn/app 并创建企业自建应用"
say "2. 开通机器人能力"
say "3. 权限开通：获取与发送单聊、群组消息；订阅事件 im.message.receive_v1"
say "4. 在凭证与基础信息中复制 App ID 和 App Secret"
say "5. 应用可用范围只添加你自己"
say "事件订阅里的长连接先不要保存，等服务启动后再保存。"
say ""
pause "完成后按回车，开始填写凭证。"

APP_ID=""
APP_SECRET=""
if [[ -f .env ]]; then
  keep=""
  ask "已检测到 .env，是否保留现有凭证" keep "Y"
  if [[ ! "$keep" =~ ^[Yy]$ ]]; then
    collect_credentials
  fi
else
  collect_credentials
fi

if ! grep -q '^FEISHU_APP_ID=.\+' .env || ! grep -q '^FEISHU_APP_SECRET=.\+' .env; then
  echo ".env 中缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET。"
  exit 1
fi

PYTHON_BIN="$INSTALL_DIR/.venv/bin/python"
mkdir -p "$INSTALL_DIR/data"

if [[ -d /run/systemd/system ]]; then
  say "正在安装 systemd 服务 $SERVICE_NAME。"
  install_systemd "$INSTALL_DIR" "$PYTHON_BIN"
  sleep 2
  if ! as_root systemctl is-active --quiet "$SERVICE_NAME"; then
    say "服务未能保持运行，最近日志："
    as_root journalctl -u "$SERVICE_NAME" -n 40 --no-pager || true
    exit 1
  fi
  say "服务已启动。"
else
  say "未检测到 systemd，改用后台进程启动。"
  nohup "$PYTHON_BIN" -m fundmon >>"$INSTALL_DIR/data/fundmon.log" 2>&1 &
  echo $! >"$INSTALL_DIR/data/fundmon.pid"
  say "进程已在后台运行，PID $(cat "$INSTALL_DIR/data/fundmon.pid")"
fi

say ""
say "安装完成。请立刻回到飞书开放平台："
say "应用后台 → 事件与回调 → 选择「使用长连接接收事件」→ 保存"
say "必须在本机服务在线时保存，否则会失败。"
say ""
say "然后用手机飞书搜索该机器人，进入私聊，依次发送："
say "添加 110022"
say "开启"
say "分钟 15"
say ""
if [[ -d /run/systemd/system ]]; then
  say "常用命令："
  say "sudo systemctl status $SERVICE_NAME"
  say "sudo journalctl -u $SERVICE_NAME -f"
  say "sudo systemctl restart $SERVICE_NAME"
else
  say "日志文件：$INSTALL_DIR/data/fundmon.log"
fi
say "安装目录：$INSTALL_DIR"
say ""
