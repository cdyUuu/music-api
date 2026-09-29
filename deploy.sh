#!/bin/bash

# ============================================================
# 音乐 API 一键部署脚本
#
# 用法:
#   bash deploy.sh                  交互式向导
#   bash deploy.sh --yes            全自动（全用默认值）
#   bash deploy.sh --port 8080      自定义端口
#   bash deploy.sh --init-only      仅初始化项目（跳过系统依赖）
#   bash deploy.sh --help           查看帮助
#
# 发布原则:
#   1. 只做"让服务能跑起来"的必做操作
#   2. 所有服务器配置都询问，影响运行默认开，不影响默认关
#   3. 用户直接回车 = 用默认值
#   4. 幂等：重复跑不报错、不重复加
#   5. 绝不擅自改用户系统
# ============================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ==================== 默认配置 ====================
DEFAULT_PORT="48006"
DEFAULT_DIR="$SCRIPT_DIR"
DEFAULT_USER="www-data"
DEFAULT_WORKERS="1"
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
SOCKET_DIR="/run/music-api"
SOCKET_PATH="$SOCKET_DIR/music-api.sock"

# ==================== 命令行参数 ====================
ARG_PORT=""
ARG_DIR=""
ARG_WORKERS=""
INIT_ONLY=false
AUTO_YES=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)     ARG_PORT="$2"; shift 2 ;;
        --dir)      ARG_DIR="$2"; shift 2 ;;
        --workers)  ARG_WORKERS="$2"; shift 2 ;;
        --init-only) INIT_ONLY=true; shift ;;
        --yes|-y)   AUTO_YES=true; shift ;;
        --help|-h)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "未知参数: $1，使用 --help 查看帮助"
            exit 1 ;;
    esac
done

# ==================== 输出函数 ====================
log()  { echo ""; echo -e "\033[1;34m[$1]\033[0m $2"; }
ok()   { echo -e "\033[1;32m  ✓\033[0m $1"; }
warn() { echo -e "\033[1;33m  ⚠\033[0m $1"; }
info() { echo "    $1"; }
die()  { echo -e "\033[1;31m错误: $1\033[0m"; exit 1; }

ask_yes() {
    local prompt="$1" default="${2:-y}"
    if [ "$AUTO_YES" = true ]; then
        echo "$default" | grep -qi '^y' && return 0 || return 1
    fi
    local hint="[Y/n]"
    echo "$default" | grep -qi '^n' && hint="[y/N]"
    read -p "  $prompt $hint " ans
    ans="${ans:-$default}"
    echo "$ans" | grep -qi '^y' && return 0 || return 1
}

ask_value() {
    local prompt="$1" default="$2"
    if [ "$AUTO_YES" = true ]; then
        echo "$default"
        return
    fi
    read -p "  $prompt [$default]: " ans
    echo "${ans:-$default}"
}

echo ""
echo "============================================"
echo "       音乐 API 部署向导"
echo "============================================"

# ==================== 系统检测 ====================
OS_NAME=$(lsb_release -ds 2>/dev/null || cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'"' -f2 || echo "Unknown")
CPU_CORES=$(nproc 2>/dev/null || echo "?")
MEM_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo "?")
DISK_GB=$(df -h / 2>/dev/null | awk 'NR==2{print $2}' || echo "?")
info "系统: $OS_NAME | CPU: ${CPU_CORES}核 | 内存: ${MEM_MB}MB | 磁盘: $DISK_GB"

# ==================== 基础配置 ====================
echo ""
echo "=== 基础配置 ==="
PORT="${ARG_PORT:-$(ask_value "Web 端口" "$DEFAULT_PORT")}"
INSTALL_DIR="${ARG_DIR:-$DEFAULT_DIR}"
RUN_USER="${ARG_USER:-$(ask_value "运行用户" "$DEFAULT_USER")}"
WORKERS="${ARG_WORKERS:-$(ask_value "Python worker 数" "$DEFAULT_WORKERS")}"
info "端口=$PORT 目录=$INSTALL_DIR 用户=$RUN_USER workers=$WORKERS"

# ==================== 服务配置（默认开） ====================
echo ""
echo "=== 服务配置（推荐开启）==="
DO_INSTALL_DEPS=true
DO_AUTO_REFRESH=true
DO_AUTOSTART=true
DO_FIREWALL=true
DO_LOGROTATE=true

if [ "$INIT_ONLY" = false ]; then
    ask_yes "是否安装系统依赖（Nginx/PHP/Python）？" "y" && DO_INSTALL_DEPS=true || DO_INSTALL_DEPS=false
