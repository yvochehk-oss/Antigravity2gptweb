#!/bin/bash
# ==============================================================================
# 成都建工·天府掌舵 —— Cloudflare 零配置安全穿透隧道 (Mac / Linux)
# ==============================================================================

GREEN='\033[0;32m'
GOLD='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GOLD}==============================================================================${NC}"
echo -e "${GOLD}  🚀 正在启动 成都建工 Cloudflare 远程安全穿透隧道${NC}"
echo -e "${GOLD}==============================================================================${NC}"

# 1. 检查 cloudflared 是否已安装
if ! command -v cloudflared &> /dev/null; then
    echo -e "${CYAN}[1/2] 未检测到 cloudflared 工具，正在通过 brew 自动安装...${NC}"
    brew install cloudflared || {
        echo -e "${RED}❌ 安装 cloudflared 失败，请先在终端运行: brew install cloudflared${NC}"
        exit 1
    }
fi

# 2. 检查本地 RAG/Executive 后端服务 (Port 8922) 是否在线
if ! nc -z 127.0.0.1 8922 2>/dev/null; then
    echo -e "${RED}⚠️ 检测到本地 8922 端口服务未运行，正在启动服务...${NC}"
    cd "$(dirname "$0")/../"
    ./start_all.sh
fi

echo -e "${CYAN}[2/2] 正在创建免费端到端加密公网安全隧道 (直连本地 8922 端口)...${NC}"
echo ""

# 3. 运行 cloudflared 快速隧道并捕获生成的 HTTPS 链接
cloudflared tunnel --url http://127.0.0.1:8922 2>&1 | while read -r line; do
    echo "$line"
    if [[ "$line" =~ (https://[a-zA-Z0-9-]+\.trycloudflare\.com) ]]; then
        TUNNEL_URL="${BASH_REMATCH[1]}"
        echo ""
        echo -e "${GREEN}==============================================================================${NC}"
        echo -e "${GREEN}  🎉 Cloudflare 远程安全隧道已建立成功！${NC}"
        echo -e "${GOLD}  📱 老板端 Android App 远程连接地址 (复制至 App 设置页):${NC}"
        echo -e "${GOLD}     ➔ ${TUNNEL_URL}${NC}"
        echo -e "${GREEN}==============================================================================${NC}"
        echo -e "${CYAN}💡 提示：保持此终端窗口运行，老板在户外任何 4G/5G 手机网络均可直连本地数据！${NC}"
        echo ""
    fi
done
