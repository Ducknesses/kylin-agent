#!/bin/bash
# ============================================================
# MCP Server 配置向导
# ============================================================
# 功能:
#   1. 修改监听地址 + 防火墙放行 + 热重启
#   2. 修改认证 Token + 冷重启 + 连接测试
#   3. 连接测试
#   4. 查看当前配置
#
# 用法: sudo ./kylin-wizard.sh
# 前提: mcp-server 已通过 kylin-install.sh 安装
# ============================================================
# ---- 颜色定义 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ---- 文件路径常量 ----
ENV_FILE="/opt/mcp-server/.env"
SERVICE_FILE="/etc/systemd/system/mcp-server.service"
SERVICE_NAME="mcp-server"

# ---- 工具函数 ----
print_banner() {
    echo ""
    echo -e "${CYAN}==============================================${NC}"
    echo -e "${CYAN}  MCP Server 配置向导${NC}"
    echo -e "${CYAN}  目标平台: 麒麟 V11 + LoongArch${NC}"
    echo -e "${CYAN}==============================================${NC}"
    echo ""
}

print_step() {
    echo -e "${BLUE}[*]${NC} $1"
}

print_ok() {
    echo -e "${GREEN}  ✓${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}  ⚠${NC} $1"
}

print_err() {
    echo -e "${RED}  ✗${NC} $1"
}

# ---- 读取当前配置 ----
read_current_host() {
    if [ -f "$ENV_FILE" ]; then
        grep -oP '^MCP_HOST=\K.*' "$ENV_FILE" 2>/dev/null || echo "127.0.0.1"
    else
        echo "127.0.0.1"
    fi
}

read_current_port() {
    if [ -f "$ENV_FILE" ]; then
        grep -oP '^MCP_PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo "8001"
    else
        echo "8001"
    fi
}

read_current_token() {
    if [ -f "$ENV_FILE" ]; then
        grep -oP '^API_TOKEN=\K.*' "$ENV_FILE" 2>/dev/null || echo ""
    else
        echo ""
    fi
}

# ---- 防火墙操作 ----
configure_firewall() {
    local port="$1"
    local action="$2"  # "add" 或 "remove"

    if [ "$action" = "add" ]; then
        print_step "配置防火墙，${action} 端口 ${port}/tcp ..."
    else
        print_step "清理防火墙旧规则，${action} 端口 ${port}/tcp ..."
    fi

    if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
        # ── firewalld 路径 ──
        if [ "$action" = "add" ]; then
            if firewall-cmd --permanent --query-port="${port}/tcp" &>/dev/null; then
                print_warn "端口 ${port}/tcp 已存在于防火墙规则中，跳过添加"
            else
                firewall-cmd --permanent --add-port="${port}/tcp"
                firewall-cmd --reload
                print_ok "firewalld 已开放 ${port}/tcp（永久规则）"
            fi
        else
            if firewall-cmd --permanent --query-port="${port}/tcp" &>/dev/null 2>&1; then
                firewall-cmd --permanent --remove-port="${port}/tcp"
                firewall-cmd --reload
                print_ok "已从 firewalld 移除 ${port}/tcp"
            else
                print_warn "firewalld 中未找到端口 ${port}/tcp，跳过"
            fi
        fi

    elif command -v iptables &>/dev/null; then
        # ── iptables 路径 ──
        if [ "$action" = "add" ]; then
            if iptables -C INPUT -p tcp --dport "${port}" -j ACCEPT &>/dev/null 2>&1; then
                print_warn "端口 ${port}/tcp 已存在于 iptables 规则中，跳过添加"
            else
                iptables -I INPUT -p tcp --dport "${port}" -j ACCEPT
                print_ok "iptables 已添加 ${port}/tcp ACCEPT 规则"
            fi
        else
            if iptables -C INPUT -p tcp --dport "${port}" -j ACCEPT &>/dev/null 2>&1; then
                iptables -D INPUT -p tcp --dport "${port}" -j ACCEPT
                print_ok "已从 iptables 移除 ${port}/tcp"
            else
                print_warn "iptables 中未找到端口 ${port}/tcp，跳过"
            fi
        fi

        # 持久化 iptables 规则
        if command -v iptables-save &>/dev/null; then
            if [ -d /etc/iptables ]; then
                iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
                print_ok "已保存 iptables 规则至 /etc/iptables/rules.v4"
            elif command -v netfilter-persistent &>/dev/null; then
                netfilter-persistent save 2>/dev/null || true
                print_ok "已通过 netfilter-persistent 保存规则"
            else
                print_warn "无法持久化 iptables 规则：未找到 iptables-save 或 netfilter-persistent"
            fi
        fi

    elif command -v ufw &>/dev/null; then
        # ── ufw 路径 ──
        if [ "$action" = "add" ]; then
            ufw allow "${port}/tcp" 2>/dev/null || true
            print_ok "ufw 已开放 ${port}/tcp"
        else
            ufw delete allow "${port}/tcp" 2>/dev/null || true
            print_ok "已从 ufw 移除 ${port}/tcp"
        fi

    else
        print_warn "未检测到 firewalld / iptables / ufw，请手动管理防火墙规则"
    fi
}

