#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮箱池 OAuth 体检工具（只读，默认不改数据）

用途
----
批量验证邮箱池里 Outlook OAuth 号的 refresh_token 是否还有效。

背景
----
微软对批量注册的邮箱常返回 AADSTS70000
(Account security interrupt ... account is found as compromised)，
这类号接码必然失败，但池子里可能仍标着 new，导致注册时反复白跑：
开窗口 + 填表 + 过滑块(23~35 秒) + 占 IP 名额。

用法
----
  # 只体检 new 号（默认，只读，不改池子）
  ./venv/bin/python tools/oauth_pool_check.py

  # 体检全部状态的号
  ./venv/bin/python tools/oauth_pool_check.py --status all

  # 只体检指定状态
  ./venv/bin/python tools/oauth_pool_check.py --status new,problem

  # 体检后把坏号状态改为 problem（会先自动备份 email_pool.csv）
  ./venv/bin/python tools/oauth_pool_check.py --apply

输出
----
  logs/oauth_check_<时间戳>.csv        好/坏全量清单
  logs/oauth_check_<时间戳>_bad.txt    仅坏号，一行一个

注意
----
- 默认只读，不动 config/email_pool.csv。
- 加 --apply 才会改状态，且改前自动备份到 backups/。
- OAuth 串在 auth_code 字段（格式 client_id|||refresh_token），
  不在 password 字段，勿搞混。
"""

import argparse
import csv
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.register_kling_bitbrowser import (  # noqa: E402
    get_imap_credential,
    precheck_outlook_oauth,
)

POOL_PATH = os.path.join(ROOT, "config", "email_pool.csv")
BACKUP_DIR = os.path.join(ROOT, "backups")
LOG_DIR = os.path.join(ROOT, "logs")
SEP = "----"


def load_targets(statuses):
    """读取邮箱池，筛出需要体检的记录。返回 [(email, cred, raw_line, idx)]"""
    targets = []
    with open(POOL_PATH, encoding="utf-8-sig") as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]

    for idx, line in enumerate(lines):
        parts = line.split(SEP)
        if len(parts) < 4:
            continue
        email, pwd, code, st = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
            parts[3].strip(),
        )
        if statuses and st not in statuses:
            continue
        row = {"email": email, "password": pwd, "auth_code": code}
        cred = get_imap_credential(row)
        if "|||" not in cred:
            continue  # 非 OAuth 号，无需体检
        targets.append((email, cred, idx))
    return targets, lines


def main():
    ap = argparse.ArgumentParser(description="邮箱池 OAuth 体检（默认只读）")
    ap.add_argument(
        "--status",
        default="new",
        help="要体检的状态，逗号分隔；all 表示全部（默认 new）",
    )
    ap.add_argument("--workers", type=int, default=10, help="并发数（默认 10）")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="把坏号状态改为 problem（改前自动备份）",
    )
    args = ap.parse_args()

    statuses = None if args.status.strip().lower() == "all" else set(
        s.strip() for s in args.status.split(",") if s.strip()
    )

    targets, lines = load_targets(statuses)
    if not targets:
        print("没有符合条件的 OAuth 邮箱需要体检。")
        return 0

    print(f"邮箱池: {POOL_PATH}")
    print(f"筛选状态: {'全部' if statuses is None else ','.join(sorted(statuses))}")
    print(f"待体检: {len(targets)} 个，并发 {args.workers}")
    print("开始体检...")
    t0 = time.time()

    def check(item):
        email, cred, _ = item
        try:
            return email, precheck_outlook_oauth(email, cred, logger=None)
        except Exception:
            return email, None

    good, bad = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (email, ok) in enumerate(ex.map(check, targets), 1):
            (good if ok is True else bad).append(email)
            if i % 50 == 0:
                print(f"  进度 {i}/{len(targets)}  好={len(good)} 坏={len(bad)}")

    dt = time.time() - t0
    total = len(targets)
    print("")
    print("=" * 52)
    print(f"体检完成，耗时 {dt:.0f} 秒")
    print(f"  ✅ 可用:  {len(good)}")
    print(f"  🚫 已废:  {len(bad)}")
    if total:
        print(f"  坏号占比: {len(bad) / total * 100:.1f}%")
    print("=" * 52)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(LOG_DIR, exist_ok=True)
    csv_path = os.path.join(LOG_DIR, f"oauth_check_{ts}.csv")
    bad_path = os.path.join(LOG_DIR, f"oauth_check_{ts}_bad.txt")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["email", "体检结果"])
        for e in good:
            w.writerow([e, "可用"])
        for e in bad:
            w.writerow([e, "已废(token失效)"])

    with open(bad_path, "w", encoding="utf-8") as f:
        f.write("\n".join(bad))

    print("")
    print("清单已写出:")
    print(f"  {os.path.relpath(csv_path, ROOT)}")
    print(f"  {os.path.relpath(bad_path, ROOT)}")

    if args.apply and bad:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = os.path.join(BACKUP_DIR, f"email_pool_{stamp}.bak")
        shutil.copy2(POOL_PATH, bak)
        print("")
        print(f"已备份原文件: {os.path.relpath(bak, ROOT)}")

        bad_set = set(bad)
        changed = 0
        new_lines = []
        for line in lines:
            parts = line.split(SEP)
            if len(parts) >= 4 and parts[0].strip() in bad_set:
                parts[3] = "problem"
                if len(parts) < 5:
                    parts.append("OAuth体检: 微软判定token失效(compromised)")
                    parts.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                new_lines.append(SEP.join(parts))
                changed += 1
            else:
                new_lines.append(line)

        with open(POOL_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")

        print(f"已把 {changed} 个坏号状态改为 problem，后续注册会自动跳过。")
        print("如需还原，用上面那个 .bak 备份文件覆盖回去即可。")

    elif bad:
        print("")
        print("提示: 加 --apply 可在备份后自动把坏号标记为 problem。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
