import os, sys
sys.path.insert(0, "/opt/wikijs-mcp-http/src")
from wikijs_mcp.server import WikiJSMCPServer

def main():
    server = WikiJSMCPServer()
    server.app.settings.host = os.environ.get("MCP_HTTP_HOST", "0.0.0.0")
    server.app.settings.port = int(os.environ.get("MCP_HTTP_PORT", "8099"))

    # 외부(Tailscale Funnel/nginx)를 거치는 구성이라, FastMCP의 기본 DNS 리바인딩
    # 방지(allowed_hosts가 127.0.0.1/localhost로만 제한됨)를 끔.
    # 앞단 nginx의 GET 차단 + Tailscale Funnel 인증으로 이미 한 겹 보호되고 있음.
    server.app.settings.transport_security.enable_dns_rebinding_protection = False

    print(f"Starting wikijs-mcp (streamable-http) on {server.app.settings.host}:{server.app.settings.port}")
    server.app.run(transport="streamable-http")

if __name__ == "__main__":
    main()
