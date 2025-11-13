#!/usr/bin/env python3
"""
Logging System Demonstration

This script demonstrates the new logging system with different log levels
and shows how to use it throughout the JARVIS codebase.

Usage:
    python examples/logging_demo.py
"""

import sys
import os
from pathlib import Path

# Add jarvis to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import logging utilities
from jarvis.core.logger import JarvisLogger, get_logger

def demonstrate_logging():
    """Demonstrate different log levels and features"""
    
    print("=" * 70)
    print("JARVIS Logging System Demonstration")
    print("=" * 70)
    print()
    
    # Setup logging with INFO level (default)
    print("1. Setting up logging with INFO level...")
    JarvisLogger.setup(log_level="INFO", enable_colors=True)
    logger = get_logger("demo")
    print()
    
    # Demonstrate all log levels
    print("2. Demonstrating all log levels:")
    print("-" * 70)
    logger.debug("This is a DEBUG message (won't show at INFO level)")
    logger.info("This is an INFO message ✓")
    logger.warning("This is a WARNING message ⚠️")
    logger.error("This is an ERROR message ❌")
    logger.critical("This is a CRITICAL message 🔥")
    print()
    
    # Change log level to DEBUG
    print("3. Changing log level to DEBUG...")
    JarvisLogger.set_level("DEBUG")
    print("-" * 70)
    logger.debug("Now DEBUG messages are visible! 🔍")
    logger.info("INFO messages still show ✓")
    print()
    
    # Change log level to WARNING
    print("4. Changing log level to WARNING (only warnings and above)...")
    JarvisLogger.set_level("WARNING")
    print("-" * 70)
    logger.debug("DEBUG won't show")
    logger.info("INFO won't show")
    logger.warning("WARNING shows ⚠️")
    logger.error("ERROR shows ❌")
    print()
    
    # Demonstrate multiple loggers from different modules
    print("5. Demonstrating multiple loggers from different modules:")
    JarvisLogger.set_level("INFO")
    print("-" * 70)
    
    logger_main = get_logger("jarvis.main")
    logger_voice = get_logger("jarvis.voice_manager")
    logger_llm = get_logger("jarvis.llm")
    
    logger_main.info("Main module starting...")
    logger_voice.info("Voice manager initialized")
    logger_llm.info("LLM connection established")
    logger_voice.debug("Voice details (not shown at INFO level)")
    print()
    
    # Demonstrate file logging
    print("6. Demonstrating file logging:")
    print("-" * 70)
    
    # Create logs directory if it doesn't exist
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "jarvis_demo.log"
    
    # Re-initialize with file logging
    JarvisLogger._initialized = False  # Reset for demo
    JarvisLogger.setup(
        log_level="DEBUG",
        log_file=str(log_file),
        enable_colors=True
    )
    
    logger_file = get_logger("file_demo")
    logger_file.info(f"Logging to file: {log_file}")
    logger_file.debug("Debug message logged to file")
    logger_file.warning("Warning message logged to file")
    
    if log_file.exists():
        print(f"✓ Log file created: {log_file}")
        print(f"  Log file size: {log_file.stat().st_size} bytes")
        print(f"\nLast 5 lines of log file:")
        print("-" * 70)
        with open(log_file) as f:
            lines = f.readlines()
            for line in lines[-5:]:
                print(f"  {line.rstrip()}")
    print()
    
    # Usage recommendations
    print("7. Usage Recommendations:")
    print("-" * 70)
    print("  • Use DEBUG for detailed debugging information")
    print("  • Use INFO for general informational messages")
    print("  • Use WARNING for potential issues that don't stop execution")
    print("  • Use ERROR for errors that need attention")
    print("  • Use CRITICAL for critical errors that may crash the system")
    print()
    
    print("8. Configuration via .env file:")
    print("-" * 70)
    print("  Add to jarvis/.env:")
    print("    LOG_LEVEL=DEBUG          # Set log level")
    print("    LOG_FILE=logs/jarvis.log # Enable file logging")
    print("    LOG_COLORS=true          # Enable colored output")
    print()
    
    print("=" * 70)
    print("Demo completed successfully! ✓")
    print("=" * 70)


if __name__ == "__main__":
    demonstrate_logging()

