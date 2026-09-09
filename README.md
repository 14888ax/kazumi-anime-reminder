# Kazumi 番剧更新提醒 (Anime Update Reminder)

解析 Kazumi 的 WebDAV 同步数据（收藏列表），配合 [Bangumi](https://bangumi.tv) 放送信息，
在收藏番剧**出新一话**时自动发邮件提醒。

## 功能

- 📦 **收藏来源**: 读取 Kazumi App WebDAV 同步的收藏盒子 `collectibles.tmp`
  （Hive 二进制格式），提取收藏的番剧（Bangumi subject ID + 标题 + 收藏类型）
- 📡 **更新判断**: 调用 Bangumi 公开 API `next.bgm.tv/p1/subjects/{id}/episodes`
  获取每话的放送日期，出现新的已放送剧集即为"更新"
- 📧 **邮件提醒**: 检测到更新后通过 Gmail API (OAuth2) 发送提醒邮件
- 🔇 **静默设计**: 无更新时不发任何通知；同一天同一部番只提醒一次
- ⏰ 可配合 cron / 任务调度器每日定时运行

## 数据流

```
Kazumi App --WebDAV--> collectibles.tmp ─┐
                                          ├─> 提取收藏番剧列表 ─┐
Bangumi 公开 API <─────────────────────── 剧集放送表            │
                                                               v
                                                   检查是否有新话放送 ──> 发邮件
```

## 文件结构

| 文件 | 说明 |
| --- | --- |
| `hive_reader.py` | Kazumi WebDAV Hive 盒子二进制解析器（只读） |
| `check_updates.py` | 主检查脚本：比对放送表，输出/发送更新提醒 |
| `send_mail.py` | Gmail API (OAuth2) 发信模块 |
| `mail_config.json` | 发信配置（**本地文件，不入库**，见下） |
| `state.json` | 运行状态：每部番上次已知的放送进度（本地生成，不入库） |

## 使用方法

### 1. 准备收藏数据

Kazumi 开启 WebDAV 同步后，同步目录里会有 `kazumiSync/collectibles.tmp`。
通过环境变量告诉脚本数据位置（不设置则用默认路径）：

```bash
export KAZUMI_COLLECT_TMP=/path/to/kazumiSync/collectibles.tmp
export KAZUMI_SNAPSHOT=/path/to/kazumiSync/history/snapshot.json   # 可选
```

> 也可直接修改 `check_updates.py` 顶部的默认路径常量。

### 2. 配置发信邮箱

创建本地 `mail_config.json`（已被 `.gitignore` 排除，不会提交到仓库）：

```json
{
  "mode": "gmail",
  "to": "recipient@example.com",
  "gmail_user": "sender@gmail.com",
  "gmail_token_file": "/path/to/gmail_oauth_token.json",
  "gmail_client_file": "/path/to/client_secret.json"
}
```

`gmail_oauth_token.json` 需包含 Gmail API 的 OAuth2 凭据（`refresh_token`），
scope 需含 `https://www.googleapis.com/auth/gmail.modify`（可发送）。

### 3. 运行

```bash
# 调试：打印当前收藏与放送状态
python3 check_updates.py --dump

# 正式检查：有新更新则打印提醒文本
python3 check_updates.py

# 检查并发送邮件
python3 check_updates.py --email
```

### 4. 定时运行（示例）

每天 10:00 检查一次（cron 表达式，Asia/Shanghai）：

```cron
0 10 * * * cd /path/to/project && python3 check_updates.py --email
```

## 技术细节

- `collectibles.tmp` 是 Hive 数据库盒子文件的直接备份：
  - 帧格式 `[4B 长度][key][value][4B CRC]`，key 类型 0=uint32 / 1=string
  - 自定义类型 ID 在磁盘上 = adapter typeId + 32
  - 收藏条目 `CollectedBangumi`: 字段 0=BangumiItem、1=收藏时间、2=收藏类型
    （1 在看 / 2 想看 / 3 搁置 / 4 看过 / 5 抛弃），默认只提醒"在看/想看"
- Bangumi 剧集接口只精确到"放送日期"，故提醒粒度为天
- 跨季条目（如部分长篇）的 sort 号可能带偏移，话数按"已放送条数"计算

## 声明

- 本项目与 Kazumi、Bangumi 官方无关联，纯个人工具
- 数据来自 Bangumi 公开 API 与用户自己的 WebDAV 备份文件