# ---- 修改 .env 文件 ----
update_env_value() {
    local key="$1"
    local value="$2"

    if [ ! -f "$ENV_FILE" ]; then
        # 如果不存在，从 .env.example 复制
        if [ -f /opt/mcp-server/.env.example ]; then
            cp /opt/mcp-server/.env.example "$ENV_FILE"
        else
            touch "$ENV_FILE"
        fi
    fi

    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
    print_ok "已更新 ${key}=${value} 到 ${ENV_FILE}"
}

# ---- 更新 systemd 服务文件中的环境变量 ----
update_service_env() {
    local key="$1"
    local value="$2"

    if grep -q "Environment=${key}=" "$SERVICE_FILE" 2>/dev/null; then
        sed -i "s|Environment=${key}=.*|Environment=${key}=${value}|" "$SERVICE_FILE"
    else
        # 在 Environment=PYTHONPATH 行后插入
        sed -i "/Environment=PYTHONPATH/a Environment=${key}=${value}" "$SERVICE_FILE"
    fi
    systemctl daemon-reload
    print_ok "已更新 systemd 服务中的 ${key}=${value}"
}

# ---- JSON-RPC 热重启 ----
hot_restart_server() {
    local host="$1"
    local port="$2"
    local token="$3"
    local new_host="$4"
    local new_port="$5"

    print_step "正在通过 JSON-RPC 热重启服务器..."
    print_step "  连接地址: ${host}:${port} → 目标地址: ${new_host}:${new_port}"

    # B1: 校验 new_host 不包含 JSON 破坏字符（双引号、反斜杠、换行等）
    if [ -n "$new_host" ]; then
        if [[ "$new_host" == *['\"''\\']* ]]; then
            print_err "HOST 包含非法字符，无法热重启"
            return 1
        fi
    fi

    # 构造参数：只传递需要变更的字段
    local args_json=""
    if [ -n "$new_host" ] && [ -n "$new_port" ]; then
        args_json="\"host\": \"${new_host}\", \"port\": ${new_port}"
    elif [ -n "$new_host" ]; then
        args_json="\"host\": \"${new_host}\""
    else
        args_json="\"port\": ${new_port}"
    fi

    local payload
    payload=$(cat <<EOF
{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
        "name": "mcp_self_monitor",
        "arguments": {
            "metric": "change_port",
            ${args_json}
        }
    },
    "id": 1
}
EOF
)

    local response
    response=$(timeout 15 curl -s -X POST "http://${host}:${port}/jsonrpc" \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>&1) || true

    if echo "$response" | grep -q '"success": true'; then
        print_ok "热重启成功！"
        echo "  $response"
        return 0
    fi

    # 热重启失败，输出详细错误
    print_warn "热重启失败，服务返回:"
    echo "  ${response}"
    print_step "降级为 systemctl 冷重启..."
    return 1
}

# ---- systemd 冷重启 ----
cold_restart_server() {
    print_step "正在通过 systemctl 重启服务..."
    print_step "重新加载 systemd 配置..."
    systemctl daemon-reload || true

    print_step "重启服务中..."
    if systemctl restart "$SERVICE_NAME" 2>/tmp/mcp_wizard_restart_err.log; then
        sleep 3
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            print_ok "服务已重启"
            rm -f /tmp/mcp_wizard_restart_err.log
            return 0
        else
            print_err "服务重启后又退出了，最近日志："
            journalctl -u "$SERVICE_NAME" --no-pager -n 20 2>/dev/null || true
            rm -f /tmp/mcp_wizard_restart_err.log
            return 1
        fi
    else
        print_err "systemctl restart 命令失败！最近日志："
        journalctl -u "$SERVICE_NAME" --no-pager -n 20 2>/dev/null || true
        cat /tmp/mcp_wizard_restart_err.log 2>/dev/null || true
        rm -f /tmp/mcp_wizard_restart_err.log
        return 1
    fi
}

