"""
Installation module for KL_AI application.

Automates the setup of virtual environment and installation of dependencies
from requirements.txt. Cross-platform support for Windows, macOS, and Linux.

Example:
    >>> from install import main
    >>> main()
    
Or run directly:
    $ python install.py
"""

import os
import sys
import subprocess
import logging
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class InstallationError(Exception):
    """Custom exception for installation errors."""
    pass


def run(
    cmd: List[str],
    cwd: Optional[str] = None,
    check: bool = True,
    capture_output: bool = True
) -> subprocess.CompletedProcess:
    """
    Run a shell command with error handling.

    Args:
        cmd: Command and arguments as a list
        cwd: Working directory for the command
        check: Whether to raise exception on non-zero exit code
        capture_output: Whether to capture stdout/stderr

    Returns:
        CompletedProcess instance

    Raises:
        InstallationError: If command fails and check is True
    """
    logger.info(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
            encoding='utf-8',
            errors='replace'
        )

        if result.stdout:
            logger.info(result.stdout)

        if result.stderr:
            logger.warning(result.stderr)

        if check and result.returncode != 0:
            raise InstallationError(
                f"Command failed with exit code {result.returncode}: {' '.join(cmd)}"
            )

        return result

    except FileNotFoundError as e:
        raise InstallationError(f"Command not found: {cmd[0]}") from e
    except subprocess.SubprocessError as e:
        raise InstallationError(f"Subprocess error: {e}") from e


def get_python_executable(venv_path: Path) -> Tuple[Path, Path]:
    """
    Get Python and pip executables for the virtual environment.

    Args:
        venv_path: Path to virtual environment

    Returns:
        Tuple of (python executable, pip executable)
    """
    if sys.platform.startswith('win'):
        python_exe = venv_path / 'Scripts' / 'python.exe'
        pip_exe = venv_path / 'Scripts' / 'pip.exe'
    else:
        python_exe = venv_path / 'bin' / 'python'
        pip_exe = venv_path / 'bin' / 'pip'

    return python_exe, pip_exe


def create_virtual_environment(venv_path: Path, clear: bool = False) -> None:
    """
    Create a virtual environment.

    Args:
        venv_path: Path where venv should be created
        clear: Whether to clear existing venv

    Raises:
        InstallationError: If creation fails
    """
    if venv_path.exists():
        if clear:
            logger.info(f"Clearing existing virtual environment: {venv_path}")
            shutil.rmtree(venv_path)
        else:
            logger.info(f"Virtual environment already exists: {venv_path}")
            return

    logger.info("Creating virtual environment...")

    try:
        run([sys.executable, '-m', 'venv', str(venv_path)])
    except InstallationError as e:
        # Try with virtualenv if venv fails
        try:
            run([sys.executable, '-m', 'virtualenv', str(venv_path)])
        except InstallationError:
            raise InstallationError(
                f"Failed to create virtual environment. "
                f"Ensure 'venv' or 'virtualenv' is available."
            ) from e


def install_dependencies(python_exe: Path, pip_exe: Path, requirements_path: Path) -> None:
    """
    Install dependencies from requirements.txt.

    Args:
        python_exe: Path to Python executable
        pip_exe: Path to pip executable
        requirements_path: Path to requirements.txt

    Raises:
        InstallationError: If installation fails
    """
    if not requirements_path.exists():
        raise InstallationError(f"requirements.txt not found: {requirements_path}")

    # Upgrade pip first
    logger.info("Upgrading pip...")
    run([str(python_exe), '-m', 'pip', 'install', '--upgrade', 'pip'])

    # Install requirements
    logger.info(f"Installing dependencies from {requirements_path}...")
    run([str(pip_exe), 'install', '-r', str(requirements_path)])


def main(clear_venv: bool = False) -> int:
    """
    Main installation function.

    Args:
        clear_venv: Whether to clear existing virtual environment

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    try:
        # Get project paths
        proj_root = Path(__file__).parent.parent.resolve()
        venv_path = proj_root / '.venv'
        requirements_path = proj_root / 'requirements.txt'

        logger.info(f"Project root: {proj_root}")

        # Check requirements.txt
        if not requirements_path.exists():
            raise InstallationError(f"requirements.txt not found at: {requirements_path}")

        # Create virtual environment
        create_virtual_environment(venv_path, clear=clear_venv)

        # Get executables
        python_exe, pip_exe = get_python_executable(venv_path)

        if not python_exe.exists():
            raise InstallationError(f"Python executable not found: {python_exe}")

        # Install dependencies
        install_dependencies(python_exe, pip_exe, requirements_path)

        logger.info("Installation completed successfully!")
        logger.info(f"To activate the environment, run:")

        if sys.platform.startswith('win'):
            logger.info(f"    {venv_path}\\Scripts\\activate")
        else:
            logger.info(f"    source {venv_path}/bin/activate")

        return 0

    except InstallationError as e:
        logger.error(f"Installation failed: {e}")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    # Allow --clear flag to reset venv
    clear_flag = '--clear' in sys.argv
    sys.exit(main(clear_venv=clear_flag))
