#!/usr/bin/env python3
"""
CLI usage examples for JARVIS AI Assistant

This demonstrates the new CLI interface that allows text-based interaction
without voice input.
"""

import subprocess
import sys

def run_command(cmd):
    """Run a CLI command and print the output"""
    print(f"\n$ {cmd}")
    print("-" * 60)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    print("-" * 60)

def main():
    """Demonstrate CLI usage"""
    print("🤖 JARVIS CLI Usage Examples")
    print("=" * 60)
    
    examples = [
        # Check current mode
        ("Check current output mode", "jarvis output-type"),
        
        # Set text mode
        ("Switch to text output", "jarvis text"),
        
        # Ask a question in text mode
        ("Ask a simple question", 'jarvis ask "what is 2 plus 2?"'),
        
        # Check mode again
        ("Verify mode changed", "jarvis output-type"),
        
        # Switch to voice mode
        ("Switch to voice output", "jarvis voice"),
        
        # Show help
        ("Show help", "jarvis --help"),
    ]
    
    print("\nNOTE: These are example commands. Run them in your terminal:")
    print()
    
    for description, command in examples:
        print(f"# {description}")
        print(f"$ {command}")
        print()
    
    print("\n✅ CLI Examples listed above!")
    print("\nTo actually run these commands, execute them in your terminal")
    print("after installing JARVIS with: pip install -e .")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