# ---- 验证服务实际监听地址 ----
verify_service_listening() {
    local expected_host="$1"
    local expected_port="$2"

    print_step "验证服务实际监听地址..."

    # 使用 ss 检查实际监听端口
    if command -v ss &>/dev/null; then
        local listening
        listening=$(ss -tlnp 2>/dev/null | grep -E "LISTEN.*:${expected_port}\b" || true)
        if [ -n "$listening" ]; then
            print_ok "确认服务正在监听端口 ${expected_port}:"
            echo "  ${listening}"
            return 0
        else
            print_warn "ss 未检测到端口 ${expected_port} 在监听，尝试 netstat..."
        fi
    fi

    if command -v netstat &>/dev/null; then
        local listening
        listening=$(netstat -tlnp 2>/dev/null | grep -E ":${expected_port}\b" || true)
        if [ -n "$listening" ]; then
            print_ok "确认服务正在监听端口 ${expected_port}:"
            echo "  ${listening}"
            return 0
        fi
    fi

    print_warn "无法确认服务是否在端口 ${expected_port} 监听"
    print_step "请手动检查: ss -tlnp | grep ${expected_port}"
    return 1
}

# ---- 连接测试 ----
test_connection() {
    local host="$1"
    local port="$2"
    local token="$3"

    echo ""
    print_step "正在测试连接到 ${host}:${port} ..."

    # 1. 测试 TCP 连通性
    if ! timeout 5 bash -c "echo >/dev/tcp/${host}/${port}" 2>/dev/null; then
        print_err "无法连接到 ${host}:${port} —— 请检查："
        echo "    - 服务是否正在运行: systemctl status ${SERVICE_NAME}"
        echo "    - 防火墙是否放行端口 ${port}"
        echo "    - 监听地址是否为 ${host}"
        return 1
    fi
    print_ok "TCP 连接成功"

    # 2. 测试 JSON-RPC ping（无认证）
    local ping_noauth
    ping_noauth=$(timeout 10 curl -s -X POST "http://${host}:${port}/jsonrpc" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"ping","id":1}' 2>&1) || true

    if echo "$ping_noauth" | grep -q '"jsonrpc"'; then
        print_ok "JSON-RPC 服务响应正常"
    else
        print_err "JSON-RPC 服务无响应，原始返回:"
        echo "  $ping_noauth"
        return 1
    fi

    # 3. 测试 Bearer Token 认证
    # M3: 使用 -o 分离 body，-w 获取 HTTP 状态码，避免 stderr 污染
    local ping_body_file="/tmp/mcp_wizard_ping_body.txt"
    local http_code
    http_code=$(timeout 10 curl -s -o "$ping_body_file" -w "%{http_code}" \
        -X POST "http://${host}:${port}/jsonrpc" \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"ping","id":1}' 2>/dev/null) || true

    local body
    body=$(cat "$ping_body_file" 2>/dev/null || echo "")
    rm -f "$ping_body_file"

    if [ "$http_code" = "200" ]; then
        if echo "$body" | grep -q '"pong":true'; then
            print_ok "Token 认证通过！服务器正常响应"
            echo ""
            echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
            echo -e "${GREEN}║  ✓ 连接测试全部通过！MCP Server 运行正常 ║${NC}"
            echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
            return 0
        else
            print_warn "HTTP 200 但未返回 pong，响应:"
            echo "  $body"
            return 0
        fi
    elif [ "$http_code" = "401" ]; then
        print_err "Token 认证失败（HTTP 401）"
        echo "  请确认 Token 是否正确。当前 Token: ${token}"
        return 1
    else
        print_err "连接测试失败（HTTP ${http_code}）"
        echo "  响应: $body"
        return 1
    fi
}

