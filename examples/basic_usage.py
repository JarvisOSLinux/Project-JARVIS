#!/usr/bin/env python3
"""
Basic usage example for JARVIS AI Assistant

This example demonstrates how to use JARVIS programmatically
without the voice interface.
"""

import sys
import os

# Add the jarvis directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from jarvis import Jarvis

def main():
    """Basic JARVIS usage example"""
    print("🤖 JARVIS Basic Usage Example")
    print("=" * 40)
    
    try:
        # Initialize JARVIS
        print("Initializing JARVIS...")
        jarvis = Jarvis()
        
        # Example 1: Simple conversation
        print("\n📝 Example 1: Simple Conversation")
        response = jarvis.ask("Hello JARVIS, what can you do?")
        print(f"Response: {response['output']}")
        
        # Example 2: Command execution
        print("\n⚡ Example 2: Command Execution")
        response = jarvis.ask("List the files in the current directory")
        print(f"Response: {response['output']}")
        
        # Example 3: SuperMCP discovery
        print("\n🔍 Example 3: SuperMCP Discovery")
        response = jarvis.ask("What MCP servers are available?")
        print(f"Response: {response['output']}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    print("\n✅ Example completed successfully!")
    return 0

if __name__ == "__main__":
    exit(main())
