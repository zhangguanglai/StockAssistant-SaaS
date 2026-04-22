# -*- coding: utf-8 -*-
"""
db_backup.py - SQLite 数据库自动备份脚本
用途：定时备份 portfolio.db 到 backup 子目录
支持 Railway Volume 环境（通过 VOLUME_PATH 环境变量）
触发方式：
  1. 手动运行：python db_backup.py
  2. Cron 定时：0 */6 * * * python /app/db_backup.py（每6小时）
  3. Railway Cron（如果支持）：同上

备份策略：
  - 保留最近 7 天的每日备份
  - 每次备份生成带时间戳的文件名
  - 自动清理过期备份
"""

import os
import shutil
import sqlite3
from datetime import datetime, timedelta


def get_data_dir():
    """获取数据目录（与 database.py 保持一致）"""
    return os.environ.get("VOLUME_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))


def get_db_path(data_dir):
    """获取数据库文件路径"""
    return os.path.join(data_dir, "portfolio.db")


def get_backup_dir(data_dir):
    """获取备份目录"""
    backup_dir = os.path.join(data_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def backup_database(db_path, backup_dir):
    """
    备份数据库文件
    使用 SQLite API 的 backup() 方法，确保数据一致性（即使有 WAL 文件也能正确备份）
    返回：备份文件路径 或 None（失败时）
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(backup_dir, f"portfolio_{timestamp}.db")

    if not os.path.exists(db_path):
        print(f"[BACKUP] ⚠️ 数据库文件不存在: {db_path}")
        return None

    # 获取文件大小（用于日志）
    size_mb = os.path.getsize(db_path) / (1024 * 1024)

    try:
        # 使用 SQLite backup API 做一致性备份（比 shutil.copy2 更安全）
        source = sqlite3.connect(db_path)
        dest = sqlite3.connect(backup_file)
        source.backup(dest)
        dest.close()
        source.close()
        print(f"[BACKUP] ✅ 备份成功: {backup_file} ({size_mb:.2f} MB)")
        return backup_file
    except Exception as e:
        print(f"[BACKUP] ❌ 备份失败: {e}")
        # 如果 SQLite API 失败，尝试直接拷贝
        try:
            shutil.copy2(db_path, backup_file)
            print(f"[BACKUP] ✅ 回退到文件复制: {backup_file} ({size_mb:.2f} MB)")
            return backup_file
        except Exception as e2:
            print(f"[BACKUP] ❌ 文件复制也失败: {e2}")
            return None


def clean_old_backups(backup_dir, keep_days=7):
    """
    清理超过 keep_days 天的旧备份
    只清理 .db 文件，保留目录结构
    """
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0

    for filename in os.listdir(backup_dir):
        if not filename.endswith(".db"):
            continue

        filepath = os.path.join(backup_dir, filename)
        # 从文件名解析日期：portfolio_YYYYMMDD_HHMMSS.db
        try:
            # 尝试从文件名提取时间戳
            date_str = filename.replace("portfolio_", "").replace(".db", "")
            file_time = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
            if file_time < cutoff:
                os.remove(filepath)
                removed += 1
        except ValueError:
            # 文件名格式不匹配，跳过
            continue

    if removed > 0:
        print(f"[BACKUP] 🗑️ 已清理 {removed} 个过期备份（保留最近 {keep_days} 天）")


def main():
    """主函数"""
    data_dir = get_data_dir()
    db_path = get_db_path(data_dir)
    backup_dir = get_backup_dir(data_dir)

    print(f"[BACKUP] 📦 开始备份数据库...")
    print(f"[BACKUP]    数据目录: {data_dir}")
    print(f"[BACKUP]    数据库:   {db_path}")

    # 执行备份
    result = backup_database(db_path, backup_dir)

    if result:
        # 清理旧备份
        clean_old_backups(backup_dir, keep_days=7)

        # 显示当前备份列表
        backups = sorted([f for f in os.listdir(backup_dir) if f.endswith(".db")])
        total_size = sum(
            os.path.getsize(os.path.join(backup_dir, f)) for f in backups
        ) / (1024 * 1024)
        print(f"[BACKUP] 当前备份数: {len(backups)}, 总占用: {total_size:.2f} MB")
    else:
        print("[BACKUP] 备份未完成，请检查错误信息")


if __name__ == "__main__":
    main()