# ---- 菜单选项 1: 修改监听地址 ----
menu_change_address() {
    echo ""
    echo -e "${CYAN}━━━ 修改监听地址 ━━━${NC}"
    echo ""

    local cur_host cur_port
    cur_host=$(read_current_host)
    cur_port=$(read_current_port)
    local cur_token
    cur_token=$(read_current_token)

    echo "  当前监听地址: ${cur_host}:${cur_port}"
    echo ""

    # 输入新 HOST
    local new_host
    read -p "  新 HOST (回车保持 ${cur_host}): " new_host
    new_host="${new_host:-$cur_host}"

    # 输入新 PORT
    local new_port
    read -p "  新 PORT (回车保持 ${cur_port}): " new_port
    new_port="${new_port:-$cur_port}"

    # 验证 PORT 是数字且在合法范围
    if ! [[ "$new_port" =~ ^[0-9]+$ ]] || [ "$new_port" -lt 1 ] || [ "$new_port" -gt 65535 ]; then
        print_err "无效的端口号: ${new_port}（必须在 1-65535 之间）"
        return
    fi

    if [ "$new_host" = "$cur_host" ] && [ "$new_port" = "$cur_port" ]; then
        print_warn "新地址与当前地址相同，无需修改"
        return
    fi

    echo ""
    echo -e "${YELLOW}即将执行以下操作:${NC}"
    echo "  1. 更新 ${ENV_FILE} 中的 MCP_HOST / MCP_PORT"
    echo "  2. 更新 systemd 服务文件中的 MCP_HOST / MCP_PORT"
    echo "  3. 防火墙放行新端口 ${new_port}/tcp"
    if [ "$new_port" != "$cur_port" ]; then
        echo "  4. 可选：清理旧端口 ${cur_port}/tcp 的防火墙规则"
    fi
    echo "  5. 热重启 MCP Server（使新地址立即生效）"
    echo ""

    read -p "  确认执行？(y/n，默认 n): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        print_warn "已取消"
        return
    fi

    echo ""

    # 1. 更新 .env
    update_env_value "MCP_HOST" "$new_host"
    update_env_value "MCP_PORT" "$new_port"

    # 2. 更新 systemd 服务文件
    update_service_env "MCP_HOST" "$new_host"
    update_service_env "MCP_PORT" "$new_port"

    # 3. 防火墙放行新端口
    if [ "$new_port" != "$cur_port" ]; then
        configure_firewall "$new_port" "add"
    fi

    # 4. 尝试热重启
    echo ""
    local restart_ok=false
    if command -v curl &>/dev/null; then
        # 使用当前 Token（从 .env 读取）
        if hot_restart_server "$cur_host" "$cur_port" "$cur_token" "$new_host" "$new_port"; then
            restart_ok=true
            verify_service_listening "$new_host" "$new_port"
        else
            # 热重启失败，降级为冷重启
            print_step "热重启失败，使用 systemctl 冷重启..."
            if cold_restart_server; then
                restart_ok=true
                verify_service_listening "$new_host" "$new_port"
            fi
        fi
    else
        print_warn "未安装 curl，无法热重启，使用 systemctl 冷重启..."
        if cold_restart_server; then
            restart_ok=true
            verify_service_listening "$new_host" "$new_port"
        fi
    fi

    if [ "$restart_ok" = false ]; then
        print_err "服务未能成功重启，配置变更已写入但未生效"
        print_step "请手动检查并重启服务: systemctl restart ${SERVICE_NAME}"
        return
    fi

    # 5. 可选：清理旧端口防火墙规则
    if [ "$new_port" != "$cur_port" ]; then
        echo ""
        read -p "  是否清理旧端口 ${cur_port}/tcp 的防火墙规则？(y/n，默认 n): " clean_old
        if [ "$clean_old" = "y" ] || [ "$clean_old" = "Y" ]; then
            configure_firewall "$cur_port" "remove"
        fi
    fi

    echo ""
    print_ok "监听地址修改完成！"
}

