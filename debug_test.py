#!/usr/bin/env python3
"""
Test script to verify debugging works correctly.
Place a breakpoint on any line and run this with F5 to test debugging.
"""

import sys
import os

def test_debugging():
    """Test function to verify debugging works."""
    print("Starting debugging test...")
    
    # Add the custom_components to the path
    sys.path.insert(0, '/workspaces/aula')
    
    try:
        from custom_components.aula import client
        print("✅ Successfully imported Aula client")
        
        # This would be a good place to set a breakpoint
        result = "Debugging test completed successfully"
        print(f"✅ {result}")
        
        return result
    except ImportError as e:
        print(f"❌ Failed to import Aula client: {e}")
        return None

if __name__ == "__main__":
    print("Python executable:", sys.executable)
    print("Python version:", sys.version)
    print("Current working directory:", os.getcwd())
    print("Python path:", sys.path[:3])  # Show first 3 entries
    
    test_debugging()