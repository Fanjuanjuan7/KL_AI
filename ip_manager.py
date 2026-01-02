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
        self.data: Dict[str, Any] = {
            "max_usage_per_ip": 5,
            "ips": [],
            "used_emails": []
        }
        self._load_config()

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
            try:
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"Error saving config: {e}")

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
        Allocates an IP for the given email using Round-Robin strategy.
        Returns (ip_config, error_message)
        """
        with self.lock:
            # Check if email is already associated with an IP (globally)
            for ip in self.data["ips"]:
                if email in ip.get("used_by", []):
                    return None, "email_used"

            max_u = self.data.get("max_usage_per_ip", 5)
            ips = self.data["ips"]
            if not ips:
                return None, "ip_exhausted"

            start_index = self.data.get("current_ip_index", 0)
            if start_index >= len(ips):
                start_index = 0
            
            candidate = None
            found_index = -1

            # Round-Robin Search
            for i in range(len(ips)):
                idx = (start_index + i) % len(ips)
                ip = ips[idx]
                if len(ip.get("used_by", [])) < max_u:
                    candidate = ip
                    found_index = idx
                    break
            
            if not candidate:
                return None, "ip_exhausted"
            
            # Reserve it
            if "used_by" not in candidate:
                candidate["used_by"] = []
            candidate["used_by"].append(email)
            candidate["usage_count"] = len(candidate["used_by"])
            
            # Update pointer to next IP for next allocation
            self.data["current_ip_index"] = (found_index + 1) % len(ips)
            
            self._save_config()
            
            return candidate, "success"

    def release_ip(self, host: str, port: str, email: str):
        """
        Release an IP (remove email from used_by) if registration failed.
        """
        with self.lock:
            for ip in self.data["ips"]:
                if ip["host"] == host and str(ip["port"]) == str(port):
                    if "used_by" in ip and email in ip["used_by"]:
                        ip["used_by"].remove(email)
                        ip["usage_count"] = len(ip["used_by"])
                        self._save_config()
                    break

