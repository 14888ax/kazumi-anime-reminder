#!/usr/bin/env python3
"""通过 Gmail API (OAuth2) 发送提醒邮件。

凭据从 mail_config.json 读取（已被 .gitignore 排除，不会上传仓库）：
{
  "mode": "gmail",
  "to": "填写收件邮箱",
  "gmail_user": "填写发件Gmail邮箱",
  "gmail_token_file": "填写Gmail OAuth token文件路径",
  "gmail_client_file": "填写OAuth client secret文件路径"
}

用法: send_mail.py "标题" "正文"
"""
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "mail_config.json")


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as fp:
        return json.load(fp)


def get_access_token(cfg):
    """用 refresh_token 换新的 access_token"""
    tok = json.load(open(cfg["gmail_token_file"]))
    d = json.load(open(cfg["gmail_client_file"]))["installed"]
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": d["client_id"],
        "client_secret": d["client_secret"],
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return resp["access_token"]


def send(subject, body):
    cfg = load_config()
    access = get_access_token(cfg)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("番剧更新提醒", "utf-8")), cfg["gmail_user"]))
    msg["To"] = cfg["to"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    req = urllib.request.Request(
        url,
        data=json.dumps({"raw": raw}).encode(),
        headers={
            "Authorization": "Bearer " + access,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print(f"邮件已发送 → {cfg['to']}: {subject} (id={resp.get('id')})")


if __name__ == "__main__":
    subject = sys.argv[1] if len(sys.argv) > 1 else "测试邮件"
    body = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
    send(subject, body)
