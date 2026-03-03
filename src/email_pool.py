"""
邮箱池管理模块。

该模块提供了邮箱池的管理功能，包括邮箱的加载、保存、导入、状态更新和统计等。
支持多种邮箱格式（IMAP模式、心蓝模式）以及不同分隔符的数据解析。

文件格式:
    每行一个邮箱，格式为: email----password----auth_code----status
    - email: 邮箱地址
    - password: 邮箱密码
    - auth_code: 授权码（IMAP模式）或URL（心蓝模式）
    - status: 邮箱状态（new/success/registered/processing/failed/disabled等）
"""

import os
import random
import threading
import shutil
import tempfile
from typing import Dict, Optional, List, Callable, Final


# =============================================================================
# 常量和配置
# =============================================================================

# 字段分隔符
FIELD_SEPARATOR: Final[str] = "----"

# 心蓝模式默认密码
XINLAN_DEFAULT_PASSWORD: Final[str] = "Juan123123."

# 邮箱状态定义
class EmailStatus:
    """邮箱状态常量定义。"""
    NEW = "new"           # 新建/未使用
    SUCCESS = "success"   # 成功
    REGISTERED = "registered"  # 已注册
    SUBMITTED = "submitted"    # 已提交
    PROCESSING = "processing"  # 处理中
    FAILED = "failed"     # 失败
    STOPPED = "stopped"   # 已停止
    DISABLED = "disabled" # 已禁用
    INVALID = "invalid"   # 无效
    BANNED = "banned"     # 已封禁
    USED = "used"         # 已使用
    PROBLEM = "problem"   # 问题邮箱（验证码获取失败等）

# 无效状态集合（邮箱不可用于注册）
INVALID_STATUSES: Final[set] = {EmailStatus.DISABLED, EmailStatus.INVALID, EmailStatus.BANNED, EmailStatus.PROBLEM}

# 已完成状态集合（邮箱已成功完成注册流程）
COMPLETED_STATUSES: Final[set] = {EmailStatus.SUCCESS, EmailStatus.REGISTERED, EmailStatus.SUBMITTED}

# 导入格式类型
class ImportFormat:
    """导入格式类型。"""
    IMAP_3COL = "imap_3col"       # Email----Password----AuthCode
    XINLAN_2COL = "xinlan_2col"   # Email----URL
    XINLAN_3COL = "xinlan_3col"   # Email----Password----URL
    LEGACY = "legacy"             # 空格/制表符分隔


