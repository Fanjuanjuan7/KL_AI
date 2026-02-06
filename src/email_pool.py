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

    def is_email_valid(self, email: str) -> bool:
        """Check if an email is valid for registration."""
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    status = item.get('status', 'new').lower()
                    # User requested explicit rejection for "invalid" or "disabled" status
                    # We treat 'disabled', 'invalid', 'banned' as invalid
                    if status in ('disabled', 'invalid', 'banned'):
                        return False
                    return True
        return False # Email not found is also invalid for our pool

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
                    if status in ('disabled', 'invalid', 'banned'):
                         return False, "该邮箱已被禁用"
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

    def get_stats(self, mode_filter: str = None) -> Dict[str, int]:
        with self._lock:
            # Filter emails based on mode if provided
            if mode_filter == "心蓝模式":
                # XinLan: auth_code starts with http
                target_emails = [e for e in self.emails if e.get('auth_code', '').startswith('http')]
            elif mode_filter == "IMAP模式":
                # IMAP: auth_code does NOT start with http
                target_emails = [e for e in self.emails if not e.get('auth_code', '').startswith('http')]
            else:
                target_emails = self.emails

            total = len(target_emails)
            # Used = success, registered, submitted, used.
            used = sum(1 for e in target_emails if e.get('status', 'new') in ('success', 'registered', 'submitted', 'used'))
        return {
            'total_emails': total,
            'used_emails': used
        }

    def delete_email(self, email: str) -> bool:
        """Delete an email from the pool."""
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

    def update_status(self, email: str, status: str) -> bool:
        """Update status of an email."""
        updated = False
        with self._lock:
            for e in self.emails:
                if e['email'] == email:
                    if e.get('status') != status:
                        e['status'] = status
                        updated = True
                    break
            if updated:
                self._save_pool()
        
        if updated:
            self._notify_listeners()
        return updated

    def get_email_config(self, email: str) -> Optional[Dict[str, str]]:
        with self._lock:
            for item in self.emails:
                if item['email'] == email:
                    return item
        return None

    XINLAN_DEFAULT_PASSWORD = "Juan123123."

    def import_emails(self, content: str, overwrite: bool = True) -> int:
        """
        Import emails from string content.
        Supported Formats:
        1. IMAP (3-col): Email----Password----AuthCode
        2. XinLan (2-col): Email----URL (Password defaults to 'Juan123123.')
        3. XinLan (3-col): Email----Password----URL
        4. Legacy: Email Password AuthCode (Space/Tab separated)
        """
        count = 0
        lines = content.splitlines()
        
        with self._lock:
            if overwrite:
                pass

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Default values
                email = ""
                password = ""
                auth_code = ""

                # Try ---- split first
                if '----' in line:
                    parts = line.split('----')
                    email = parts[0].strip()
                    
                    # Logic to distinguish modes based on content
                    if len(parts) >= 2:
                        part2 = parts[1].strip()
                        # Case 1: XinLan 2-col (Email----URL)
                        if part2.startswith('http'):
                            password = self.XINLAN_DEFAULT_PASSWORD
                            auth_code = part2
                        else:
                            # Standard/IMAP or XinLan 3-col
                            password = part2
                            if len(parts) >= 3:
                                part3 = parts[2].strip()
                                # Case 2: XinLan 3-col (Email----Password----URL)
                                # Case 3: IMAP 3-col (Email----Password----AuthCode)
                                # In both cases, part3 is stored as auth_code.
                                # Downstream logic (in register_kling_bitbrowser.py) will check if it starts with 'http'
                                auth_code = part3
                            else:
                                # Fallback: IMAP 2-col (Email----Password) -> AuthCode = Password
                                auth_code = password
                else:
                    # Try space/tab split (Legacy)
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
