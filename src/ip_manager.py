import json
import os
import shutil
import threading
import re
import time
from typing import List, Dict, Optional, Tuple, Any

class IPManager:
    def __init__(self, config_path: str = "resources/ip_pool.json"):
        # Ensure directory exists
        config_dir = os.path.dirname(os.path.abspath(config_path))
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir)
            
        self.config_path = os.path.abspath(config_path)
        self.lock = threading.RLock()
        self.active_ips = set() # Track IPs currently being used by running tasks
        self.on_status_change = None
        self.logger = None
        self.data: Dict[str, Any] = {
            "max_usage_per_ip": 5,
            "ips": [],
            "used_emails": []
        }
        self._load_config()
        self.verify_consistency()

    def set_logger(self, logger):
        self.logger = logger

    def _log(self, msg: str):
        if self.logger:
            try:
                self.logger(f"[IPManager] {msg}")
            except:
                pass

    def verify_consistency(self):
        """
        Verify and fix data consistency between usage_count and used_by list.
        """
        with self.lock:
            fixed_count = 0
            for ip in self.data["ips"]:
                # Ensure used_by is a list
                if not isinstance(ip.get("used_by"), list):
                    ip["used_by"] = []
                
                real_count = len(ip["used_by"])
                if ip.get("usage_count", 0) != real_count:
                    ip["usage_count"] = real_count
                    fixed_count += 1
            
            if fixed_count > 0:
                self._log(f"Consistency check: Fixed usage counts for {fixed_count} IPs.")
                self._save_config()

    def validate_state(self) -> bool:
        """
        Validates internal state and sync with disk.
        Returns True if consistent.
        """
        with self.lock:
            # 1. Internal consistency
            for ip in self.data["ips"]:
                if len(ip.get("used_by", [])) != ip.get("usage_count", 0):
                    self._log(f"Inconsistency found for IP {ip.get('host')}:{ip.get('port')}")
                    return False
            
            # 2. Disk sync check
            try:
                if not os.path.exists(self.config_path):
                    return False
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    disk_data = json.load(f)
                
                # Check if important fields match
                if len(disk_data.get("ips", [])) != len(self.data["ips"]):
                     self._log("Disk data count mismatch")
                     return False
                     
                return True
            except Exception as e:
                self._log(f"Validation error: {e}")
                return False

    def _load_config(self):
        with self.lock:
            if not os.path.exists(self.config_path):
                self._save_config()
                return

            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
                    
                # Migration: Ensure 'used_by' exists in all IPs
                changed = False
                for ip in self.data["ips"]:
                    if "used_by" not in ip:
                        # Migrate usage_count to dummy used_by
                        cnt = ip.get("usage_count", 0)
                        ip["used_by"] = [f"migrated_{i}" for i in range(cnt)]
                        changed = True
                    # Ensure usage_count is consistent
                    ip["usage_count"] = len(ip["used_by"])
                
                if changed:
                    self._save_config()
                    
            except Exception as e:
                print(f"Error loading config: {e}")
                # Backup corrupted file?
                try:
                    shutil.copy(self.config_path, self.config_path + ".corrupted")
                except:
                    pass

    def _save_config(self):
        with self.lock:
            # Auto-backup logic
            self._create_backup()
            temp_path = self.config_path + ".tmp"
            try:
                # Atomic write: write to temp file then rename
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, indent=2, ensure_ascii=False)
                os.replace(temp_path, self.config_path)
            except Exception as e:
                print(f"Error saving config: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except:
                        pass
            
            if self.on_status_change:
                try:
                    self.on_status_change()
                except:
                    pass

    def set_on_status_change_callback(self, callback):
        self.on_status_change = callback

    def _create_backup(self):
        """
        Creates a backup in the 'backups' directory with timestamp.
        Auto-cleans old backups.
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
        except Exception as e:
            print(f"Backup failed: {e}")
            
    def _cleanup_backups(self, backup_dir: str, prefix: str, max_backups: int = 10):
        try:
            files = []
            for f in os.listdir(backup_dir):
                if f.startswith(prefix) and f.endswith(".bak"):
                    files.append(os.path.join(backup_dir, f))
            
            # Sort by modification time, oldest first
            files.sort(key=os.path.getmtime)
            
            while len(files) > max_backups:
                oldest = files.pop(0)
                os.remove(oldest)
        except Exception:
            pass

    def set_max_usage(self, n: int):
        with self.lock:
            self.data["max_usage_per_ip"] = max(1, int(n))
            self._save_config()

    def get_max_usage(self) -> int:
        return self.data.get("max_usage_per_ip", 5)

    def import_ips(self, content: str, regex: Optional[str] = None, replace_str: str = "", 
                   default_port: str = "", default_user: str = "", default_pass: str = "", default_protocol: str = "socks5") -> int:
        """
        Import IPs from string content.
        Expected format per line: host,port,user,pass OR host:port:user:pass
        If parts are missing, use provided defaults.
        """
        with self.lock:
            count = 0
            lines = content.splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                if regex:
                    try:
                        line = re.sub(regex, replace_str, line)
                    except:
                        pass
                
                # Try to parse
                # Replace common separators with comma
                cleaned = line.replace(':', ',').replace('|', ',').replace('\t', ',')
                parts = [p.strip() for p in cleaned.split(',') if p.strip()]
                
                if len(parts) >= 1:
                    host = parts[0]
                    port = parts[1] if len(parts) > 1 else default_port
                    user = parts[2] if len(parts) > 2 else default_user
                    pwd  = parts[3] if len(parts) > 3 else default_pass
                    
                    # If port is still empty, skip or warn? 
                    # Assuming port is required for a valid proxy, but maybe user just wants to store IP.
                    # But without port it's usually invalid.
                    if not port:
                         # If default is also empty, we can't really use it effectively unless it's just an IP list.
                         # But let's allow it, maybe they will edit later.
                         pass

                    ip_entry = {
                        "host": host,
                        "port": port,
                        "proxyUserName": user,
                        "proxyPassword": pwd,
                        "protocol": default_protocol,
                        "used_by": []  # Initialize with empty list
                    }
                    ip_entry["usage_count"] = 0
                    
                    # Check for duplicates (host:port)
                    exists = False
                    for existing in self.data["ips"]:
                        if existing["host"] == ip_entry["host"] and existing["port"] == ip_entry["port"]:
                            exists = True
                            break
                    
                    if not exists:
                        self.data["ips"].append(ip_entry)
                        count += 1
            
            if count > 0:
                # Prioritize new IPs: set current index to the start of new IPs
                total_ips = len(self.data["ips"])
                first_new_index = max(0, total_ips - count)
                self.data["current_ip_index"] = first_new_index
                self._log(f"Imported {count} new IPs. Reset allocation index to {first_new_index}.")
                self._save_config()
            return count

    def get_used_emails(self) -> set:
        with self.lock:
            all_used = set()
            for ip in self.data["ips"]:
                all_used.update(ip.get("used_by", []))
            return all_used


    def delete_ips(self, pattern: str) -> int:
        """
        Delete IPs where host or port matches the regex pattern.
        """
        with self.lock:
            initial_count = len(self.data["ips"])
            try:
                cp = re.compile(pattern)
                self.data["ips"] = [
                    ip for ip in self.data["ips"] 
                    if not (cp.search(ip.get("host","")) or cp.search(str(ip.get("port",""))))
                ]
            except Exception:
                return 0
            
            removed = initial_count - len(self.data["ips"])
            if removed > 0:
                self._save_config()
            return removed

    def clear_ips(self):
        with self.lock:
            self.data["ips"] = []
            self._save_config()

    def get_all_ips(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.data["ips"])

    def get_stats(self) -> Dict[str, int]:
        with self.lock:
            total = len(self.data["ips"])
            max_u = self.data.get("max_usage_per_ip", 5)
            # available: IPs that have not reached max usage
            available = sum(1 for ip in self.data["ips"] if len(ip.get("used_by", [])) < max_u)
            
            # used_emails: total unique emails in all used_by lists
            all_used = set()
            total_usage_slots = 0
            used_slots = 0
            
            for ip in self.data["ips"]:
                all_used.update(ip.get("used_by", []))
                total_usage_slots += max_u
                used_slots += len(ip.get("used_by", []))
            
            remaining_usage = total_usage_slots - used_slots
            
            return {
                "total_ips": total,
                "available_ips": available,
                "used_emails_count": len(all_used),
                "max_usage": max_u,
                "remaining_usage_count": remaining_usage
            }

    def allocate_ip(self, email: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Allocates an IP for the given email using Strict Round Robin strategy based on usage status.
        Returns (ip_config, error_message)
        """
        with self.lock:
            # Check if email is already associated with an IP (globally)
            for ip in self.data["ips"]:
                if email in ip.get("used_by", []):
                    return None, "email_used"

            max_u = self.data.get("max_usage_per_ip", 5)
            ips = self.data["ips"]
            total_ips = len(ips)
            if total_ips == 0:
                return None, "ip_exhausted"

            # Strict Rotation: Start from current_ip_index and find first available
            start_index = self.data.get("current_ip_index", 0)
            candidate = None
            
            # Iterate once through the list
            for i in range(total_ips):
                idx = (start_index + i) % total_ips
                ip = ips[idx]
                used_count = len(ip.get("used_by", []))
                
                # Check if IP is active (concurrency control)
                # ip_key = (ip["host"], str(ip["port"]))
                # if ip_key in self.active_ips:
                #     continue

                if used_count < max_u:
                    candidate = ip
                    # Update index for next time (round robin)
                    self.data["current_ip_index"] = (idx + 1) % total_ips
                    break
            
            if not candidate:
                # If we failed to find one, check if it's because they are all busy (active) or all full
                any_capacity = False
                for ip in ips:
                    if len(ip.get("used_by", [])) < max_u:
                        any_capacity = True
                        break
                
                if any_capacity:
                    # Capacity exists but currently locked
                    return None, "ip_busy"

                self._log("IP allocation failed: All IPs in pool have reached max usage.")
                return None, "ip_exhausted"
            
            # Reserve it
            if "used_by" not in candidate:
                candidate["used_by"] = []
            candidate["used_by"].append(email)
            candidate["usage_count"] = len(candidate["used_by"])
            candidate["last_updated"] = int(time.time())
            
            # Add to active_ips
            self.active_ips.add((candidate["host"], str(candidate["port"])))
            
            self._log(f"Allocated IP {candidate['host']}:{candidate['port']} to {email}. Usage: {candidate['usage_count']}/{max_u}")
            self._save_config()
            
            return candidate, "success"

    def update_ip_usage(self, host: str, port: str, new_count: int):
        """
        Manually update usage count.
        If new_count < current, truncate used_by.
        If new_count > current, add dummy entries.
        """
        with self.lock:
            for ip in self.data["ips"]:
                if ip["host"] == host and str(ip["port"]) == str(port):
                    current_len = len(ip.get("used_by", []))
                    new_count = max(0, int(new_count))
                    
                    if new_count < current_len:
                        # Truncate
                        ip["used_by"] = ip["used_by"][:new_count]
                    elif new_count > current_len:
                        # Add dummy
                        diff = new_count - current_len
                        for i in range(diff):
                            ip["used_by"].append(f"manual_set_{int(time.time())}_{i}")
                    
                    ip["usage_count"] = len(ip["used_by"])
                    ip["last_updated"] = int(time.time())
                    self._log(f"Manually updated IP {host}:{port} usage to {ip['usage_count']}")
                    self._save_config()
                    break

    def release_active_ip(self, host: str, port: str):
        with self.lock:
            key = (host, str(port))
            if key in self.active_ips:
                self.active_ips.remove(key)
                # self._log(f"Released active lock for IP {host}:{port}")

    def release_ip(self, host: str, port: str, email: str):
        """
        Release an IP (remove email from used_by) if registration failed.
        """
        with self.lock:
            self.release_active_ip(host, port)
            for ip in self.data["ips"]:
                if ip["host"] == host and str(ip["port"]) == str(port):
                    if "used_by" in ip and email in ip["used_by"]:
                        ip["used_by"].remove(email)
                        ip["usage_count"] = len(ip["used_by"])
                        ip["last_updated"] = int(time.time())
                        self._log(f"Released IP {host}:{port} for {email}. Usage: {ip['usage_count']}")
                        self._save_config()
                    break

