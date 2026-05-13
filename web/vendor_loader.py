"""
首次启动时把 Alpine / vis-network / Chart.js 下载到 web/static/vendor/，
之后 index.html 直接 serve 本地文件——比走国外 CDN 快 10-100 倍。

幂等：已经下载过的文件不会重复拉。
"""
from __future__ import annotations
import os
import sys
import ssl
import urllib.request
import urllib.error

VENDOR_DIR = os.path.join(os.path.dirname(__file__), "static", "vendor")

# 每个库多镜像源——国内友好镜像优先，国际 CDN 兜底
# 顺序上优先 npmmirror（淘宝镜像，国内最稳）→ jsdelivr CN → jsdelivr → unpkg
VENDOR_SOURCES = {
    "alpine.min.js": [
        "https://registry.npmmirror.com/alpinejs/3.13.3/files/dist/cdn.min.js",
        "https://cdn.jsdmirror.com/npm/alpinejs@3.13.3/dist/cdn.min.js",
        "https://fastly.jsdelivr.net/npm/alpinejs@3.13.3/dist/cdn.min.js",
        "https://cdn.jsdelivr.net/npm/alpinejs@3.13.3/dist/cdn.min.js",
    ],
    "vis-network.min.js": [
        "https://registry.npmmirror.com/vis-network/latest/files/standalone/umd/vis-network.min.js",
        "https://cdn.jsdmirror.com/npm/vis-network/standalone/umd/vis-network.min.js",
        "https://fastly.jsdelivr.net/npm/vis-network/standalone/umd/vis-network.min.js",
        "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js",
    ],
    "chart.umd.min.js": [
        "https://registry.npmmirror.com/chart.js/4.4.1/files/dist/chart.umd.min.js",
        "https://cdn.jsdmirror.com/npm/chart.js@4.4.1/dist/chart.umd.min.js",
        "https://fastly.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js",
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js",
    ],
}


def _download(url: str, dest: str) -> tuple[bool, str]:
    # 宽松 SSL context（绕过某些 Windows Python 的 CA 链问题）
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = resp.read()
        if len(data) < 1000:
            return False, f"too small ({len(data)} bytes)"
        with open(dest, "wb") as f:
            f.write(data)
        return True, f"{len(data)//1024} KB"
    except urllib.error.URLError as e:
        return False, f"URLError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def ensure_vendor_libs(verbose: bool = True) -> dict[str, bool]:
    """
    确保 vendor 目录里三个库齐全。缺失的自动下载——每个文件会依次尝试多个镜像。
    返回 {filename: present}。
    """
    os.makedirs(VENDOR_DIR, exist_ok=True)
    results: dict[str, bool] = {}

    for fname, urls in VENDOR_SOURCES.items():
        dest = os.path.join(VENDOR_DIR, fname)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            results[fname] = True
            continue

        if verbose:
            print(f"  [...] {fname}")
        ok_any = False
        for i, url in enumerate(urls, 1):
            if verbose:
                # 截短 URL 便于阅读
                short = url.split("/")[2]
                print(f"    try #{i} ({short}) ...", end=" ", flush=True)
            ok, info = _download(url, dest)
            if verbose:
                print("[OK]" if ok else "[FAIL]", info)
            if ok:
                ok_any = True
                break
        results[fname] = ok_any

    return results


if __name__ == "__main__":
    r = ensure_vendor_libs(verbose=True)
    missing = [k for k, v in r.items() if not v]
    if missing:
        print(f"\n[WARN] failed: {missing}")
        print("  index.html will fallback to CDN automatically.")
        sys.exit(1)
    print(f"\n[OK] all vendor libs ready ({VENDOR_DIR})")
