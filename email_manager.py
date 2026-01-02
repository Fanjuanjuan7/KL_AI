import json
import os
import shutil
import threading
import time
from typing import List, Dict, Optional, Tuple, Any

class EmailManager:
    def __init__(self, config_path: str = "resources/email_pool.json"):
        # Ensure directory exists
        config_dir = os.path.dirname(os.path.abspath(config_path))
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir)
            
        self.config_path = os.path.abspath(config_path)
        self.lock = threading.RLock()
        self.data: Dict[str, Any] = {
            "emails": []
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
                
                # Replace common separators with comma for splitting
                # But careful, code_url might contain colons.
                # Use tab or comma or pipe.
                
                parts = []
                if '\t' in line:
                    parts = [p.strip() for p in line.split('\t')]
                elif ',' in line:
                    parts = [p.strip() for p in line.split(',')]
                else:
                    # Maybe space separated?
                    parts = [p.strip() for p in line.split()]
                
                parts = [p for p in parts if p]
                
                if len(parts) >= 2:
                    email = parts[0]
                    password = parts[1]
                    code_url = ""
                    if len(parts) >= 3:
                        code_url = parts[2]
                    else:
                        code_url = f"http://acg-mail-tool.getcharzp.cn/newDetail?email={email}"
                    
                    entry = {
                        "email": email,
                        "password": password,
                        "code_url": code_url,
                        "status": "new"
                    }
                    
                    # Check duplicate
                    exists = False
                    for existing in self.data["emails"]:
                        if existing["email"] == email:
                            exists = True
                            # Update existing?
                            existing["password"] = password
                            existing["code_url"] = code_url
                            break
                    
                    if not exists:
                        self.data["emails"].append(entry)
                        count += 1
            
            if count > 0:
                self._save_config()
            return count

    def add_email(self, email: str, password: str, code_url: str = "") -> bool:
        with self.lock:
            # Check duplicate
            for existing in self.data["emails"]:
                if existing["email"] == email:
                    return False
            
            if not code_url:
                code_url = f"http://acg-mail-tool.getcharzp.cn/newDetail?email={email}"
            
            self.data["emails"].append({
                "email": email,
                "password": password,
                "code_url": code_url,
                "status": "new"
            })
            self._save_config()
            return True

    def update_email(self, original_email: str, new_email: str, password: str, code_url: str):
        with self.lock:
            for item in self.data["emails"]:
                if item["email"] == original_email:
                    item["email"] = new_email
                    item["password"] = password
                    item["code_url"] = code_url
                    self._save_config()
                    return True
            return False

    def delete_email(self, email: str):
        with self.lock:
            initial = len(self.data["emails"])
            self.data["emails"] = [e for e in self.data["emails"] if e["email"] != email]
            if len(self.data["emails"]) < initial:
                self._save_config()

    def clear_emails(self):
        with self.lock:
            self.data["emails"] = []
            self._save_config()

    def get_all_emails(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.data["emails"])

    def get_stats(self) -> Dict[str, int]:
        with self.lock:
            total = len(self.data["emails"])
            used = sum(1 for e in self.data["emails"] if e.get("status") in ("good", "success", "registered"))
            failed = sum(1 for e in self.data["emails"] if e.get("status") in ("fail", "failed"))
            return {
                "total_emails": total,
                "used_emails": used,
                "failed_emails": failed,
                "available_emails": total - used
            }

    def get_code_url(self, email: str) -> Optional[str]:
        with self.lock:
            for item in self.data["emails"]:
                if item["email"] == email:
                    return item["code_url"]
            return None