class EmailPool:
    """
    邮箱池管理类。
    
    管理邮箱列表的加载、保存、状态更新和统计功能。
    支持线程安全的并发访问，并提供变更通知机制。
    
    Attributes:
        pool_file: 邮箱池文件路径
        emails: 邮箱列表，每个邮箱是一个字典
        _listeners: 状态变更监听器列表
        _lock: 线程锁，用于并发控制
    """

    def __init__(self, pool_file: str) -> None:
        """
        初始化邮箱池。
        
        Args:
            pool_file: 邮箱池文件的路径
        """
        self.pool_file: str = pool_file
        self.emails: List[Dict[str, str]] = []
        self._listeners: List[Callable[[], None]] = []
        self._lock: threading.RLock = threading.RLock()
        self._load_pool()

    def add_listener(self, callback: Callable[[], None]) -> None:
        """
        注册状态变更回调函数。
        
        Args:
            callback: 状态变更时调用的回调函数，无参数
        """
        with self._lock:
            self._listeners.append(callback)

    def _notify_listeners(self) -> None:
        """
        通知所有监听器状态已变更。
        
        在锁外调用回调函数，避免死锁。
        """
        with self._lock:
            listeners = list(self._listeners)
        
        for callback in listeners:
            try:
                callback()
            except Exception as e:
                print(f"Error in listener callback: {e}")

    def _load_pool(self) -> None:
        """
        从文件加载邮箱池。
        
        解析文件中的每行数据，提取邮箱、密码、授权码和状态。
        支持以 # 开头的注释行和空行。
        
        状态规范化:
            - 空状态或 'new' 统一为 'new'
            - 其他状态转换为小写
        """
        if not os.path.exists(self.pool_file):
            return

        with self._lock:
            lines: List[str] = []
            try:
                with open(self.pool_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except (IOError, OSError, UnicodeDecodeError) as e:
                print(f"Error reading pool file: {e}")
                lines = []

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split(FIELD_SEPARATOR)
                email = parts[0].strip()
                password = parts[1].strip() if len(parts) > 1 else ""
                auth_code = parts[2].strip() if len(parts) > 2 else password
                raw_status = parts[3].strip() if len(parts) > 3 else EmailStatus.NEW
                
                # Load failure reason and time if available
                failure_reason = parts[4].strip() if len(parts) > 4 else ""
                failure_time = parts[5].strip() if len(parts) > 5 else ""

                # 规范化状态
                status = self._normalize_status(raw_status)

                self.emails.append({
                    'email': email,
                    'password': password,
                    'auth_code': auth_code, 
                    'status': status,
                    'failure_reason': failure_reason,
                    'failure_time': failure_time
                })

    def _normalize_status(self, status: str) -> str:
        """
        规范化邮箱状态。
        
        Args:
            status: 原始状态字符串
            
        Returns:
            规范化后的状态字符串
        """
        status = status.lower()
        if not status or status == EmailStatus.NEW:
            return EmailStatus.NEW
        return status

    def _save_pool(self) -> None:
        """
        保存邮箱池到文件。
        
        使用原子写入策略：先写入临时文件，再重命名为目标文件，
        确保即使在写入过程中发生错误也不会损坏原文件。
        """
        temp_path: Optional[str] = None
        try:
            with self._lock:
                # 在同一目录创建临时文件，确保原子重命名跨文件系统工作
                dir_name = os.path.dirname(self.pool_file) or '.'
                with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding='utf-8') as tf:
                    temp_path = tf.name
                    for item in self.emails:
                        email = item['email']
                        password = item['password']
                        auth_code = item['auth_code']
                        status = item.get('status', EmailStatus.NEW)
                        failure_reason = item.get('failure_reason', '')
                        failure_time = item.get('failure_time', '')
                        
                        line = f"{email}{FIELD_SEPARATOR}{password}{FIELD_SEPARATOR}{auth_code}{FIELD_SEPARATOR}{status}"
                        if failure_reason or failure_time:
                            line += f"{FIELD_SEPARATOR}{failure_reason}{FIELD_SEPARATOR}{failure_time}"
                        tf.write(line + "\n")
                
                # 重命名临时文件到实际文件
                shutil.move(temp_path, self.pool_file)

        except (IOError, OSError) as e:
            print(f"Failed to save pool: {e}")
        finally:
            # 清理临时文件
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except (IOError, OSError):
                    pass

    def is_email_valid(self, email: str) -> bool:
        """
        检查邮箱是否可用于注册。
        
        邮箱无效的情况包括：
        - 邮箱不存在于池中
        - 邮箱状态为 disabled/invalid/banned
        
        Args:
            email: 要检查的邮箱地址
            
        Returns:
            如果邮箱有效返回 True，否则返回 False
        """
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    status = item.get('status', EmailStatus.NEW).lower()
                    # 禁用状态视为无效
                    if status in INVALID_STATUSES:
                        return False
                    return True
        return False  # 邮箱不存在也视为无效

    def update_email_status(self, email: str, status: str, reason: str = "") -> None:
        """
        更新邮箱状态并保存。
        
        Args:
            email: 要更新的邮箱地址
            status: 新状态
            reason: 失败原因 (可选)
        """
        dirty = False
        import time
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    if item.get('status') != status or (reason and item.get('failure_reason') != reason):
                        item['status'] = status
                        if reason:
                            item['failure_reason'] = reason
                            item['failure_time'] = time.strftime('%Y-%m-%d %H:%M:%S')
                        elif status == EmailStatus.NEW:
                             # Reset failure info if setting to NEW
                             item['failure_reason'] = ""
                             item['failure_time'] = ""
                        dirty = True
                    break
            if dirty:
                self._save_pool()
        
        # 在锁外通知监听器
        if dirty:
            self._notify_listeners()

    def check_email_availability(self, email: str) -> tuple[bool, str]:
        """
        检查邮箱是否可用于注册。
        
        可用性判断：
        - success/registered/submitted: 已完成，不可用
        - disabled/invalid/banned/problem: 已禁用/问题邮箱，不可用
        - processing: 正在处理中，不可用
        - new/failed/stopped: 可用
        
        Args:
            email: 要检查的邮箱地址
            
        Returns:
            (是否可用, 状态说明)
        """
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    status = item.get('status', EmailStatus.NEW)
                    
                    if status in COMPLETED_STATUSES:
                         return False, f"状态为 {status}"
                    if status in INVALID_STATUSES:
                         return False, "该邮箱已被禁用"
                    if status == EmailStatus.PROCESSING:
                         return False, "正在处理中"
                    # failed/stopped 状态允许重试
                    return True, "可用"
            return False, "未找到"

    def is_email_registered(self, email: str) -> bool:
        """
        检查邮箱是否已注册（兼容旧版）。
        
        Args:
            email: 要检查的邮箱地址
            
        Returns:
            如果邮箱已注册返回 True
        """
        avail, _ = self.check_email_availability(email)
        return not avail

    def get_stats(self, mode_filter: Optional[str] = None) -> Dict[str, int]:
        """
        获取邮箱池统计信息。
        
        Args:
            mode_filter: 模式过滤器
                - "心蓝模式": 只统计 auth_code 以 http 开头的邮箱
                - "IMAP模式": 只统计 auth_code 不以 http 开头的邮箱
                - None: 统计所有邮箱
                
        Returns:
            包含 total_emails, used_emails, problem_emails 的字典
        """
        with self._lock:
            # 根据模式过滤邮箱
            if mode_filter == "心蓝模式":
                # 心蓝模式: auth_code 以 http 开头
                target_emails = [e for e in self.emails if e.get('auth_code', '').startswith('http')]
            elif mode_filter == "IMAP模式":
                # IMAP模式: auth_code 不以 http 开头
                target_emails = [e for e in self.emails if not e.get('auth_code', '').startswith('http')]
            else:
                target_emails = self.emails

            total = len(target_emails)
            # 已使用 = success/registered/submitted/used
            completed_statuses = COMPLETED_STATUSES | {EmailStatus.USED}
            used = sum(1 for e in target_emails if e.get('status', EmailStatus.NEW) in completed_statuses)
            # 问题邮箱
            problem = sum(1 for e in target_emails if e.get('status', EmailStatus.NEW) == EmailStatus.PROBLEM)
            
        return {
            'total_emails': total,
            'used_emails': used,
            'problem_emails': problem
        }

    def delete_email(self, email: str) -> bool:
        """
        从池中删除邮箱。
        
        Args:
            email: 要删除的邮箱地址
            
        Returns:
            如果删除成功返回 True
        """
        deleted = False
        with self._lock:
            initial_len = len(self.emails)
            self.emails = [e for e in self.emails if e['email'] != email]
            if len(self.emails) < initial_len:
                deleted = True
                self._save_pool()
        
        if deleted:
            self._notify_listeners()
        return deleted

    def batch_delete_emails(self, emails: List[str]) -> Dict[str, any]:
        """
        批量删除邮箱。
        
        Args:
            emails: 要删除的邮箱地址列表
            
        Returns:
            包含删除结果的字典:
            {
                'success': bool,  # 是否全部成功
                'deleted_count': int,  # 成功删除的数量
                'failed': List[tuple],  # 失败的邮箱和原因 [(email, reason), ...]
            }
        """
        result = {
            'success': True,
            'deleted_count': 0,
            'failed': []
        }
        
        deleted_any = False
        
        with self._lock:
            for email in emails:
                try:
                    initial_len = len(self.emails)
                    self.emails = [e for e in self.emails if e['email'] != email]
                    if len(self.emails) < initial_len:
                        result['deleted_count'] += 1
                        deleted_any = True
                    else:
                        result['failed'].append((email, "邮箱不存在"))
                        result['success'] = False
                except Exception as e:
                    result['failed'].append((email, str(e)))
                    result['success'] = False
            
            if deleted_any:
                self._save_pool()
        
        if deleted_any:
            self._notify_listeners()
        
        return result

    def update_status(self, email: str, status: str, reason: str = "") -> bool:
        """
        更新邮箱状态（update_email_status 的别名）。
        
        Args:
            email: 要更新的邮箱地址
            status: 新状态
            reason: 失败原因 (可选)
            
        Returns:
            如果状态发生变化返回 True
        """
        updated = False
        import time
        with self._lock:
            for e in self.emails:
                if e['email'] == email:
                    if e.get('status') != status or (reason and e.get('failure_reason') != reason):
                        e['status'] = status
                        if reason:
                            e['failure_reason'] = reason
                            e['failure_time'] = time.strftime('%Y-%m-%d %H:%M:%S')
                        elif status == EmailStatus.NEW:
                             e['failure_reason'] = ""
                             e['failure_time'] = ""
                        updated = True
                    break
            if updated:
                self._save_pool()
        
        if updated:
            self._notify_listeners()
        return updated

    def get_email_config(self, email: str) -> Optional[Dict[str, str]]:
        """
        获取邮箱配置信息。
        
        Args:
            email: 邮箱地址
            
        Returns:
            邮箱配置字典，不存在则返回 None
        """
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    return item
        return None

    def _parse_email_line(self, line: str) -> Optional[Dict[str, str]]:
        """
        解析单行邮箱数据。
        
        支持的格式:
        1. IMAP (3-col): Email----Password----AuthCode
        2. XinLan (2-col): Email----URL (Password 默认为 XINLAN_DEFAULT_PASSWORD)
        3. XinLan (3-col): Email----Password----URL
        4. Legacy: Email Password AuthCode (空格/制表符分隔)
        
        Args:
            line: 要解析的行数据
            
        Returns:
            解析后的邮箱字典，解析失败返回 None
        """
        # 初始化默认值
        email = ""
        password = ""
        auth_code = ""

        # 优先尝试 ---- 分隔符
        if FIELD_SEPARATOR in line:
            parts = line.split(FIELD_SEPARATOR)
            email = parts[0].strip()
            
            if len(parts) >= 2:
                part2 = parts[1].strip()
                # 判断是心蓝2列格式 (Email----URL) 还是标准格式
                if part2.startswith('http'):
                    # 心蓝2列格式
                    password = XINLAN_DEFAULT_PASSWORD
                    auth_code = part2
                else:
                    # 标准IMAP或心蓝3列格式
                    password = part2
                    if len(parts) >= 3:
                        part3 = parts[2].strip()
                        # part3 在心蓝模式是URL，在IMAP模式是授权码
                        # 下游逻辑会根据是否以 http 开头判断模式
                        auth_code = part3
                    else:
                        # IMAP 2列格式: auth_code 默认为 password
                        auth_code = password
        else:
            # 尝试空格/制表符分隔（旧版格式）
            parts = line.replace('\t', ' ').split()
            if not parts:
                return None
            email = parts[0].strip()
            password = parts[1].strip() if len(parts) > 1 else ""
            auth_code = parts[2].strip() if len(parts) > 2 else password

        if not email:
            return None

        return {
            'email': email,
            'password': password,
            'auth_code': auth_code,
            'status': EmailStatus.NEW
        }

    def import_emails(self, content: str, overwrite: bool = True) -> int:
        """
        从字符串内容导入邮箱。
        
        支持的格式:
        1. IMAP (3-col): Email----Password----AuthCode
        2. XinLan (2-col): Email----URL (Password 默认为 'Juan123123.')
        3. XinLan (3-col): Email----Password----URL
        4. Legacy: Email Password AuthCode (空格/制表符分隔)
        
        Args:
            content: 包含邮箱数据的字符串
            overwrite: 如果为 True，更新现有邮箱时将重置状态为 new
            
        Returns:
            成功导入的邮箱数量
        """
        count = 0
        lines = content.splitlines()
        
        with self._lock:
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # 解析邮箱数据
                parsed = self._parse_email_line(line)
                if not parsed:
                    continue

                email = parsed['email']
                password = parsed['password']
                auth_code = parsed['auth_code']

                # 更新或插入
                found = False
                for item in self.emails:
                    if item['email'] == email:
                        item['password'] = password
                        item['auth_code'] = auth_code
                        if overwrite:
                            item['status'] = EmailStatus.NEW
                        found = True
                        break
                
                if not found:
                    self.emails.append({
                        'email': email,
                        'password': password,
                        'auth_code': auth_code,
                        'status': EmailStatus.NEW
                    })
                count += 1
            
            if count > 0:
                self._save_pool()
        
        # 在锁外通知监听器
        if count > 0:
            self._notify_listeners()
            
        return count

    def clear_emails(self) -> None:
        """清空邮箱池中的所有邮箱。"""
        with self._lock:
            self.emails = []
            self._save_pool()
        self._notify_listeners()

    def get_all_rows(self) -> List[Dict[str, str]]:
        """
        获取所有邮箱数据。
        
        Returns:
            邮箱字典列表的副本
        """
        with self._lock:
            return list(self.emails)
