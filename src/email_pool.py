import os
import random
import threading
import shutil
import tempfile
from typing import Dict, Optional, List

class EmailPool:
    def __init__(self, pool_file: str):
        self.pool_file = pool_file
        self.emails: List[Dict[str, str]] = []
        self._listeners = []
        self._lock = threading.RLock()
        self._load_pool()

    def add_listener(self, callback):
        """Register a callback for status changes."""
        with self._lock:
            self._listeners.append(callback)

    def _notify_listeners(self):
        """Notify all listeners of a change."""
        with self._lock:
            listeners = list(self._listeners)
        
        for callback in listeners:
            try:
                callback()
            except Exception as e:
                print(f"Error in listener callback: {e}")

    def _load_pool(self):
        if not os.path.exists(self.pool_file):
            return

        with self._lock:
            try:
                with open(self.pool_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except Exception:
                lines = []

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                parts = line.split('----')
                email = parts[0].strip()
                password = parts[1].strip() if len(parts) > 1 else ""
                auth_code = parts[2].strip() if len(parts) > 2 else password
                raw_status = parts[3].strip() if len(parts) > 3 else "new"

                # Normalize status
                status = raw_status.lower()
                if not status or status == 'new':
                    status = 'new'

                self.emails.append({
                    'email': email,
                    'password': password,
                    'auth_code': auth_code, 
                    'status': status
                })

    def _save_pool(self):
        try:
            # Atomic write: write to temp file then rename
            with self._lock:
                # Create temp file in the same directory to ensure atomic rename works across filesystems
                dir_name = os.path.dirname(self.pool_file) or '.'
                with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding='utf-8') as tf:
                    temp_path = tf.name
                    for item in self.emails:
                        email = item['email']
                        password = item['password']
                        auth_code = item['auth_code']
                        status = item.get('status', 'new')
                        tf.write(f"{email}----{password}----{auth_code}----{status}\n")
                
                # Rename temp to actual
                shutil.move(temp_path, self.pool_file)

        except Exception as e:
            print(f"Failed to save pool: {e}")
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

    def update_email_status(self, email: str, status: str):
        """Update status for an email and save."""
        dirty = False
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    if item.get('status') != status:
                        item['status'] = status
                        dirty = True
                    break
            if dirty:
                self._save_pool()
        
        # Notify outside lock
        if dirty:
            self._notify_listeners()

    def check_email_availability(self, email: str) -> tuple[bool, str]:
        """Check if email is available for registration."""
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    status = item.get('status', 'new')
                    # 'new', 'failed', 'stopped' are usually considered retryable or available depending on logic.
                    # 'success', 'registered', 'submitted' are definitely done.
                    # 'processing' means currently in use.
                    
                    if status in ('success', 'registered', 'submitted'):
                         return False, f"状态为 {status}"
                    if status == 'processing':
                         return False, "正在处理中"
                    # For 'failed' or 'stopped', we generally allow retry unless explicitly filtered elsewhere.
                    # But if the user says "No available emails", it might be because everything is marked 'failed' or 'processing'.
                    # We should be permissive here: as long as it's not success/registered/processing, it's available.
                    return True, "可用"
            return False, "未找到"

    def is_email_registered(self, email: str) -> bool:
        """Legacy check."""
        avail, _ = self.check_email_availability(email)
        return not avail

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            total = len(self.emails)
            # Used = success, registered, submitted.
            # Processing is temporary used.
            # Failed/Stopped/New are technically 'available' for next run, but failed/stopped might need manual reset?
            # User wants "Used" vs "Unused".
            # Unused = Total - Used.
            # If we count 'failed' as used, they can't be reused.
            # Let's define 'used' as completed (success/registered/submitted).
            used = sum(1 for e in self.emails if e.get('status', 'new') in ('success', 'registered', 'submitted'))
        return {
            'total_emails': total,
            'used_emails': used
        }

    def get_email_config(self, email: str) -> Optional[Dict[str, str]]:
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    return item
        return None

    def import_emails(self, content: str, overwrite: bool = True) -> int:
        """
        Import emails from string content.
        Format: email----password----auth_code
        or legacy: email password auth_code
        """
        count = 0
        lines = content.splitlines()
        
        with self._lock:
            if overwrite:
                # If overwrite is True, we might want to clear existing or merge.
                # Based on user intent "overwrite existing status", we'll upsert.
                pass

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Try ---- split first
                if '----' in line:
                    parts = line.split('----')
                    email = parts[0].strip()
                    password = parts[1].strip() if len(parts) > 1 else ""
                    auth_code = parts[2].strip() if len(parts) > 2 else password
                else:
                    # Try space/tab split
                    parts = line.replace('\t', ' ').split()
                    if not parts: continue
                    email = parts[0].strip()
                    password = parts[1].strip() if len(parts) > 1 else ""
                    auth_code = parts[2].strip() if len(parts) > 2 else password

                if not email:
                    continue

                # Upsert
                found = False
                for item in self.emails:
                    if item['email'] == email:
                        item['password'] = password
                        item['auth_code'] = auth_code
                        if overwrite:
                            item['status'] = 'new'
                        found = True
                        break
                
                if not found:
                    self.emails.append({
                        'email': email,
                        'password': password,
                        'auth_code': auth_code,
                        'status': 'new'
                    })
                count += 1
            
            if count > 0:
                self._save_pool()
        
        if count > 0:
            self._notify_listeners()
            
        return count

    def clear_emails(self):
        """Clear all emails from the pool."""
        with self._lock:
            self.emails = []
            self._save_pool()
        self._notify_listeners()

    def get_all_rows(self) -> List[Dict[str, str]]:
        with self._lock:
            return list(self.emails)