# ---- 菜单选项 2: 修改认证 Token ----
menu_change_token() {
    echo ""
    echo -e "${CYAN}━━━ 修改认证 Token ━━━${NC}"
    echo ""

    local cur_token
    cur_token=$(read_current_token)
    local cur_host cur_port
    cur_host=$(read_current_host)
    cur_port=$(read_current_port)

    if [ -z "$cur_token" ]; then
        echo "  当前 Token: (未设置)"
    else
        # 显示 Token 的前4位和后4位，中间用 * 代替
        local masked
        if [ ${#cur_token} -le 8 ]; then
            masked="***"
        else
            masked="${cur_token:0:4}****${cur_token: -4}"
        fi
        echo "  当前 Token: ${masked}"
    fi
    echo ""

    echo "  请选择 Token 来源:"
    echo "    1) 随机生成（推荐，使用 openssl rand -hex 32）"
    echo "    2) 手动输入"
    echo ""
    read -p "  请选择 (1/2，默认 1): " token_choice
    token_choice="${token_choice:-1}"

    local new_token
    if [ "$token_choice" = "2" ]; then
        read -p "  请输入新 Token: " new_token
        if [ -z "$new_token" ]; then
            print_err "Token 不能为空"
            return
        fi
    else
        if command -v openssl &>/dev/null; then
            new_token=$(openssl rand -hex 32)
            echo "  已随机生成 Token: ${new_token}"
        else
            new_token=$(head -c 32 /dev/urandom | xxd -p -c 32 2>/dev/null || head -c 32 /dev/urandom | od -A n -t x1 | tr -d ' \n')
            echo "  已随机生成 Token: ${new_token}"
        fi
    fi

    echo ""
    echo -e "${YELLOW}即将执行以下操作:${NC}"
    echo "  1. 更新 ${ENV_FILE} 中的 API_TOKEN"
    echo "  2. 冷重启 MCP Server（Token 变更需要重启进程）"
    echo "  3. 测试新 Token 是否生效"
    echo ""

    read -p "  确认执行？(y/n，默认 n): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        print_warn "已取消"
        return
    fi

    echo ""

    # 1. 更新 .env
    update_env_value "API_TOKEN" "$new_token"

    # 2. 冷重启
    cold_restart_server || {
        print_err "服务重启失败，Token 可能未生效"
        return
    }

    # 3. 测试
    test_connection "$cur_host" "$cur_port" "$new_token"

    # 如果是随机生成的，提醒用户保存
    if [ "$token_choice" != "2" ]; then
        echo ""
        echo -e "${YELLOW}  ╔══════════════════════════════════════════════╗${NC}"
        echo -e "${YELLOW}  ║  ⚠ 请妥善保存新 Token！                    ║${NC}"
        echo -e "${YELLOW}  ║  Token: ${new_token}${NC}"
        echo -e "${YELLOW}  ╚══════════════════════════════════════════════╝${NC}"
        echo ""
        echo "  Token 已保存到 ${ENV_FILE}，如需查看:"
        echo "    cat ${ENV_FILE} | grep API_TOKEN"
    fi
}

# ---- 菜单选项 3: 连接测试 ----
menu_test_connection() {
    echo ""
    echo -e "${CYAN}━━━ 连接测试 ━━━${NC}"
    echo ""

    local cur_host cur_port cur_token
    cur_host=$(read_current_host)
    cur_port=$(read_current_port)
    cur_token=$(read_current_token)

    echo "  将从 ${ENV_FILE} 读取配置..."
    echo ""

    # 允许用户覆盖测试目标
    read -p "  测试 HOST (回车使用 ${cur_host}): " test_host
    test_host="${test_host:-$cur_host}"

    read -p "  测试 PORT (回车使用 ${cur_port}): " test_port
    test_port="${test_port:-$cur_port}"

    read -p "  测试 Token (回车使用 .env 中的 Token): " test_token
    test_token="${test_token:-$cur_token}"

    if [ -z "$test_token" ]; then
        print_warn "Token 为空，测试可能失败"
    fi

    test_connection "$test_host" "$test_port" "$test_token"
}

# ---- 菜单选项 4: 查看当前配置 ----
menu_view_config() {
    echo ""
    echo -e "${CYAN}━━━ 当前配置 ━━━${NC}"
    echo ""

    echo -e "${BLUE}  .env 文件 (${ENV_FILE}):${NC}"
    if [ -f "$ENV_FILE" ]; then
        echo "  ─────────────────────────────────────────────"
        while IFS= read -r line; do
            # 对 API_TOKEN 行做脱敏
            if [[ "$line" == API_TOKEN=* ]]; then
                local token_val="${line#API_TOKEN=}"
                if [ -n "$token_val" ]; then
                    if [ ${#token_val} -le 8 ]; then
                        echo "  API_TOKEN=***"
                    else
                        echo "  API_TOKEN=${token_val:0:4}****${token_val: -4}"
                    fi
                else
                    echo "  API_TOKEN=(未设置)"
                fi
            else
                echo "  $line"
            fi
        done < "$ENV_FILE"
        echo "  ─────────────────────────────────────────────"
    else
        echo "  (文件不存在)"
    fi

    echo ""
    echo -e "${BLUE}  服务状态:${NC}"
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        print_ok "mcp-server 正在运行"
        systemctl status "$SERVICE_NAME" --no-pager -l 2>/dev/null | head -15 || true
    else
        print_warn "mcp-server 未运行"
    fi
}

# ---- 服务启动（仅在服务未运行时尝试） ----
ensure_service_running() {
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        return 0
    fi

    print_warn "mcp-server 服务未运行"
    read -p "  是否启动服务？(y/n，默认 n): " start_it
    if [ "$start_it" = "y" ] || [ "$start_it" = "Y" ]; then
        systemctl start "$SERVICE_NAME" || {
            print_err "服务启动失败！请查看日志: sudo journalctl -u ${SERVICE_NAME} -n 50"
            return 1
        }
        print_ok "服务已启动"
        return 0
    fi
    return 1
}

# ---- 主菜单 ----
main_menu() {
    while true; do
        echo ""
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${CYAN}  MCP Server 配置向导${NC}"

        local cur_host cur_port
        cur_host=$(read_current_host)
        cur_port=$(read_current_port)

        echo "  当前监听: ${cur_host}:${cur_port}"
        echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo "  [1] 修改监听地址 + 防火墙放行"
        echo "  [2] 修改认证 Token + 测试"
        echo "  [3] 连接测试"
        echo "  [4] 查看当前配置"
        echo "  [0] 退出"
        echo ""
        read -p "  请选择 (0-4): " choice

        case "$choice" in
            1)
                menu_change_address
                ;;
            2)
                menu_change_token
                ;;
            3)
                menu_test_connection
                ;;
            4)
                menu_view_config
                ;;
            0)
                echo ""
                echo -e "${GREEN}  再见！${NC}"
                echo ""
                exit 0
                ;;
            *)
                print_warn "无效选项，请输入 0-4"
                ;;
        esac
    done
}

