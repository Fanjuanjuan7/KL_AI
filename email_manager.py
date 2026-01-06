import json
import os
import shutil
import threading
import time
import re
from typing import List, Dict, Optional, Tuple, Any, Set

class EmailManager:
    def __init__(self, config_path: str = "resources/email_pool.json"):
        # Ensure directory exists
        config_dir = os.path.dirname(os.path.abspath(config_path))
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir)
            
        self.config_path = os.path.abspath(config_path)
        self.lock = threading.RLock()
        self.on_status_change = None
        self.data: Dict[str, Any] = {
            "emails": [],
            "blacklist": []
        }
        # In-memory map for O(1) access: email.lower() -> item dict
        self._email_map: Dict[str, Dict[str, Any]] = {}
        self._blacklist: Set[str] = set()
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
                
                # Rebuild map
                self._email_map.clear()
                for item in self.data["emails"]:
                    email = item.get("email", "").strip().lower()
                    if email:
                        self._email_map[email] = item
                
                # Rebuild blacklist
                self._blacklist.clear()
                for email in self.data.get("blacklist", []):
                    if email:
                        self._blacklist.add(email.lower())
                        
            except Exception as e:
                print(f"Error loading email config: {e}")
                # Backup corrupted file
                try:
                    shutil.copy(self.config_path, self.config_path + ".corrupted")
                except:
                    pass

    def _save_config(self):
        with self.lock:
            # Auto-backup
            self._create_backup()
            try:
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"Error saving email config: {e}")
            
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

    def validate_email_format(self, email: str) -> bool:
        """
        Validates email format using regex.
        Case-insensitive.
        """
        if not email:
            return False
        # Basic email regex
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        return bool(re.match(pattern, email))

    def import_emails(self, content: str) -> int:
        """
        Import emails from string content.
        Supports:
        - CSV/TSV
        - email,password
        - email\tpassword
        - email,password,code_url
        """
        with self.lock:
            count = 0
            lines = content.splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                parts = []
                if '\t' in line:
                    parts = [p.strip() for p in line.split('\t')]
                elif ',' in line:
                    parts = [p.strip() for p in line.split(',')]
                else:
                    parts = [p.strip() for p in line.split()]
                
                parts = [p for p in parts if p]
                
                if len(parts) >= 2:
                    email = parts[0]
                    password = parts[1]
                    
                    if not self.validate_email_format(email):
                        continue
                        
                    code_url = ""
                    if len(parts) >= 3:
                        code_url = parts[2]
                    else:
                        code_url = f"http://acg-mail-tool.getcharzp.cn/newDetail?email={email}"
                    
                    email_lower = email.lower()
                    
                    if email_lower in self._email_map:
                        # Update existing
                        existing = self._email_map[email_lower]
                        existing["password"] = password
                        existing["code_url"] = code_url
                        # Don't reset status if it's already used/success? 
                        # User didn't specify, but usually import implies reset or update.
                        # Let's keep status unless it's new.
                    else:
                        # New entry
                        entry = {
                            "email": email,
                            "password": password,
                            "code_url": code_url,
                            "status": "new"
                        }
                        self.data["emails"].append(entry)
                        self._email_map[email_lower] = entry
                        count += 1
            
            if count > 0:
                self._save_config()
            return count

    def add_email(self, email: str, password: str, code_url: str = "") -> bool:
        with self.lock:
            if not self.validate_email_format(email):
                return False
                
            email_lower = email.lower()
            if email_lower in self._email_map:
                return False
            
            if not code_url:
                code_url = f"http://acg-mail-tool.getcharzp.cn/newDetail?email={email}"
            
            entry = {
                "email": email,
                "password": password,
                "code_url": code_url,
                "status": "new"
            }
            self.data["emails"].append(entry)
            self._email_map[email_lower] = entry
            self._save_config()
            return True

    def update_email(self, original_email: str, new_email: str, password: str, code_url: str):
        with self.lock:
            orig_lower = original_email.lower()
            if orig_lower not in self._email_map:
                return False
            
            if not self.validate_email_format(new_email):
                return False
                
            new_lower = new_email.lower()
            
            # If changing email address, check if new one exists (unless it's the same)
            if new_lower != orig_lower and new_lower in self._email_map:
                return False
            
            item = self._email_map[orig_lower]
            item["email"] = new_email
            item["password"] = password
            item["code_url"] = code_url
            
            if new_lower != orig_lower:
                del self._email_map[orig_lower]
                self._email_map[new_lower] = item
                
            self._save_config()
            return True

    def update_email_status(self, email: str, status: str):
        with self.lock:
            email_lower = email.lower()
            if email_lower in self._email_map:
                self._email_map[email_lower]["status"] = status
                self._save_config()
                return True
            return False

    def delete_email(self, email: str):
        with self.lock:
            email_lower = email.lower()
            if email_lower in self._email_map:
                self.data["emails"] = [e for e in self.data["emails"] if e["email"].lower() != email_lower]
                del self._email_map[email_lower]
                self._save_config()

    def clear_emails(self):
        with self.lock:
            self.data["emails"] = []
            self._email_map.clear()
            self._save_config()

    def get_all_emails(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.data["emails"])

    def get_stats(self) -> Dict[str, int]:
        with self.lock:
            total = len(self.data["emails"])
            # 'submitted' implies it has been used in a registration attempt that reached the final button
            used = sum(1 for e in self.data["emails"] if e.get("status") in ("good", "success", "registered", "submitted", "fail_used"))
            failed = sum(1 for e in self.data["emails"] if e.get("status") in ("fail", "failed"))
            return {
                "total_emails": total,
                "used_emails": used,
                "failed_emails": failed,
                "available_emails": total - used
            }

    def get_code_url(self, email: str) -> Optional[str]:
        with self.lock:
            item = self._email_map.get(email.lower())
            return item["code_url"] if item else None

    def get_status(self, email: str) -> Optional[str]:
        with self.lock:
            item = self._email_map.get(email.lower())
            return item.get("status") if item else None

    def is_email_registered(self, email: str) -> bool:
        """
        Check if email is already registered/used or blacklisted.
        Performance: O(1) via map/set lookup.
        """
        with self.lock:
            email_lower = email.lower()
            # Check blacklist first
            if email_lower in self._blacklist:
                return True
                
            item = self._email_map.get(email_lower)
            if item and item.get("status") in ("good", "success", "registered", "submitted", "fail_used"):
                return True
            return False

    def add_to_blacklist(self, email: str) -> bool:
        with self.lock:
            if not self.validate_email_format(email):
                return False
            email_lower = email.lower()
            if email_lower not in self._blacklist:
                self._blacklist.add(email_lower)
                self._save_config()
                return True
            return False

