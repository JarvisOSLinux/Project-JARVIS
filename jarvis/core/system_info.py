"""
System information gathering for JARVIS AI Assistant

This module handles gathering system information needed for LLM initialization
and other system-dependent operations.
"""

import platform
import shutil
from typing import Dict, List


class SystemInfo:
    """Handles system information gathering and shell detection"""
    
    @staticmethod
    def get_system_info() -> Dict[str, str]:
        """
        Get comprehensive system information for LLM initialization
        
        Returns:
            Dictionary containing system information
        """
        system = platform.system().lower()
        
        return {
            'system': system,
            'release': platform.release(),
            'version': platform.version(),
            'machine': platform.machine(),
            'shell': SystemInfo._get_shell_command(system)
        }
    
    @staticmethod
    def _get_shell_command(system: str) -> List[str]:
        """
        Get the appropriate shell command for the current system
        
        Args:
            system: Platform system name (lowercase)
            
        Returns:
            List of shell command arguments
        """
        if system == "windows":
            if shutil.which("pwsh"):
                return ["pwsh", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
            elif shutil.which("powershell"):
                return ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
            else:
                return ["cmd.exe", "/d", "/s", "/c"]
        else:
            if shutil.which("bash"):
                return ["bash", "-lc"]
            else:
                return ["sh", "-lc"]
    
    @staticmethod
    def get_platform_summary() -> str:
        """
        Get a human-readable platform summary
        
        Returns:
            String describing the current platform
        """
        info = SystemInfo.get_system_info()
        return f"{info['system'].title()} {info['release']} ({info['machine']})"
