#!/usr/bin/env python3
"""Kazumi WebDAV sync 数据解析器 (Hive box → Python dict)。

背景: Kazumi 把收藏(collectibles)/观看历史等存成 Hive 盒子,
WebDAV 同步时把 hive/xxx.hive 原样上传为 kazumiSync/xxx.tmp。
本模块实现 Hive 二进制帧格式的只读解析(未加密), 供更新提醒脚本使用。

帧格式 (参考 hive_ce 源码 BinaryWriterImpl/ReaderImpl):
  [4B frameLength LE] [key] [value...] [4B CRC32 LE]
  key  : 1B keyType (0=uint32, 1=utf8 len8) + payload
  value: typeId 自带; 对象类 = 1B numOfFields + 重复 [1B fieldId + typed value]
  自定义 adapter typeId 在磁盘 = adapter.typeId + 32 (reservedTypeIds)
"""
import json
import os
import struct


# ---- 内置值类型 (FrameValueType, 0..20; 21=typeIdExtension) ----
NULL_T, INT_T, DOUBLE_T, BOOL_T, STRING_T = 0, 1, 2, 3, 4
BYTE_LIST_T, INT_LIST_T, DOUBLE_LIST_T, BOOL_LIST_T, STRING_LIST_T = 5, 6, 7, 8, 9
LIST_T, MAP_T, HIVE_LIST_T = 10, 11, 12
INT_SET_T, DOUBLE_SET_T, STRING_SET_T, DATETIME_T, BIGINT_T = 13, 14, 15, 16, 17
DATETIME_TZ_T, SET_T, DURATION_T, TYPE_ID_EXT = 18, 19, 20, 21

# 内置类型里 DateTime/BigInt/Duration 的 typeId (FrameValueType) 与注册的 internal adapter typeId 相同
TYPE_TO_NAME = {
    0: "null", 1: "int", 2: "double", 3: "bool", 4: "string", 5: "bytes",
    6: "int[]", 7: "double[]", 8: "bool[]", 9: "string[]", 10: "list",
    11: "map", 12: "hiveList", 13: "intSet", 14: "doubleSet", 15: "stringSet",
    16: "datetime", 17: "bigint", 18: "datetimeTZ", 19: "set", 20: "duration",
}


class Reader:
    def __init__(self, buf):
        self.buf = buf
        self.pos = 0

    def remaining(self):
        return len(self.buf) - self.pos

    def u8(self):
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def i64_as_double(self):
        v = struct.unpack_from("<d", self.buf, self.pos)[0]
        self.pos += 8
        return v

    def bytes(self, n):
        v = self.buf[self.pos:self.pos + n]
        self.pos += n
        return v

    def type_id(self):
        t = self.u8()
        if t == TYPE_ID_EXT:
            return self.u16()
        return t

    # --- 通用 typed value ---
    def value(self, type_id=None):
        if type_id is None:
            type_id = self.type_id()
        n = TYPE_TO_NAME.get(type_id)
        if type_id == NULL_T:
            return None
        if type_id == INT_T:  # int 以 double 存储
            return int(self.i64_as_double())
        if type_id == DOUBLE_T:
            return self.i64_as_double()
        if type_id == BOOL_T:
            return self.u8() != 0
        if type_id == STRING_T:
            return self.bytes(self.u32()).decode("utf-8", "replace")
        if type_id == BYTE_LIST_T:
            return self.bytes(self.u32())
        if type_id in (INT_LIST_T, INT_SET_T):
            return [int(self.i64_as_double()) for _ in range(self.u32())]
        if type_id in (DOUBLE_LIST_T, DOUBLE_SET_T):
            return [self.i64_as_double() for _ in range(self.u32())]
        if type_id in (BOOL_LIST_T,):
            return [self.u8() != 0 for _ in range(self.u32())]
        if type_id in (STRING_LIST_T, STRING_SET_T):
            return [self.bytes(self.u32()).decode("utf-8", "replace")
                    for _ in range(self.u32())]
        if type_id in (LIST_T, SET_T):
            return [self.value() for _ in range(self.u32())]
        if type_id == MAP_T:
            return {self.value(): self.value() for _ in range(self.u32())}
        if type_id == DATETIME_T:
            return int(self.i64_as_double())  # epoch ms
        if type_id == DATETIME_TZ_T:
            # DateTimeWithTimezoneAdapter: 8B int(ms) + 1B bool(isUtc)
            ms = int(self.i64_as_double())
            self.u8()
            return ms
        if type_id == DURATION_T:
            return int(self.i64_as_double())  # microseconds
        if type_id == BIGINT_T:
            # 按 hive_ce: BigIntAdapter 存字符串? 这里少见, 尽力读字符串
            try:
                return int(self.bytes(self.u32()).decode())
            except Exception:
                return None
        if type_id == HIVE_LIST_T:
            # [count u32][boxNameLen u8][boxName][keys...]
            cnt = self.u32()
            blen = self.u8()
            self.bytes(blen)
            return [self.key() for _ in range(cnt)]
        if type_id >= 32:  # 自定义 adapter: typeId - 32
            return self.object_fields(type_id - 32)
        raise ValueError(f"未知 typeId {type_id} (n={n}) @{self.pos}")

    # 对象: [numOfFields u8] + numOfFields 次 [fieldId u8 + typed value]
    def object_fields(self, adapter_type_id):
        num = self.u8()
        fields = {}
        for _ in range(num):
            fid = self.u8()
            fields[fid] = self.value()
        return {"_adapter": adapter_type_id, "fields": fields}

    # --- key ---
    def key(self):
        kt = self.u8()
        if kt == 0:  # uint
            return self.u32()
        if kt == 1:  # utf8 string, 长度 1B
            return self.bytes(self.u8()).decode("utf-8", "replace")
        raise ValueError(f"未知 keyType {kt}")


