#!/usr/bin/env python3
"""番剧更新提醒检查器
数据流:
  WebDAV (Kazumi collectibles.hive 备份)  → 收藏的番剧 (Bangumi subject id)
  Bangumi (next.bgm.tv 匿名 API)          → 每部剧的剧集放送表 (airdate)

逻辑 (以 Bangumi 放送表为唯一权威):
  · 话数 = 该条目内已放送(airdate<=今天)的本篇集数
  · 首次建档: 基线取到"昨天为止"的放送, 仅当今天正好有新放送才提醒一次
  · 之后: 每当出现比上次记录更新的 airdate → 提醒, 去重
用法:
  python3 check_updates.py            # 检查并输出提醒文本(stdout)
  python3 check_updates.py --dump     # 调试: 打印收藏+放送状态
  python3 check_updates.py --force    # 忽略 state 基线(模拟首跑/重扫)
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import date
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COLLECT_TMP = os.environ.get(
    "KAZUMI_COLLECT_TMP", "/opt/kazumi-webdav/data/kazumiSync/collectibles.tmp")
SNAPSHOT = os.environ.get(
    "KAZUMI_SNAPSHOT", "/opt/kazumi-webdav/data/kazumiSync/history/snapshot.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
UA = "KazumiUpdateChecker/1.0 (personal reminder bot)"

WATCH_TYPES = (1, 2)  # 1=在看 2=想看
TYPE_LABEL = {1: "在看", 2: "想看", 3: "搁置", 4: "看过", 5: "抛弃"}


def http_json(url, params=None):
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------- 解析 WebDAV 收藏 ----------
def load_collects():
    sys.path.insert(0, BASE_DIR)
    from hive_reader import collectibles_from_box
    with open(COLLECT_TMP, "rb") as fp:
        data = fp.read()
    out = []
    for it in collectibles_from_box(data):
        bi = it["bangumiItem"]
        if bi.get("id") is None:
            continue
        out.append({
            "bangumiId": bi["id"],
            "name": bi.get("nameCn") or bi.get("name") or "",
            "collectType": it.get("collectType"),
        })
    return out


# ---------- Bangumi: 放送状态 ----------
def bangumi_episodes(bangumi_id):
    """返回按 sort 排序的本篇剧集列表 [{sort,airdate,name,nameCN}], 失败返回 None"""
    try:
        d = http_json(f"https://next.bgm.tv/p1/subjects/{bangumi_id}/episodes",
                      {"type": 0, "limit": 200})
    except Exception as e:
        print(f"[warn] 查询 {bangumi_id} 失败: {e}", file=sys.stderr)
        return None
    data = d.get("data") or []
    eps = [e for e in data if e.get("type") == 0 and e.get("sort") is not None]
    eps.sort(key=lambda e: e["sort"])
    return eps


def state_of(ep_list, today):
    """计算放送状态: 话数=该条目内相对序号(从1), 与 airdate"""
    aired = [e for e in ep_list if e.get("airdate") and e["airdate"] <= today]
    if not aired:
        return {"airedEp": 0, "lastAirdate": None, "lastName": ""}
    last = aired[-1]
    # 相对话数: 找到 last 在 ep_list 中的位置 (跳过无 airdate 的剧集)
    return {
        "airedEp": len(aired),
        "lastAirdate": last["airdate"],
        "lastName": (last.get("nameCN") or last.get("name") or "").strip(),
    }


# ---------- state ----------
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略state, 模拟首跑")
    ap.add_argument("--email", action="store_true", help="有更新时发送邮件提醒")
    args = ap.parse_args()

    collects = load_collects()
    state = {} if args.force else load_state()
    today = date.today().isoformat()

    if args.dump:
        print(f"收藏 {len(collects)} 部:")
        for c in collects:
            eps = bangumi_episodes(c["bangumiId"])
            if eps is None:
                print(f"  [{TYPE_LABEL.get(c['collectType'])}] {c['name']} 查询失败")
                continue
            st = state_of(eps, today)
            print(f"  [{TYPE_LABEL.get(c['collectType'])}] {c['name']} "
                  f"(bgm:{c['bangumiId']}) 已放送 {st['airedEp']} 话, "
                  f"最新 {st['lastAirdate']} {st['lastName']!r}")
        return

    reminders = []
    for c in collects:
        if c["collectType"] not in WATCH_TYPES:
            continue
        bid = str(c["bangumiId"])
        eps = bangumi_episodes(c["bangumiId"])
        if eps is None:
            continue
        st = state_of(eps, today)
        rec = state.get(bid, {})
        prev_airdate = rec.get("lastAirdate")

        if prev_airdate is None:
            # 首跑/新收藏: 基线 = 最新放送. 特殊: 最新放送日==今天 → 值得提醒一次
            new_ep = st["airedEp"] > 0 and st["lastAirdate"] == today
            if new_ep:
                reminders.append((c, st))
            state[bid] = {
                "name": c["name"],
                "lastAirdate": st["lastAirdate"],
                "airedEp": st["airedEp"],
            }
            continue

        # 常规: 有新放送(airdate 更新) → 提醒
        if (st["lastAirdate"] and st["lastAirdate"] > prev_airdate):
            reminders.append((c, st))
        state[bid] = {
            "name": c["name"],
            "lastAirdate": st["lastAirdate"],
            "airedEp": st["airedEp"],
        }

    save_state(state)

    if reminders:
        lines = [f"📅 {today} 番剧更新提醒:"]
        for c, st in reminders:
            title = c["name"]
            ep = st["airedEp"]
            name = f"「{st['lastName']}」" if st["lastName"] else ""
            lines.append(f"• {title}: 第{ep}话 {name}已放送 ({st['lastAirdate']})")
        text = "\n".join(lines)
        print(text)
        if args.email:
            try:
                sys.path.insert(0, BASE_DIR)
                from send_mail import send
                send("📺 番剧更新提醒 " + today, text)
            except FileNotFoundError:
                print("[warn] mail_config.json 不存在, 跳过邮件", file=sys.stderr)
            except Exception as e:
                print(f"[warn] 邮件发送失败: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