# ============================================================
# 入口
# ============================================================

# ---- 检查 root 权限 ----
if [ "$(id -u)" -ne 0 ]; then
    print_err "请使用 sudo 运行此脚本: sudo ./kylin-wizard.sh"
    exit 1
fi

# ---- 检查是否已安装 ----
if [ ! -f "$SERVICE_FILE" ]; then
    print_err "未检测到 mcp-server 服务文件 (${SERVICE_FILE})"
    echo "  请先运行安装脚本: sudo ./kylin-install.sh"
    exit 1
fi

# ---- 确保 .env 文件存在 ----
if [ ! -f "$ENV_FILE" ]; then
    print_warn "${ENV_FILE} 不存在，正在创建..."

    # 优先从 systemd 服务文件中提取当前环境变量
    _svc_host=$(grep -oP 'Environment=MCP_HOST=\K.*' "$SERVICE_FILE" 2>/dev/null || echo "127.0.0.1")
    _svc_port=$(grep -oP 'Environment=MCP_PORT=\K.*' "$SERVICE_FILE" 2>/dev/null || echo "8001")

    # M1: 随机生成默认 Token，避免硬编码不安全值
    if command -v openssl &>/dev/null; then
        _auto_token=$(openssl rand -hex 32)
    else
        _auto_token=$(head -c 32 /dev/urandom | xxd -p -c 32 2>/dev/null || head -c 32 /dev/urandom | od -A n -t x1 | tr -d ' \n')
    fi

    cat > "$ENV_FILE" <<EOF2
# MCP Server 环境变量（由配置向导自动生成）
MCP_HOST=${_svc_host}
MCP_PORT=${_svc_port}
API_TOKEN=${_auto_token}
COMMAND_TIMEOUT=30
MAX_OUTPUT_LINES=1000
LOG_FILE=/var/log/mcp-server.log
LOG_LEVEL=INFO
EOF2
    print_ok "已从 systemd 服务配置生成 ${ENV_FILE}"
    print_warn "已自动生成 API_TOKEN，请妥善保存: ${_auto_token}"
fi

print_banner
print_step "初始化..."
ensure_service_running || print_warn "部分功能需要服务运行才能使用（如热重启）"

main_menu