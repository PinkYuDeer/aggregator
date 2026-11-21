# -*- coding: utf-8 -*-

# @Author  : wzdnzd
# @Time    : 2026-11-21
#
# 只做一件事：把 process.py 的产出路径改到指定的 Gist。
#
# 输入（环境变量）：
#   PROCESS_GIST_LINK  必需，格式 'username/gist_id'，即 process 的产出 Gist
#   SUBSCRIBE_CONF     可选，process 的基础配置（URL 或本地文件），缺省时使用
#                      subscribe/examples/config.gist.json
#   GIST_LINK          可选，collect 的产出 Gist，格式 'username/gist_id'；
#                      缺省配置下会把它的 clash.yaml 作为节点来源
#
# 行为：读取基础配置 -> 只覆盖 storage 段（engine=gist，全部 item 指向
# PROCESS_GIST_LINK，补齐缺失的 filename 与 group 引用项）-> 写出目标配置。
# 除 storage 外的任何字段都不会被修改。

import json
import os
import re
import sys
import urllib.parse
import urllib.request

PATH = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
TEMPLATE = os.path.join(PATH, "subscribe", "examples", "config.gist.json")
DEFAULT_OUTPUT = os.path.join(PATH, "data", "process.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# item 名 -> 产出文件名，未命中时按名字里的关键字推断
KNOWN_FILES = {
    "all-clash": "clash.yaml",
    "all-v2ray": "v2ray.txt",
    "all-singbox": "singbox.json",
    "clash": "clash.yaml",
    "v2ray": "v2ray.txt",
    "singbox": "singbox.json",
    "crawledsubs": "crawledsubs.txt",
    "crawledproxies": "crawledproxies.yaml",
}


def fail(message: str) -> None:
    print(f"[GistConfError] {message}", file=sys.stderr)
    sys.exit(1)


def trim(value: object) -> str:
    return str(value).strip() if isinstance(value, str) or isinstance(value, (int, float)) else ""


def parse_link(raw: str, name: str) -> tuple[str, str]:
    text = trim(raw)
    if not text:
        return "", ""

    words = text.replace("https://gist.github.com/", "").replace("https://gist.githubusercontent.com/", "")
    words = words.split("?", maxsplit=1)[0].strip("/").split("/")
    words = [x for x in words if x]
    if len(words) != 2:
        fail(f"{name} 必须是 'username/gist_id' 格式，当前值: {text}")

    username, gist_id = words[0].strip(), words[1].strip()
    if not username or not gist_id or not re.match(r"^[0-9a-zA-Z]+$", gist_id):
        fail(f"{name} 不合法，必须是 'username/gist_id' 格式，当前值: {text}")

    return username, gist_id


def guess_filename(name: str) -> str:
    key = trim(name).lower()
    if key in KNOWN_FILES:
        return KNOWN_FILES[key]
    if "clash" in key:
        return "clash.yaml"
    if "singbox" in key or "sing-box" in key:
        return "singbox.json"
    if "v2ray" in key:
        return "v2ray.txt"
    return f"{key or 'output'}.txt"


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": url})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.getcode() != 200:
            fail(f"下载基础配置失败，HTTP {response.getcode()}: {url}")
        return json.loads(response.read().decode("utf8"))


def load_base() -> tuple[dict, str]:
    raw = trim(os.environ.get("SUBSCRIBE_CONF", ""))
    if raw:
        if re.match(r"^https?://", raw, flags=re.I):
            return fetch(raw), raw
        localfile = os.path.abspath(raw)
        if not os.path.isfile(localfile):
            fail(f"SUBSCRIBE_CONF 指向的文件不存在: {localfile}")
        return json.loads(open(localfile, "r", encoding="utf8").read()), localfile

    if not os.path.isfile(TEMPLATE):
        fail(f"找不到内置模板: {TEMPLATE}")
    return json.loads(open(TEMPLATE, "r", encoding="utf8").read()), TEMPLATE


def main() -> None:
    output = DEFAULT_OUTPUT
    argv = sys.argv[1:]
    for i, item in enumerate(argv):
        if item in ("-o", "--output") and i + 1 < len(argv):
            output = os.path.abspath(argv[i + 1])

    username, gist_id = parse_link(os.environ.get("PROCESS_GIST_LINK", ""), "PROCESS_GIST_LINK")
    if not gist_id:
        fail(
            "PROCESS_GIST_LINK 未设置。请在仓库 Settings -> Secrets and variables -> Actions "
            "中添加 PROCESS_GIST_LINK（值为 'username/gist_id'，指向 process 的产出 Gist），"
            "避免 process 与 collect 写到同一个 Gist。"
        )

    config, source = load_base()
    if not isinstance(config, dict):
        fail(f"基础配置不是 JSON 对象: {source}")

    collect_user, collect_id = parse_link(os.environ.get("GIST_LINK", ""), "GIST_LINK")
    sites = config.get("sites")
    if isinstance(sites, list) and collect_id:
        for site in sites:
            if not isinstance(site, dict):
                continue
            subscribe = site.get("subscribe")
            values = subscribe if isinstance(subscribe, list) else [subscribe]
            if any(not trim(x) for x in values):
                url = f"https://gist.githubusercontent.com/{collect_user}/{collect_id}/raw/clash.yaml"
                site["subscribe"] = [x if trim(x) else url for x in values]
                print(f"[GistConfInfo] 节点来源使用 collect 产出: {url}")

    storage = config.get("storage")
    if not isinstance(storage, dict):
        storage = {}
        config["storage"] = storage

    storage["engine"] = "gist"
    items = storage.get("items")
    if not isinstance(items, dict):
        items = {}
        storage["items"] = items

    # 1) 已存在的 item：只替换 gist 指向与缺失的文件名
    for name, item in list(items.items()):
        if not isinstance(item, dict):
            item = {}
            items[name] = item
        item["username"] = username
        item["gist_id"] = gist_id
        if not trim(item.get("filename", "")):
            item["filename"] = guess_filename(name)

    # 2) group 里引用但缺失的 item：补上，保证配置校验能通过
    groups = config.get("groups")
    if isinstance(groups, dict):
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            targets = group.get("targets")
            if not isinstance(targets, dict):
                continue
            for target, item_name in targets.items():
                name = trim(item_name)
                if not name:
                    continue
                if name not in items:
                    items[name] = {
                        "username": username,
                        "gist_id": gist_id,
                        "filename": guess_filename(name),
                    }
                elif isinstance(items[name], dict):
                    items[name]["username"] = username
                    items[name]["gist_id"] = gist_id

    if not items:
        fail("基础配置里没有任何 storage item，也没有可补全的 group 目标，无法确定产出文件名")

    if not isinstance(groups, dict) or not groups:
        print("[GistConfWarn] 基础配置没有 groups，process 不会产出任何文件", file=sys.stderr)

    folder = os.path.dirname(output)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(output, "w", encoding="utf8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"[GistConfInfo] 基础配置: {source}")
    print(f"[GistConfInfo] 产出 Gist: {username}/{gist_id}")
    for name in sorted(items):
        print(f"[GistConfInfo]   {name} -> {items[name].get('filename', '')}")
    print(f"[GistConfInfo] 已写出: {output}")


if __name__ == "__main__":
    main()