def read_box(data: bytes):
    """解析整个 .hive/.tmp 盒子 → {key: value} (后者覆盖前者, 尊重 deleted 帧)"""
    r = Reader(data)
    out = {}
    order = []
    while r.remaining() >= 8:
        frame_start = r.pos
        frame_len = r.u32()
        if frame_len < 8 or r.remaining() < frame_len - 4:
            break  # 尾部垃圾/截断
        # CRC 在帧末尾 4B (跳过即可, 无需校验: 未加密时 seed=0 的标准 crc32)
        body_end = frame_start + frame_len - 4
        key = r.key()
        deleted = r.pos >= body_end
        val = None if deleted else r.value()
        if deleted:
            out.pop(key, None)
            if key in order:
                order.remove(key)
        else:
            is_new = key not in out
            out[key] = val
            if is_new:
                order.append(key)
        r.pos = body_end + 4  # 跳到 CRC 之后
    return out, order


# ---- Kazumi 模型字段 (来自 .g.dart, 供上层直接取用) ----
# CollectedBangumi adapterTypeId=3: fields 0=bangumiItem 1=time(ms) 2=type
# BangumiItem adapterTypeId=0: 见下
def unwrap(v):
    """typed value → 如果是对象则返回 (adapterTypeId, fields dict)"""
    if isinstance(v, dict) and "fields" in v:
        return v["_adapter"], v["fields"]
    return None, v


def bangumi_item_from_value(v):
    """磁盘上的 BangumiItem (adapter 0) → 友好 dict"""
    if not (isinstance(v, dict) and "fields" in v):
        return None
    aid, f = v["_adapter"], v["fields"]
    if aid != 0:
        return None
    return {
        "id": f.get(0),
        "type": f.get(1),
        "name": f.get(2),
        "nameCn": f.get(3),
        "summary": f.get(4),
        "airDate": f.get(5),
        "airWeekday": f.get(6),
        "rank": f.get(7),
        "images": f.get(8) or {},
        "tags": f.get(9) or [],
        "alias": f.get(10) or [],
        "ratingScore": f.get(11),
        "votes": f.get(12),
        "votesCount": f.get(13) or [],
        "info": f.get(14),
    }


def collectibles_from_box(data: bytes):
    """collectibles.tmp → [ {bangumiItem, collectedAt, collectType} ]"""
    box, _ = read_box(data)
    items = []
    for key, v in box.items():
        if not (isinstance(v, dict) and "fields" in v and v["_adapter"] == 3):
            continue
        f = v["fields"]
        bi = bangumi_item_from_value(f.get(0))
        if bi is None:
            continue
        items.append({
            "key": key,
            "bangumiItem": bi,
            "collectedAt": f.get(1),
            "collectType": f.get(2),  # 1在看 2想看 3搁置 4看过 5抛弃
        })
    return items


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "KAZUMI_COLLECT_TMP", "/opt/kazumi-webdav/data/kazumiSync/collectibles.tmp")
    with open(path, "rb") as fp:
        data = fp.read()
    items = collectibles_from_box(data)
    print(f"收藏条目数: {len(items)}")
    for it in items:
        bi = it["bangumiItem"]
        print(f"- [{it['collectType']}] {bi['id']} {bi.get('nameCn') or bi.get('name')} "
              f"air={bi.get('airDate')} 周{bi.get('airWeekday')} @{it['collectedAt']}")
