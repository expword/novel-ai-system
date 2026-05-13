"""
启动可视化前端：
    python run_web.py            # 默认 http://127.0.0.1:5000
    python run_web.py --port 8080
    python run_web.py --host 0.0.0.0 --port 8080  # 局域网访问

需要先有 output/checkpoint/state.json（即至少跑过一次 python main.py）。
"""
import argparse
from web.app import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    run(host=args.host, port=args.port, debug=args.debug)
