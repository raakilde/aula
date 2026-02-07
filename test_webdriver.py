#!/usr/bin/env python3

import logging

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# Set up logging
logging.basicConfig(level=logging.DEBUG)


def test_webdriver():
    """Test WebDriver creation with different option sets"""

    # Basic options for containerized environment
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    chrome_options.add_argument("--remote-debugging-port=9222")

    # Additional stability options
    chrome_options.add_argument("--single-process")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    chrome_options.add_argument("--disable-client-side-phishing-detection")
    chrome_options.add_argument("--disable-crash-reporter")
    chrome_options.add_argument("--disable-oopr-debug-crash-dump")
    chrome_options.add_argument("--no-crash-upload")
    chrome_options.add_argument("--disable-low-res-tiling")
    chrome_options.add_argument("--memory-pressure-off")

    # Set binary location for ARM64
    chrome_options.binary_location = "/usr/bin/chromium"

    driver = None
    try:
        print("Creating WebDriver...")
        from selenium.webdriver.chrome.service import Service

        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print("WebDriver created successfully!")

        print("Testing navigation...")
        driver.get("https://www.google.com")
        print(f"Page title: {driver.title}")
        print("Navigation successful!")

        return True

    except Exception as e:
        print(f"WebDriver test failed: {e}")
        return False

    finally:
        if driver:
            try:
                driver.quit()
                print("WebDriver closed successfully")
            except:
                pass


if __name__ == "__main__":
    test_webdriver()
