import psutil
import os
import platform
import sys
import time
import json
from datetime import datetime
from typing import List, Dict, Any

def get_process_tree(proc: psutil.Process) -> Dict[str, Any]:
    try:
        children = proc.children(recursive=True)
        return {
            'pid': proc.pid,
            'name': proc.name(),
            'status': proc.status(),
            'memory_mb': proc.memory_info().rss / 1024 / 1024,
            'create_time': datetime.fromtimestamp(proc.create_time()).strftime('%Y-%m-%d %H:%M:%S'),
            'children': [
                {
                    'pid': c.pid,
                    'name': c.name(),
                    'memory_mb': c.memory_info().rss / 1024 / 1024
                } for c in children
            ]
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {}

def find_bitbrowser_processes() -> List[Dict[str, Any]]:
    target_names = ['BitBrowser.exe', 'BitBrowser', 'BitBrowserHelper', 'BitBrowser_Driver']
    found = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            name = proc.info['name']
            if name in target_names or any(t in name for t in target_names):
                found.append(get_process_tree(proc))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found

def clean_processes(processes: List[Dict[str, Any]]) -> None:
    print("\n[Resource Release] Executing force cleanup...")
    for p_info in processes:
        pid = p_info.get('pid')
        if not pid:
            continue
        try:
            if not psutil.pid_exists(pid):
                continue
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    print(f"  Killing child process: {child.name()} (PID={child.pid})")
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            print(f"  Killing parent process: {parent.name()} (PID={pid})")
            parent.kill()
            parent.wait(3)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"  Failed to kill PID={pid}: {e}")

def check_registry_windows() -> List[str]:
    issues = []
    if platform.system() != 'Windows':
        return issues
    
    try:
        import winreg
        # Example: Check for startup items or specific BitBrowser keys that might indicate improper cleanup
        # This is a placeholder as specific registry keys for BitBrowser cleanup aren't specified in the memory
        # But we can check for common persistence locations
        locations = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run")
        ]
        
        for hkey, path in locations:
            try:
                reg = winreg.OpenKey(hkey, path, 0, winreg.KEY_READ)
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(reg, i)
                        if 'BitBrowser' in str(value):
                            issues.append(f"Registry Persistence found: {path} -> {name}={value}")
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(reg)
            except OSError:
                pass
    except ImportError:
        pass
    return issues

def generate_report(processes: List[Dict[str, Any]], registry_issues: List[str]) -> None:
    print("\n" + "="*50)
    print(f"DIAGNOSTIC REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*50)
    
    print(f"\nSystem: {platform.system()} {platform.release()}")
    
    print(f"\n1. Residual Process Analysis:")
    if not processes:
        print("   ✅ No residual BitBrowser processes found.")
    else:
        print(f"   ⚠️ Found {len(processes)} parent processes.")
        total_mem = 0
        for p in processes:
            mem = p.get('memory_mb', 0)
            total_mem += mem
            children = p.get('children', [])
            child_mem = sum(c.get('memory_mb', 0) for c in children)
            total_mem += child_mem
            
            print(f"   - PID: {p['pid']} | Name: {p['name']} | Status: {p['status']}")
            print(f"     Memory: {mem:.2f} MB | Created: {p['create_time']}")
            if children:
                print(f"     Children ({len(children)}):")
                for c in children:
                    print(f"       -> PID: {c['pid']} | Name: {c['name']} | Mem: {c['memory_mb']:.2f} MB")
        
        print(f"\n   Total Memory Wasted: {total_mem:.2f} MB")

    print(f"\n2. Registry/Persistence Check:")
    if not registry_issues:
        print("   ✅ No registry persistence issues detected (or not applicable).")
    else:
        for issue in registry_issues:
            print(f"   ❌ {issue}")

    print("\n3. Root Cause Analysis (Automated):")
    if processes:
        print("   -> Processes were found lingering after exit.")
        print("   -> Possible causes: Crash during shutdown, infinite loop in child process, or 'keep-alive' setting enabled.")
        print("   -> Recommendation: Use the force cleanup utility integrated in the registration script.")
    else:
        print("   -> System appears clean.")

def main():
    print("Starting BitBrowser Resource Validation...")
    
    # 1. Check Processes
    processes = find_bitbrowser_processes()
    
    # 2. Check Registry (Windows only)
    registry_issues = check_registry_windows()
    
    # 3. Generate Report
    generate_report(processes, registry_issues)
    
    # 4. Optional Cleanup
    if processes:
        if '--clean' in sys.argv or '-c' in sys.argv:
            clean_processes(processes)
            print("\nRe-verifying after cleanup...")
            remaining = find_bitbrowser_processes()
            if not remaining:
                print("✅ Cleanup Successful: All processes terminated.")
            else:
                print(f"⚠️ Cleanup Warning: {len(remaining)} processes still remain.")
        else:
            print("\nRun with --clean argument to force terminate these processes.")

if __name__ == '__main__':
    main()
