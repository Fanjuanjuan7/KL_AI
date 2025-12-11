import os
import sys
import subprocess

def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(r.stdout)
    if r.returncode != 0:
        raise SystemExit(r.returncode)

def main():
    proj = os.path.dirname(__file__)
    venv = os.path.join(proj, '.venv')
    req = os.path.join(proj, 'requirements.txt')
    if not os.path.exists(req):
        print('requirements.txt not found')
        raise SystemExit(1)
    if not os.path.exists(venv):
        print('Creating virtual environment')
        run([sys.executable, '-m', 'venv', venv])
    if sys.platform.startswith('win'):
        pip = os.path.join(venv, 'Scripts', 'pip.exe')
        py = os.path.join(venv, 'Scripts', 'python.exe')
    else:
        pip = os.path.join(venv, 'bin', 'pip')
        py = os.path.join(venv, 'bin', 'python')
    print('Upgrading pip')
    run([py, '-m', 'pip', 'install', '--upgrade', 'pip'])
    print('Installing dependencies')
    run([pip, 'install', '-r', req])
    print('Done')

if __name__ == '__main__':
    main()

