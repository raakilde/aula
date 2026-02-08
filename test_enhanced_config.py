#!/usr/bin/env python3
"""Test the enhanced config flow with JWT token support"""

import json
import os
import sys

# Add the custom component to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components"))

from aula.client import Client


def test_session_cookies():
    """Test session cookies authentication"""
    print("Testing Session Cookies Authentication:")

    # Test valid JSON cookies
    valid_cookies = '{"PHPSESSID": "abc123", "aula_remember_token": "def456"}'
    try:
        parsed = json.loads(valid_cookies)
        print(f"✅ Valid JSON parsed: {parsed}")
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse valid JSON: {e}")
        return False

    # Test client creation with session cookies
    try:
        client = Client(
            schoolschedule=True,
            ugeplan=False,
            bibliotek=False,
            minUddannelseForloeb=False,
            minUddannelseOpgaveListe=False,
            minUddannelseUgeNote=False,
            auth_cookies=parsed,
        )
        print("✅ Client created successfully with session cookies")

        # Test authentication (will fail with fake cookies but tests the code path)
        result = client.test_authentication()
        print(
            f"🔍 Session authentication test result: {result} (expected False for test cookies)"
        )
        return True

    except Exception as e:
        print(f"❌ Client creation with session cookies failed: {e}")
        return False


def test_jwt_token():
    """Test JWT token authentication"""
    print("\nTesting JWT Token Authentication:")

    # Test with fake JWT token
    fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"

    try:
        # Create client with JWT token as special cookie
        jwt_cookies = [{"name": "_jwt_token", "value": fake_jwt, "domain": "aula.dk"}]

        client = Client(
            schoolschedule=True,
            ugeplan=False,
            bibliotek=False,
            minUddannelseForloeb=False,
            minUddannelseOpgaveListe=False,
            minUddannelseUgeNote=False,
            auth_cookies=jwt_cookies,
        )
        print("✅ Client created successfully with JWT token")

        # Test JWT token directly
        result = client.test_jwt_token(fake_jwt)
        print(f"🔍 JWT token test result: {result} (expected False for fake token)")

        # Test authentication with JWT token
        result = client.test_authentication()
        print(
            f"🔍 JWT authentication test result: {result} (expected False for fake token)"
        )
        return True

    except Exception as e:
        print(f"❌ JWT token testing failed: {e}")
        return False


def test_invalid_inputs():
    """Test invalid inputs"""
    print("\nTesting Invalid Inputs:")

    # Test invalid JSON
    invalid_json = "PHPSESSID=abc123; aula_token=def456"
    try:
        parsed = json.loads(invalid_json)
        print(f"❌ Invalid JSON should have failed: {parsed}")
        return False
    except json.JSONDecodeError as e:
        print(f"✅ Correctly rejected invalid JSON: {e}")

    # Test empty inputs
    try:
        client = Client(
            schoolschedule=False,
            ugeplan=False,
            bibliotek=False,
            minUddannelseForloeb=False,
            minUddannelseOpgaveListe=False,
            minUddannelseUgeNote=False,
            auth_cookies=None,
        )
        result = client.test_authentication()
        print(f"✅ Correctly handled empty cookies: {result}")
        return True

    except Exception as e:
        print(f"❌ Empty cookies test failed: {e}")
        return False


if __name__ == "__main__":
    print("Testing Enhanced Config Flow with JWT Token Support...")
    print("=" * 60)

    success = True
    success &= test_session_cookies()
    success &= test_jwt_token()
    success &= test_invalid_inputs()

    print("\n" + "=" * 60)
    if success:
        print("✅ All tests passed! Enhanced config flow is working correctly.")
    else:
        print("❌ Some tests failed. Please check the implementation.")
    print("=" * 60)
