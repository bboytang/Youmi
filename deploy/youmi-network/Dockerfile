FROM docker.io/library/alpine@sha256:ee16d92f96db08d9d2d07da48555a27f3a69d8f0759021dbc601195ed0d2b5bd

RUN apk add --no-cache python3 iptables iproute2
WORKDIR /opt/youmi-network
COPY xray_config.py firewall_plan.py install_rules.py keeper.py ./
HEALTHCHECK --interval=5s --timeout=3s --retries=6 CMD python3 -c "import urllib.request; assert urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:31992/ready', timeout=2).read() == b'youmi-network-ready'"
ENTRYPOINT ["python3", "/opt/youmi-network/keeper.py"]
