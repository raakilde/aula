#!/usr/bin/env python3
"""Test script for the new config flow approach"""

import json

from custom_components.aula.client import Client


def test_cookie_parsing():
    """Test JSON cookie parsing"""

    # Test valid JSON
    valid_cookies = '{"PHPSESSID": "abc123", "aula_token": "xyz789"}'
    try:
        parsed = json.loads(valid_cookies)
        print(f"✅ Valid JSON parsed: {parsed}")
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse valid JSON: {e}")

    # Test invalid JSON
    invalid_cookies = "PHPSESSID=abc123; aula_token=xyz789"
    try:
        parsed = json.loads(invalid_cookies)
        print(f"❌ Invalid JSON should have failed: {parsed}")
    except json.JSONDecodeError as e:
        print(f"✅ Correctly rejected invalid JSON: {e}")


def test_client_creation():
    """Test Client creation with cookies"""

    cookies = {"PHPSESSID": "test123", "aula_token": "test789"}

    try:
        client = Client(
            schoolschedule=True,
            ugeplan=False,
            bibliotek=False,
            minUddannelseForloeb=False,
            minUddannelseOpgaveListe=False,
            minUddannelseUgeNote=False,
            auth_cookies=cookies,
        )
        print("✅ Client created successfully with cookies")

        # Test the new test_authentication method
        # This will fail since we don't have valid cookies, but tests the method exists
        result = client.test_authentication()
        print(
            f"🔍 Authentication test result: {result} (expected False for test cookies)"
        )

    except Exception as e:
        print(f"❌ Client creation failed: {e}")


if __name__ == "__main__":
    print("Testing new config flow approach...")
    print("\n1. Testing JSON cookie parsing:")
    test_cookie_parsing()

    print("\n2. Testing Client creation:")
    test_client_creation()

    print("\nTests completed!")
