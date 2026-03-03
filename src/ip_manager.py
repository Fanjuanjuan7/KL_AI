"""
IP代理池管理模块。

该模块提供了IP代理池的管理功能，包括IP的导入、分配、释放和统计等。
支持基于使用次数的轮询分配策略，以及线程安全的并发访问控制。

数据结构:
    IP配置存储在JSON文件中，包含以下字段:
    - host: IP地址
    - port: 端口号
    - proxyUserName: 代理用户名（可选）
    - proxyPassword: 代理密码（可选）
    - protocol: 代理协议（默认socks5）
    - used_by: 已使用此IP的邮箱列表
    - usage_count: 使用计数（应与len(used_by)一致）
    - last_updated: 最后更新时间戳
"""

import json
import os
import shutil
import threading
import re
import time
from typing import List, Dict, Optional, Tuple, Any, Set, Callable, Final


# =============================================================================
# 常量和配置
# =============================================================================

# 默认配置值
DEFAULT_MAX_USAGE_PER_IP: Final[int] = 5
DEFAULT_PROTOCOL: Final[str] = "socks5"

# 分配结果状态码
class AllocationStatus:
    """IP分配结果状态码。"""
    SUCCESS = "success"       # 分配成功
    EMAIL_USED = "email_used" # 邮箱已被使用
    IP_EXHAUSTED = "ip_exhausted"  # IP已耗尽
    IP_BUSY = "ip_busy"       # IP繁忙（并发限制）

# IP配置字段名
class IPField:
    """IP配置字典的字段名常量。"""
    HOST = "host"
    PORT = "port"
    USERNAME = "proxyUserName"
    PASSWORD = "proxyPassword"
    PROTOCOL = "protocol"
    USED_BY = "used_by"
    USAGE_COUNT = "usage_count"
    LAST_UPDATED = "last_updated"

# 数据文件字段名
class DataField:
    """数据文件顶层字段名常量。"""
    MAX_USAGE = "max_usage_per_ip"
    IPS = "ips"
    CURRENT_INDEX = "current_ip_index"