else
    DO_INSTALL_DEPS=false
    info "--init-only: 跳过系统依赖安装"
fi
ask_yes "是否配置每 6 小时自动刷新凭证？" "y" && DO_AUTO_REFRESH=true || DO_AUTO_REFRESH=false
ask_yes "是否设置服务开机自启？" "y" && DO_AUTOSTART=true || DO_AUTOSTART=false
ask_yes "是否放行防火墙端口 $PORT？" "y" && DO_FIREWALL=true || DO_FIREWALL=false
ask_yes "是否配置日志轮转（超 7 天自动删）？" "y" && DO_LOGROTATE=true || DO_LOGROTATE=false

# ==================== 可选优化（默认关） ====================
echo ""
echo "=== 可选优化（默认关闭）==="
DO_RATE_LIMIT=false
DO_DISABLE_AUTOUPD=false
DO_PHP_WORKER=false
DO_SWAP=false

ask_yes "是否配置 Nginx 限流（防高频请求）？" "n" && DO_RATE_LIMIT=true || DO_RATE_LIMIT=false
ask_yes "是否关闭系统自动更新？" "n" && DO_DISABLE_AUTOUPD=true || DO_DISABLE_AUTOUPD=false
ask_yes "是否按 CPU 核数优化 PHP-FPM worker？" "n" && DO_PHP_WORKER=true || DO_PHP_WORKER=false
ask_yes "是否添加 2G Swap？" "n" && DO_SWAP=true || DO_SWAP=false

echo ""
echo "============================================"
echo "开始部署..."
echo "============================================"

# ==================== 第0阶段：系统依赖 ====================
STEP=0
TOTAL=8

