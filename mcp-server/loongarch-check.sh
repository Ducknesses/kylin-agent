#!/bin/bash
# ============================================================
# LoongArch 兼容性验证脚本
# ============================================================
# 用途: 在麒麟 V11 + LoongArch 上验证所有运行依赖
# 用法: ./loongarch-check.sh
# ============================================================
set -e

echo "=============================================="
echo "  MCP Server - LoongArch 兼容性验证"
echo "=============================================="
echo ""

# ---- 1. 架构检测 ----
echo "[1/8] 检测 CPU 架构..."
ARCH=$(uname -m)
echo "  架构: $ARCH"
case "$ARCH" in
    loongarch64|loongarch)
        echo "  ✓ 检测到 LoongArch 架构"
        ;;
    x86_64|aarch64)
        echo "  ⚠  非 LoongArch 架构 ($ARCH)，交叉测试模式"
        ;;
    *)
        echo "  ⚠  未知架构: $ARCH"
        ;;
esac
echo ""

# ---- 2. Python 版本 ----
echo "[2/8] 检查 Python 版本..."
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 --version 2>&1)
    echo "  ✓ $PY_VERSION"
else
    echo "  ✗ python3 未安装！请执行: sudo apt install python3"
    exit 1
fi
echo ""

# ---- 3. pip 可用性 ----
echo "[3/8] 检查 pip..."
if command -v pip3 &>/dev/null; then
    echo "  ✓ pip3 可用 ($(pip3 --version 2>&1 | head -1))"
else
    echo "  ⚠  pip3 未找到，将使用 python3 -m pip"
fi
echo ""

# ---- 4. psutil 安装与导入测试 ----
echo "[4/8] 测试 psutil 安装..."
if python3 -c "import psutil" 2>/dev/null; then
    PSUTIL_VER=$(python3 -c "import psutil; print(psutil.__version__)")
    echo "  ✓ psutil $PSUTIL_VER 已安装"
else
    echo "  ⚠  psutil 未安装，尝试安装..."
    pip3 install psutil 2>/dev/null || python3 -m pip install psutil
    if python3 -c "import psutil" 2>/dev/null; then
        echo "  ✓ psutil 安装成功"
    else
        echo "  ✗ psutil 安装失败！请检查: sudo apt install python3-dev gcc"
        exit 1
    fi
fi

# psutil 功能验证
echo "  功能验证:"
python3 -c "
import psutil
print(f'    CPU 核心数: {psutil.cpu_count()}')
print(f'    CPU 使用率: {psutil.cpu_percent(interval=0.5)}%')
print(f'    内存总量:   {psutil.virtual_memory().total // (1024*1024)} MB')
"
echo ""

# ---- 5. systemctl 可用性 ----
echo "[5/8] 检查 systemctl..."
if command -v systemctl &>/dev/null; then
    SYSCTL_VER=$(systemctl --version 2>&1 | head -1)
    echo "  ✓ $SYSCTL_VER"
else
    echo "  ✗ systemctl 不可用！麒麟 V11 基于 systemd，请检查系统完整性"
    exit 1
fi
echo ""

# ---- 6. journalctl 可用性 ----
echo "[6/8] 检查 journalctl..."
if command -v journalctl &>/dev/null; then
    JCTL_VER=$(journalctl --version 2>&1 | head -1)
    echo "  ✓ $JCTL_VER"
else
    echo "  ⚠  journalctl 不可用（可能使用了其他日志系统）"
fi
echo ""

# ---- 7. ss 命令可用性 ----
echo "[7/8] 检查 ss 命令（网络工具）..."
if command -v ss &>/dev/null; then
    SS_VER=$(ss --version 2>&1 | head -1 || echo "可用")
    echo "  ✓ $SS_VER"
else
    echo "  ✗ ss 命令不可用！请安装: sudo apt install iproute2"
fi
echo ""

# ---- 8. 网络接口检查 ----
echo "[8/8] 检查网络接口..."
if command -v ip &>/dev/null; then
    echo "  ✓ ip 命令可用"
    INTERFACES=$(ip -br addr 2>/dev/null | wc -l)
    echo "  网络接口数: $INTERFACES"
else
    echo "  ✗ ip 命令不可用"
fi
echo ""

# ---- 文件权限检查 ----
echo "--- 文件系统检查 ---"
echo "  /var/log/ 可读: $([ -r /var/log ] && echo '✓' || echo '✗')"
echo "  /proc/ 可读:    $([ -r /proc/cpuinfo ] && echo '✓' || echo '✗')"
echo "  /sys/ 可读:     $([ -r /sys/devices ] && echo '✓' || echo '✗')"
echo "  /tmp/ 可写:     $([ -w /tmp ] && echo '✓' || echo '✗')"
echo ""

echo "=============================================="
echo "  ✓ LoongArch 兼容性验证完成"
echo "=============================================="
echo ""
echo "  如需安装 MCP Server，请执行:"
echo "    sudo ./kylin-install.sh"
echo ""