class IPManager:
    """
    IP代理池管理类。
    
    管理IP代理列表的加载、保存、分配和释放功能。
    支持基于使用次数的轮询分配策略，确保负载均衡。
    支持线程安全的并发访问，并提供变更通知机制。
    
    Attributes:
        config_path: IP配置文件路径
        lock: 线程锁，用于并发控制
        active_ips: 当前正在被任务使用的IP集合
        on_status_change: 状态变更回调函数
        logger: 日志回调函数
        data: IP池数据字典
    """

    def __init__(self, config_path: str = "config/ip_pool.json") -> None:
        """
        初始化IP管理器。
        
        Args:
            config_path: IP配置文件路径，默认为 "config/ip_pool.json"
        """
        # 确保目录存在
        config_dir = os.path.dirname(os.path.abspath(config_path))
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir)
            
        self.config_path: str = os.path.abspath(config_path)
        self.lock: threading.RLock = threading.RLock()
        self.active_ips: Set[Tuple[str, str]] = set()  # Track IPs currently being used by running tasks
        self.on_status_change: Optional[Callable[[], None]] = None
        self.logger: Optional[Callable[[str], None]] = None
        self.data: Dict[str, Any] = {
            DataField.MAX_USAGE: DEFAULT_MAX_USAGE_PER_IP,
            DataField.IPS: [],
            "used_emails": []
        }
        self.last_import_duplicates: Set[Tuple[str, str]] = set()
        self._load_config()
        self.verify_consistency()

    def set_logger(self, logger: Callable[[str], None]) -> None:
        """
        设置日志回调函数。
        
        Args:
            logger: 日志回调函数，接收一个字符串参数
        """
        self.logger = logger

    def _log(self, msg: str) -> None:
        """
        记录日志。
        
        Args:
            msg: 日志消息
        """
        if self.logger:
            try:
                self.logger(f"[IPManager] {msg}")
            except Exception:
                pass

    def _is_valid_ip_entry(self, ip: Dict[str, Any]) -> bool:
        """
        验证IP条目是否有效。
        
        检查必需的字段是否存在。
        
        Args:
            ip: IP配置字典
            
        Returns:
            如果条目有效返回 True
        """
        return (
            isinstance(ip, dict) and
            IPField.HOST in ip and
            IPField.PORT in ip
        )

    def _get_ip_key(self, host: str, port: Any) -> Tuple[str, str]:
        """
        生成IP的唯一标识键。
        
        Args:
            host: IP地址
            port: 端口号
            
        Returns:
            (host, port_str) 元组
        """
        return (host, str(port))

    def _find_ip_index(self, host: str, port: Any) -> Optional[int]:
        """
        查找IP在列表中的索引。
        
        Args:
            host: IP地址
            port: 端口号
            
        Returns:
            索引位置，未找到返回 None
        """
        for idx, ip in enumerate(self.data[DataField.IPS]):
            if (ip.get(IPField.HOST) == host and 
                str(ip.get(IPField.PORT)) == str(port)):
                return idx
        return None

    def verify_consistency(self) -> None:
        """
        验证并修复数据一致性。
        
        检查 usage_count 和 used_by 列表长度是否一致，
        如果不一致则进行修复。
        """
        with self.lock:
            fixed_count = 0
            for ip in self.data[DataField.IPS]:
                # 确保 used_by 是列表
                if not isinstance(ip.get(IPField.USED_BY), list):
                    ip[IPField.USED_BY] = []
                
                real_count = len(ip[IPField.USED_BY])
                if ip.get(IPField.USAGE_COUNT, 0) != real_count:
                    ip[IPField.USAGE_COUNT] = real_count
                    fixed_count += 1
            
            if fixed_count > 0:
                self._log(f"Consistency check: Fixed usage counts for {fixed_count} IPs.")
                self._save_config()

    def validate_state(self) -> bool:
        """
        验证内部状态并与磁盘同步。
        
        Returns:
            如果状态一致返回 True
        """
        with self.lock:
            # 1. 内部一致性检查
            for ip in self.data[DataField.IPS]:
                if len(ip.get(IPField.USED_BY, [])) != ip.get(IPField.USAGE_COUNT, 0):
                    host = ip.get(IPField.HOST, "unknown")
                    port = ip.get(IPField.PORT, "unknown")
                    self._log(f"Inconsistency found for IP {host}:{port}")
                    return False
            
            # 2. 磁盘同步检查
            try:
                if not os.path.exists(self.config_path):
                    return False
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    disk_data = json.load(f)
                
                # 检查重要字段是否匹配
                if len(disk_data.get(DataField.IPS, [])) != len(self.data[DataField.IPS]):
                     self._log("Disk data count mismatch")
                     return False
                     
                return True
            except (IOError, OSError, json.JSONDecodeError) as e:
                self._log(f"Validation error: {e}")
                return False

    def _load_config(self) -> None:
        """
        从文件加载配置。
        
        处理配置迁移：确保所有IP条目都有 used_by 字段。
        如果加载失败，会备份损坏的文件。
        """
        with self.lock:
            if not os.path.exists(self.config_path):
                self._save_config()
                return

            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
                    
                # 迁移：确保所有IP都有 used_by 字段
                changed = False
                for ip in self.data[DataField.IPS]:
                    if IPField.USED_BY not in ip:
                        # 将 usage_count 迁移为 used_by 列表
                        cnt = ip.get(IPField.USAGE_COUNT, 0)
                        ip[IPField.USED_BY] = [f"migrated_{i}" for i in range(cnt)]
                        changed = True
                    # 确保 usage_count 一致性
                    ip[IPField.USAGE_COUNT] = len(ip[IPField.USED_BY])
                
                if changed:
                    self._save_config()
                    
            except (IOError, OSError, json.JSONDecodeError) as e:
                print(f"Error loading config: {e}")
                # 备份损坏的文件
                try:
                    shutil.copy(self.config_path, self.config_path + ".corrupted")
                except (IOError, OSError):
                    pass

    def _save_config(self) -> None:
        """
        保存配置到文件。
        
        使用原子写入策略，并在保存后触发状态变更回调。
        """
        with self.lock:
            # 自动备份
            self._create_backup()
            temp_path = self.config_path + ".tmp"
            try:
                # 原子写入：先写入临时文件再重命名
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, indent=2, ensure_ascii=False)
                os.replace(temp_path, self.config_path)
            except (IOError, OSError) as e:
                print(f"Error saving config: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except (IOError, OSError):
                        pass
            
            if self.on_status_change:
                try:
                    self.on_status_change()
                except Exception:
                    pass

    def set_on_status_change_callback(self, callback: Callable[[], None]) -> None:
        """
        设置状态变更回调函数。
        
        Args:
            callback: 状态变更时调用的回调函数
        """
        self.on_status_change = callback

    def _create_backup(self) -> None:
        """
        创建配置备份。
        
        备份存储在 'backups' 目录中，带有时间戳。
        会自动清理旧备份文件。
        """
        if not os.path.exists(self.config_path):
            return
            
        backup_dir = os.path.join(os.path.dirname(os.path.dirname(self.config_path)), "backups")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
            
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(self.config_path)
        name, ext = os.path.splitext(filename)
        backup_path = os.path.join(backup_dir, f"{name}_{timestamp}.bak")
        
        try:
            shutil.copy(self.config_path, backup_path)
            self._cleanup_backups(backup_dir, name)
        except (IOError, OSError) as e:
            print(f"Backup failed: {e}")
            
    def _cleanup_backups(self, backup_dir: str, prefix: str, max_backups: int = 10) -> None:
        """
        清理旧备份文件。
        
        Args:
            backup_dir: 备份目录路径
            prefix: 文件名前缀
            max_backups: 最大保留的备份数量
        """
        try:
            files: List[str] = []
            for f in os.listdir(backup_dir):
                if f.startswith(prefix) and f.endswith(".bak"):
                    files.append(os.path.join(backup_dir, f))
            
            # 按修改时间排序，最早的在前
            files.sort(key=os.path.getmtime)
            
            # 删除超出限制的备份
            while len(files) > max_backups:
                oldest = files.pop(0)
                try:
                    os.remove(oldest)
                except (IOError, OSError):
                    pass
        except (IOError, OSError):
            pass

    def set_max_usage(self, n: int) -> None:
        """
        设置每个IP的最大使用次数。
        
        Args:
            n: 最大使用次数（至少为1）
        """
        with self.lock:
            self.data[DataField.MAX_USAGE] = max(1, int(n))
            self._save_config()

    def get_max_usage(self) -> int:
        """
        获取每个IP的最大使用次数。
        
        Returns:
            最大使用次数
        """
        return self.data.get(DataField.MAX_USAGE, DEFAULT_MAX_USAGE_PER_IP)

    def _parse_ip_line(self, line: str, regex: Optional[str] = None, 
                       replace_str: str = "") -> Optional[List[str]]:
        """
        解析单行IP数据。
        
        支持多种分隔符：冒号、逗号、竖线、制表符。
        
        Args:
            line: 要解析的行数据
            regex: 可选的正则表达式，用于预处理
            replace_str: 正则替换字符串
            
        Returns:
            解析后的字段列表，失败返回 None
        """
        line = line.strip()
        if not line:
            return None
        
        if regex:
            try:
                line = re.sub(regex, replace_str, line)
            except re.error:
                pass
        
        # 将常见分隔符统一为逗号
        cleaned = line.replace(':', ',').replace('|', ',').replace('\t', ',')
        parts = [p.strip() for p in cleaned.split(',') if p.strip()]
        
        return parts if parts else None

    def _is_duplicate_ip(self, host: str, port: str) -> bool:
        """
        检查IP是否已存在。
        
        Args:
            host: IP地址
            port: 端口号
            
        Returns:
            如果已存在返回 True
        """
        for existing in self.data[DataField.IPS]:
            if (existing.get(IPField.HOST) == host and 
                str(existing.get(IPField.PORT)) == str(port)):
                return True
        return False

    def import_ips(self, content: str, regex: Optional[str] = None, 
                   replace_str: str = "", default_port: str = "", 
                   default_user: str = "", default_pass: str = "", 
                   default_protocol: str = DEFAULT_PROTOCOL) -> int:
        """
        从字符串内容导入IP。
        
        支持的格式（每行一个）:
        - host,port,user,pass
        - host:port:user:pass
        - host|port|user|pass
        - host\tport\tuser\tpass
        
        缺失的字段将使用默认值填充。
        
        Args:
            content: 包含IP数据的字符串
            regex: 可选的正则表达式，用于预处理每行数据
            replace_str: 正则替换字符串
            default_port: 默认端口号
            default_user: 默认用户名
            default_pass: 默认密码
            default_protocol: 默认协议
            
        Returns:
            成功导入的IP数量
        """
        with self.lock:
            self.last_import_duplicates = set()
            count = 0
            lines = content.splitlines()
            for line in lines:
                parts = self._parse_ip_line(line, regex, replace_str)
                if not parts:
                    continue
                
                host = parts[0]
                port = parts[1] if len(parts) > 1 else default_port
                user = parts[2] if len(parts) > 2 else default_user
                pwd  = parts[3] if len(parts) > 3 else default_pass
                
                # 跳过重复项
                if self._is_duplicate_ip(host, port):
                    self.last_import_duplicates.add((host, str(port)))
                    continue

                ip_entry: Dict[str, Any] = {
                    IPField.HOST: host,
                    IPField.PORT: port,
                    IPField.USERNAME: user,
                    IPField.PASSWORD: pwd,
                    IPField.PROTOCOL: default_protocol,
                    IPField.USED_BY: [],
                    IPField.USAGE_COUNT: 0
                }
                
                self.data[DataField.IPS].append(ip_entry)
                count += 1
            
            if count > 0:
                # 优先使用新IP：将当前索引设置为新IP的起始位置
                total_ips = len(self.data[DataField.IPS])
                first_new_index = max(0, total_ips - count)
                self.data[DataField.CURRENT_INDEX] = first_new_index
                self._log(f"Imported {count} new IPs. Reset allocation index to {first_new_index}.")
                self._save_config()
            return count

    def get_last_import_duplicates(self) -> Set[Tuple[str, str]]:
        with self.lock:
            return set(self.last_import_duplicates)

    def get_used_emails(self) -> Set[str]:
        """
        获取所有已使用的邮箱集合。
        
        Returns:
            所有IP的 used_by 列表的并集
        """
        with self.lock:
            all_used: Set[str] = set()
            for ip in self.data[DataField.IPS]:
                all_used.update(ip.get(IPField.USED_BY, []))
            return all_used

    def delete_ips(self, pattern: str) -> int:
        """
        删除匹配正则表达式的IP。
        
        Args:
            pattern: 用于匹配host或port的正则表达式
            
        Returns:
            删除的IP数量
        """
        with self.lock:
            initial_count = len(self.data[DataField.IPS])
            try:
                cp = re.compile(pattern)
                self.data[DataField.IPS] = [
                    ip for ip in self.data[DataField.IPS] 
                    if not self._ip_matches_pattern(ip, cp)
                ]
            except re.error:
                return 0
            
            removed = initial_count - len(self.data[DataField.IPS])
            if removed > 0:
                self._save_config()
            return removed

    def batch_delete_ips(self, hosts: List[str]) -> int:
        """
        批量删除指定Host的IP。
        
        Args:
            hosts: 要删除的IP地址列表
            
        Returns:
            成功删除的数量
        """
        if not hosts:
            return 0
            
        hosts_set = set(hosts)
        with self.lock:
            initial_count = len(self.data[DataField.IPS])
            
            self.data[DataField.IPS] = [
                ip for ip in self.data[DataField.IPS]
                if ip.get(IPField.HOST) not in hosts_set
            ]
            
            removed = initial_count - len(self.data[DataField.IPS])
            if removed > 0:
                self._save_config()
            return removed

    def _ip_matches_pattern(self, ip: Dict[str, Any], pattern: re.Pattern) -> bool:
        """
        检查IP是否匹配正则表达式。
        
        Args:
            ip: IP配置字典
            pattern: 编译后的正则表达式
            
        Returns:
            如果匹配返回 True
        """
        host = ip.get(IPField.HOST, "")
        port = str(ip.get(IPField.PORT, ""))
        return bool(pattern.search(host) or pattern.search(port))

    def clear_ips(self) -> None:
        """清空所有IP。"""
        with self.lock:
            self.data[DataField.IPS] = []
            self._save_config()

    def get_all_ips(self) -> List[Dict[str, Any]]:
        """
        获取所有IP配置。
        
        Returns:
            IP配置列表的副本
        """
        with self.lock:
            return list(self.data[DataField.IPS])

    def get_stats(self) -> Dict[str, int]:
        """
        获取IP池统计信息。
        
        Returns:
            包含以下字段的字典:
            - total_ips: 总IP数量
            - available_ips: 可用IP数量（未达到最大使用次数）
            - used_emails_count: 已使用邮箱数量
            - max_usage: 每个IP的最大使用次数
            - remaining_usage_count: 剩余可用次数
        """
        with self.lock:
            total = len(self.data[DataField.IPS])
            max_u = self.data.get(DataField.MAX_USAGE, DEFAULT_MAX_USAGE_PER_IP)
            # 可用：未达到最大使用次数的IP
            available = sum(1 for ip in self.data[DataField.IPS] 
                          if len(ip.get(IPField.USED_BY, [])) < max_u)
            
            # 统计所有已使用邮箱和槽位
            all_used: Set[str] = set()
            total_usage_slots = 0
            used_slots = 0
            
            for ip in self.data[DataField.IPS]:
                all_used.update(ip.get(IPField.USED_BY, []))
                total_usage_slots += max_u
                used_slots += len(ip.get(IPField.USED_BY, []))
            
            remaining_usage = total_usage_slots - used_slots
            
            return {
                "total_ips": total,
                "available_ips": available,
                "used_emails_count": len(all_used),
                "max_usage": max_u,
                "remaining_usage_count": remaining_usage
            }

    def _find_available_ip(self, start_index: int, max_u: int) -> Tuple[Optional[Dict[str, Any]], int]:
        """
        查找可用的IP。
        
        从 start_index 开始轮询查找使用次数未达到 max_u 的IP。
        
        Args:
            start_index: 起始索引
            max_u: 最大使用次数
            
        Returns:
            (找到的IP配置, 找到的索引)，未找到返回 (None, -1)
        """
        ips = self.data[DataField.IPS]
        total_ips = len(ips)
        
        for i in range(total_ips):
            idx = (start_index + i) % total_ips
            ip = ips[idx]
            used_count = len(ip.get(IPField.USED_BY, []))
            
            if used_count < max_u:
                return ip, idx
        
        return None, -1

    def _has_any_capacity(self, max_u: int) -> bool:
        """
        检查是否有任何IP还有剩余容量。
        
        Args:
            max_u: 最大使用次数
            
        Returns:
            如果有任何IP还可以使用返回 True
        """
        for ip in self.data[DataField.IPS]:
            if len(ip.get(IPField.USED_BY, [])) < max_u:
                return True
        return False

    def _is_email_used(self, email: str) -> bool:
        """
        检查邮箱是否已被分配到某个IP。
        
        Args:
            email: 邮箱地址
            
        Returns:
            如果邮箱已被使用返回 True
        """
        for ip in self.data[DataField.IPS]:
            if email in ip.get(IPField.USED_BY, []):
                return True
        return False

    def allocate_ip(self, email: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        为邮箱分配IP。
        
        使用基于使用次数的轮询策略，确保负载均衡。
        首先检查邮箱是否已被分配，然后轮询查找可用IP。
        
        Args:
            email: 要分配IP的邮箱地址
            
        Returns:
            (IP配置, 状态码)。
            状态码可能值:
            - "success": 分配成功
            - "email_used": 邮箱已被使用
            - "ip_exhausted": IP已耗尽
            - "ip_busy": IP繁忙（并发限制，当前未使用）
        """
        with self.lock:
            # 检查邮箱是否已被分配到某个IP
            if self._is_email_used(email):
                return None, AllocationStatus.EMAIL_USED

            max_u = self.data.get(DataField.MAX_USAGE, DEFAULT_MAX_USAGE_PER_IP)
            ips = self.data[DataField.IPS]
            total_ips = len(ips)
            
            if total_ips == 0:
                return None, AllocationStatus.IP_EXHAUSTED

            # 轮询查找可用IP
            start_index = self.data.get(DataField.CURRENT_INDEX, 0)
            candidate, idx = self._find_available_ip(start_index, max_u)
            
            if not candidate:
                # 没有找到可用IP，检查是因为全部繁忙还是全部已满
                if self._has_any_capacity(max_u):
                    # 有容量但当前锁定
                    return None, AllocationStatus.IP_BUSY

                self._log("IP allocation failed: All IPs in pool have reached max usage.")
                return None, AllocationStatus.IP_EXHAUSTED
            
            # 更新下次分配的索引位置
            self.data[DataField.CURRENT_INDEX] = (idx + 1) % total_ips
            
            # 分配IP给邮箱
            if IPField.USED_BY not in candidate:
                candidate[IPField.USED_BY] = []
            candidate[IPField.USED_BY].append(email)
            candidate[IPField.USAGE_COUNT] = len(candidate[IPField.USED_BY])
            candidate[IPField.LAST_UPDATED] = int(time.time())
            
            # 添加到活跃IP集合
            ip_key = self._get_ip_key(candidate[IPField.HOST], candidate[IPField.PORT])
            self.active_ips.add(ip_key)
            
            self._log(f"Allocated IP {candidate[IPField.HOST]}:{candidate[IPField.PORT]} "
                     f"to {email}. Usage: {candidate[IPField.USAGE_COUNT]}/{max_u}")
            self._save_config()
            
            return candidate, AllocationStatus.SUCCESS

    def update_ip_usage(self, host: str, port: str, new_count: int) -> None:
        """
        手动更新IP使用次数。
        
        如果 new_count < 当前值，截断 used_by 列表。
        如果 new_count > 当前值，添加虚拟条目。
        
        Args:
            host: IP地址
            port: 端口号
            new_count: 新的使用次数
        """
        with self.lock:
            for ip in self.data[DataField.IPS]:
                if ip[IPField.HOST] == host and str(ip[IPField.PORT]) == str(port):
                    current_len = len(ip.get(IPField.USED_BY, []))
                    new_count = max(0, int(new_count))
                    
                    if new_count < current_len:
                        # 截断
                        ip[IPField.USED_BY] = ip[IPField.USED_BY][:new_count]
                    elif new_count > current_len:
                        # 添加虚拟条目
                        diff = new_count - current_len
                        for i in range(diff):
                            ip[IPField.USED_BY].append(f"manual_set_{int(time.time())}_{i}")
                    
                    ip[IPField.USAGE_COUNT] = len(ip[IPField.USED_BY])
                    ip[IPField.LAST_UPDATED] = int(time.time())
                    self._log(f"Manually updated IP {host}:{port} usage to {ip[IPField.USAGE_COUNT]}")
                    self._save_config()
                    break

    def release_active_ip(self, host: str, port: str) -> None:
        """
        释放活跃IP锁。
        
        Args:
            host: IP地址
            port: 端口号
        """
        with self.lock:
            ip_key = self._get_ip_key(host, port)
            if ip_key in self.active_ips:
                self.active_ips.remove(ip_key)

    def release_ip(self, host: str, port: str, email: str) -> None:
        """
        释放IP（从 used_by 中移除邮箱）。
        
        通常用于注册失败时回滚IP分配。
        
        Args:
            host: IP地址
            port: 端口号
            email: 要移除的邮箱地址
        """
        with self.lock:
            self.release_active_ip(host, port)
            for ip in self.data[DataField.IPS]:
                if ip[IPField.HOST] == host and str(ip[IPField.PORT]) == str(port):
                    if IPField.USED_BY in ip and email in ip[IPField.USED_BY]:
                        ip[IPField.USED_BY].remove(email)
                        ip[IPField.USAGE_COUNT] = len(ip[IPField.USED_BY])
                        ip[IPField.LAST_UPDATED] = int(time.time())
                        self._log(f"Released IP {host}:{port} for {email}. "
                                 f"Usage: {ip[IPField.USAGE_COUNT]}")
                        self._save_config()
                    break