if [ "$DO_INSTALL_DEPS" = true ]; then
    STEP=$((STEP+1))
    log "$STEP/$TOTAL" "安装系统依赖"

    if [ "$(id -u)" -ne 0 ]; then
        die "安装系统依赖需要 root 权限，请使用: sudo bash deploy.sh"
    fi

    PKG_MGR=""
    command -v apt-get &>/dev/null && PKG_MGR="apt"
    command -v dnf &>/dev/null && PKG_MGR="dnf"
    command -v yum &>/dev/null && PKG_MGR="yum"

    if [ -z "$PKG_MGR" ]; then
        warn "未识别的包管理器，跳过系统依赖安装"
    else
        NEED_INSTALL=()
        check_pkg() {
            local pkg="$1"
            if [ "$PKG_MGR" = "apt" ]; then
                dpkg -s "$pkg" &>/dev/null || NEED_INSTALL+=("$pkg")
            else
                rpm -q "$pkg" &>/dev/null || NEED_INSTALL+=("$pkg")
            fi
        }

        command -v nginx &>/dev/null || check_pkg nginx
        command -v php &>/dev/null || check_pkg php8.3-fpm
        check_pkg php8.3-curl 2>/dev/null
        check_pkg php8.3-mbstring 2>/dev/null
        check_pkg php8.3-xml 2>/dev/null
        command -v python3 &>/dev/null || check_pkg python3
        check_pkg python3-venv
        check_pkg python3-pip
        command -v curl &>/dev/null || check_pkg curl
        check_pkg unzip

        if [ ${#NEED_INSTALL[@]} -gt 0 ]; then
            info "安装: ${NEED_INSTALL[*]}"
            if [ "$PKG_MGR" = "apt" ]; then
                apt-get update -qq
                DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEED_INSTALL[@]}" \
                    || die "系统依赖安装失败"
            else
                $PKG_MGR install -y "${NEED_INSTALL[@]}" || die "系统依赖安装失败"
            fi
            ok "系统依赖安装完成"
        else
            ok "系统依赖已齐全"
        fi

        systemctl enable nginx php8.3-fpm &>/dev/null || true
        systemctl start php8.3-fpm &>/dev/null || true
        systemctl start nginx &>/dev/null || true
    fi

    if ! id "$RUN_USER" &>/dev/null; then
        useradd -m -s /bin/bash "$RUN_USER" 2>/dev/null && ok "用户 $RUN_USER 已创建"
    fi
fi

# ==================== 第1阶段：同步socket路径 ====================
STEP=$((STEP+1))
log "$STEP/$TOTAL" "同步 Socket 路径配置"

if [ -f "index.php" ]; then
    sed -i "s/define('PY_SOCKET', '[^']*')/define('PY_SOCKET', '$SOCKET_PATH')/" index.php
fi
if [ -f "py/server.py" ]; then
    sed -i "s|\"/run/[^\"]*music-api.sock\"|\"$SOCKET_PATH\"|g" py/server.py
fi
ok "Socket 路径已同步: $SOCKET_PATH"

# ==================== 第2阶段：Nginx站点配置 ====================
if [ "$DO_INSTALL_DEPS" = true ] || [ "$(id -u)" -eq 0 ]; then
    STEP=$((STEP+1))
    log "$STEP/$TOTAL" "配置 Nginx"

    FPM_SOCK=""
    for sock in /run/php/php8.3-fpm.sock /run/php/php8.2-fpm.sock /run/php/php8.1-fpm.sock /run/php/php7.4-fpm.sock; do
        [ -S "$sock" ] && FPM_SOCK="$sock" && break
    done
    [ -z "$FPM_SOCK" ] && FPM_SOCK="/run/php/php8.3-fpm.sock"

    RATE_LIMIT_ZONE=""
    if [ "$DO_RATE_LIMIT" = true ]; then
        RATE_LIMIT_ZONE="limit_req_zone \$binary_remote_addr zone=musicapi:10m rate=10r/s;\n"
    fi

    printf "${RATE_LIMIT_ZONE}server {\n    listen ${PORT};\n    server_name _;\n    root ${INSTALL_DIR};\n    index index.php index.html;\n    client_max_body_size 20m;\n    server_tokens off;\n\n    location ~ /\\. { deny all; }\n    location ~* /(config\\.json|config\\.json\\.example|requirements\\.txt|deploy\\.sh|music-api-py\\.service)$ { deny all; }\n    location ^~ /data/ { deny all; }\n    location ^~ /logs/ { deny all; }\n    location ^~ /venv/ { deny all; }\n    location ^~ /cache/ { deny all; }\n    location ^~ /py/ { deny all; }\n\n    location / {\n        try_files \$uri \$uri/ /index.php?\$query_string;\n    }\n\n    location ~ \\.php$ {\n        include snippets/fastcgi-php.conf;\n        fastcgi_pass unix:${FPM_SOCK};\n        fastcgi_param HTTP_PROXY \"\";\n    }\n\n    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {\n        expires 7d;\n        access_log off;\n    }\n}\n" > /etc/nginx/sites-available/music-api

    ln -sf /etc/nginx/sites-available/music-api /etc/nginx/sites-enabled/music-api
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null

    if nginx -t 2>/dev/null; then
        systemctl reload nginx 2>/dev/null || systemctl restart nginx
        ok "Nginx 站点已启用 (端口 $PORT)"
    else
        warn "Nginx 配置测试失败，请手动检查: nginx -t"
    fi

    if [ "$DO_FIREWALL" = true ]; then
        if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
            ufw allow "${PORT}/tcp" &>/dev/null && ok "防火墙已放行 $PORT"
        elif command -v firewall-cmd &>/dev/null && firewall-cmd --state 2>/dev/null | grep -q running; then
            firewall-cmd --permanent --add-port="${PORT}/tcp" &>/dev/null
            firewall-cmd --reload &>/dev/null && ok "防火墙已放行 $PORT"
        fi
    fi
fi

# ==================== 第3阶段：config.json ====================
STEP=$((STEP+1))
log "$STEP/$TOTAL" "配置文件"
if [ -f "config.json" ]; then
    ok "config.json 已存在，保持不变"
elif [ -f "config.json.example" ]; then
    cp config.json.example config.json
    ok "已从模板创建 config.json"
    warn "请通过 admin.php 填写账号信息"
else
    warn "config.json 模板不存在"
fi

# ==================== 第4阶段：目录+密钥+咪咕 ====================
STEP=$((STEP+1))
log "$STEP/$TOTAL" "初始化凭证"
mkdir -p data data/cache logs cache

if [ -f "data/admin_key.txt" ]; then
    ok "管理密钥已存在"
else
    if command -v php &>/dev/null && [ -f "gen_key.php" ]; then
        php gen_key.php > /dev/null 2>&1
        [ -f "data/admin_key.txt" ] && ok "管理密钥已生成" || warn "gen_key.php 执行失败"
    fi
fi

COOKIE_COUNT=$(ls data/cookies_*.json 2>/dev/null | wc -l)
if [ "$COOKIE_COUNT" -gt 0 ]; then
    ok "已存在 $COOKIE_COUNT 个咪咕凭证"
else
    MIGU_ACCOUNT=""; MIGU_PASSWORD=""
    if [ -f "config.json" ] && command -v php &>/dev/null; then
        read -r MIGU_ACCOUNT MIGU_PASSWORD < <(php -r '
            $c = json_decode(file_get_contents("config.json"), true);
            echo ($c["migu"]["accounts"][0] ?? "") . " " . ($c["migu"]["password"] ?? "");
        ' 2>/dev/null)
    fi
    if [ -n "$MIGU_ACCOUNT" ] && [ -n "$MIGU_PASSWORD" ] && [ -f "migu_login.php" ]; then
        info "登录咪咕 ($MIGU_ACCOUNT)..."
        php migu_login.php "$MIGU_ACCOUNT" "$MIGU_PASSWORD" > /dev/null 2>&1 && ok "咪咕凭证生成成功" || warn "咪咕登录失败，可在 admin.php 手动刷新"
    else
        warn "未配置咪咕账号，跳过"
    fi
fi

# ==================== 第5阶段：Python venv ====================
STEP=$((STEP+1))
log "$STEP/$TOTAL" "Python 虚拟环境"
if [ ! -d "venv" ] || [ ! -x "venv/bin/python3" ]; then
    if command -v python3 &>/dev/null; then
        info "创建 venv..."
        python3 -m venv venv || die "venv 创建失败"
        venv/bin/pip install --upgrade pip -i "$PIP_MIRROR" --trusted-host pypi.tuna.tsinghua.edu.cn > /dev/null 2>&1
        if [ -f "requirements.txt" ]; then
            info "安装依赖（清华源）..."
            timeout 180 venv/bin/pip install -r requirements.txt -i "$PIP_MIRROR" --trusted-host pypi.tuna.tsinghua.edu.cn \
                && ok "依赖安装完成" || warn "部分依赖安装失败"
        fi
    else
        warn "python3 不可用"
    fi
else
    ok "venv 已存在"
    if venv/bin/python3 -c "import aiohttp, requests, cryptography, fastapi, uvicorn" 2>/dev/null; then
        ok "依赖完整"
    else
        info "补充安装依赖..."
        timeout 180 venv/bin/pip install -r requirements.txt -i "$PIP_MIRROR" --trusted-host pypi.tuna.tsinghua.edu.cn > /dev/null 2>&1
        ok "依赖补充完成"
    fi
fi

# ==================== 第6阶段：Python常驻服务 ====================
STEP=$((STEP+1))
log "$STEP/$TOTAL" "Python 常驻服务"
if [ "$(id -u)" -eq 0 ] && [ -f "music-api-py.service" ]; then
    sed -e "s|/var/www/music-api-v2|$INSTALL_DIR|g" \
        -e "s|User=www-data|User=$RUN_USER|g" \
        -e "s|Group=www-data|Group=$RUN_USER|g" \
        music-api-py.service > /etc/systemd/system/music-api-py.service

    systemctl daemon-reload

    if [ "$DO_AUTOSTART" = true ]; then
        systemctl enable music-api-py &>/dev/null
    fi

    mkdir -p "$SOCKET_DIR" 2>/dev/null
    chown "$RUN_USER:$RUN_USER" "$SOCKET_DIR" 2>/dev/null || true

    if systemctl restart music-api-py 2>/dev/null; then
        sleep 2
        if [ -S "$SOCKET_PATH" ]; then
            ok "Python 常驻服务已启动 ($SOCKET_PATH)"
        else
            warn "服务已启动但 socket 未就绪: journalctl -u music-api-py"
        fi
    else
        warn "Python 服务启动失败: journalctl -u music-api-py"
    fi
else
    warn "非 root 或服务文件不存在，跳过 systemd 安装"
    info "可手动启动: venv/bin/python py/server.py"
fi

# ==================== 第7阶段：系统配置 ====================
STEP=$((STEP+1))
log "$STEP/$TOTAL" "系统配置"

if [ "$DO_AUTO_REFRESH" = true ] && [ "$(id -u)" -eq 0 ]; then
    CRON_CMD="0 */6 * * * curl -s http://127.0.0.1:${PORT}/refresh_cron.php > /dev/null 2>&1"
    (crontab -l 2>/dev/null | grep -v "refresh_cron.php"; echo "$CRON_CMD") | crontab -
    ok "凭证自动刷新已配置（每6小时）"
fi

if [ "$DO_LOGROTATE" = true ] && [ "$(id -u)" -eq 0 ]; then
    cat > /etc/logrotate.d/music-api << LREOF
$INSTALL_DIR/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    create 0644 $RUN_USER $RUN_USER
    su $RUN_USER $RUN_USER
}
LREOF
    ok "日志轮转已配置（保留7天）"
fi

if [ "$DO_PHP_WORKER" = true ] && [ "$(id -u)" -eq 0 ]; then
    PHP_FPM_CONF=$(ls /etc/php/*/fpm/pool.d/www.conf 2>/dev/null | head -1)
    if [ -n "$PHP_FPM_CONF" ]; then
        PM_MAX_CHILDREN=$((CPU_CORES * 4))
        [ "$PM_MAX_CHILDREN" -lt 4 ] && PM_MAX_CHILDREN=4
        sed -i "s/^pm.max_children =.*/pm.max_children = $PM_MAX_CHILDREN/" "$PHP_FPM_CONF"
        sed -i "s/^pm.start_servers =.*/pm.start_servers = $((PM_MAX_CHILDREN/2))/" "$PHP_FPM_CONF"
        sed -i "s/^pm.min_spare_servers =.*/pm.min_spare_servers = $((PM_MAX_CHILDREN/4))/" "$PHP_FPM_CONF"
        sed -i "s/^pm.max_spare_servers =.*/pm.max_spare_servers = $((PM_MAX_CHILDREN*3/4))/" "$PHP_FPM_CONF"
        systemctl reload php8.3-fpm &>/dev/null || true
        ok "PHP-FPM worker 已优化 (max_children=$PM_MAX_CHILDREN)"
    fi
fi

if [ "$DO_SWAP" = true ] && [ "$(id -u)" -eq 0 ]; then
    if [ ! -f /swapfile ]; then
        fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 2>/dev/null
        chmod 600 /swapfile
        mkswap /swapfile > /dev/null 2>&1
        swapon /swapfile
        grep -q "/swapfile" /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
        ok "2G Swap 已添加"
    else
        ok "Swap 已存在，跳过"
    fi
fi

if [ "$DO_DISABLE_AUTOUPD" = true ] && [ "$(id -u)" -eq 0 ]; then
    if [ -f /etc/apt/apt.conf.d/20auto-upgrades ]; then
        sed -i 's/Update-Package-Lists "1"/Update-Package-Lists "0"/' /etc/apt/apt.conf.d/20auto-upgrades
        sed -i 's/Unattended-Upgrade "1"/Unattended-Upgrade "0"/' /etc/apt/apt.conf.d/20auto-upgrades
        ok "系统自动更新已关闭"
    fi
fi

# ==================== 第8阶段：权限+健康检查 ====================
STEP=$((STEP+1))
log "$STEP/$TOTAL" "权限与验证"

if id "$RUN_USER" &>/dev/null; then
    chown -R "$RUN_USER:$RUN_USER" . 2>/dev/null && ok "属主设置为 $RUN_USER" || warn "chown 失败"
else
    chown -R www-data:www-data . 2>/dev/null || true
fi
chmod -R 775 . 2>/dev/null || true
chmod 664 config.json 2>/dev/null || true
for d in venv logs cache data; do
    [ -d "$d" ] && chown -R "$RUN_USER:$RUN_USER" "$d" 2>/dev/null
done
chmod 644 data/admin_key.txt 2>/dev/null || true
chmod 644 data/cookies_* 2>/dev/null || true
ok "权限修正完成"

systemctl reload nginx &>/dev/null || true

info "健康检查..."
sleep 1
HTTP_CODE=$(curl -s -o /tmp/mh.json -w "%{http_code}" --max-time 10 "http://127.0.0.1:${PORT}/index.php?action=health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    ok "健康检查通过 (HTTP 200)"
else
    warn "健康检查 HTTP $HTTP_CODE"
fi

if [ -S "$SOCKET_PATH" ]; then
    PY_HEALTH=$(curl -s --unix-socket "$SOCKET_PATH" --max-time 5 "http://localhost/health" 2>/dev/null)
    echo "$PY_HEALTH" | grep -q '"status":"ok"' && ok "Python 常驻服务正常" || warn "Python 服务响应异常"
else
    warn "Python 常驻服务未运行（将使用 exec 模式）"
fi
rm -f /tmp/mh.json

# ==================== 完成 ====================
echo ""
echo "============================================"
echo "  部署完成"
echo "============================================"
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$SERVER_IP" ] && SERVER_IP="服务器IP"

if [ -f "data/admin_key.txt" ]; then
    KEY=$(cat data/admin_key.txt)
    echo ""
    echo "  配置后台: http://${SERVER_IP}:${PORT}/admin.php?key=${KEY}"
fi
echo "  健康检查: http://${SERVER_IP}:${PORT}/index.php?action=health"
echo ""
if [ "$DO_AUTO_REFRESH" = true ]; then
    echo "  凭证自动刷新: 已配置（每6小时）"
else
    echo "  凭证自动刷新: 未配置，可手动添加:"
    echo "    0 */6 * * * curl -s http://127.0.0.1:${PORT}/refresh_cron.php > /dev/null 2>&1"
fi
echo ""
