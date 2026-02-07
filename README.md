[![Current Release](https://img.shields.io/github/release/raakilde/aula/all.svg?style=plastic)](https://github.com/raakilde/aula/releases) [![Github All Releases](https://img.shields.io/github/downloads/raakilde/aula/total.svg?style=plastic)](https://github.com/raakilde/aula/releases) [![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=plastic)](https://github.com/hacs/integration)

# Aula

This is a custom component for Home Assistant to integrate Aula. It is very much based on the great work by @scaarup at https://github.com/scaarup/aula. However this "rewrite" comes with new features like:

- Supports Min Uddannelse Forløb, ugenoter og skema
- Supports Systematic A/S Bibliotek

  "Ugeplaner/ugenoter/huskelisten" are stored as sensor attributes. Can be rendered like:

  ```yamlg
  {{ state_attr("sensor.hojelse_skole_emilie", "ugeplan") }}
  ```

  And visualized in your dashboard with the markdown card:

  ```yaml
  type: markdown
  content: '{{ state_attr("sensor.hojelse_skole_emilie", "ugeplan") }}'
  title: Ugeplan for Emilie
  ```

  Another example using vertical-stack and collapsable-cards:

  ![image](https://user-images.githubusercontent.com/8055470/200306258-1c9e98ff-75d9-4111-994c-a69833e40c61.png)

```yaml
type: vertical-stack
cards:
  - type: custom:collapsable-cards
    title: Ugeplan Emilie
    cards:
      - type: markdown
        content: '{{ state_attr("sensor.hojelse_skole_emilie", "ugeplan") }}'
  - type: custom:collapsable-cards
    title: Ugeplan Emilie, næste uge
    cards:
      - type: markdown
        content: '{{ state_attr("sensor.hojelse_skole_emilie", "ugeplan_next") }}'
  - type: custom:collapsable-cards
    title: Ugeplan Rasmus
    cards:
      - type: markdown
        content: '{{ state_attr("sensor.hojelse_skole_rasmus", "ugeplan") }}'
  - type: custom:collapsable-cards
    title: Ugeplan Rasmus, næste uge
    cards:
      - type: markdown
        content: '{{ state_attr("sensor.hojelse_skole_rasmus", "ugeplan_next") }}'
```

   ![image](https://user-images.githubusercontent.com/8055470/199254249-3bf441bc-7dce-4f5d-a809-d119d20a7b2b.png)

- Lots of small fixes and optimizations

## Installation

#### HACS

- Ensure that HACS is installed.
- Search for and install the "Aula" integration.
- Restart Home Assistant.

#### Manual installation

- Download the latest release.
- Unpack the release and copy the custom_components/aula directory into the custom_components directory of your Home Assistant installation.
- Restart Home Assistant.

## Setup

Shortcut:<br>
[![](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=aula)

- Go to Settings -> Integrations -> Add Integration
- Search for "Aula" and follow the instructions in the config flow.

### Authentication

- **Pure QR Code Login**: During setup, a browser window will open for you to complete the login process:
  - **MitID QR Code**: Simply scan the QR code with your MitID app when it appears, then approve the login in your app
  - **Wait for Completion**: Browser window stays open until you complete the MitID approval in your app
  - **Portal Loading**: System waits for Aula portal to fully load after authentication with multiple stabilization phases
  - **Session Capture**: Login session cookies are automatically captured for ongoing data access
  - **Extended Validation**: Browser stays open for 10 seconds after setup completion to ensure everything is properly configured
  - **Auto-Close with Countdown**: Browser window shows "Login successful! Browser will close in 10 seconds..." before closing
  - **No Username Required**: The integration uses pure QR code authentication without needing manual credentials
  - **MitID Authentication**: Secure authentication using Danish MitID with QR code support
- **Smart Detection**: The integration automatically detects which login methods are available on your Aula instance
- **Complete Authentication Flow**: The setup process waits for the entire MitID approval process and full session establishment
- **Headless Operation**: After initial setup, all subsequent logins happen automatically in the background using saved session
- **Session Management**: Secure cookie-based sessions with automatic re-authentication when needed

## System Requirements & Compatibility

This integration uses **browser automation** for MitID authentication. Here's what you need to know for different Home Assistant setups:

### ✅ **Home Assistant OS (Recommended)**
- **Status**: Fully supported out-of-the-box
- **Requirements**: None - built-in browser support
- **Setup**: Install integration → Configure → Complete MitID in browser

### ✅ **Home Assistant Container (Docker)**
- **Status**: Supported with additional setup
- **Requirements**: Chrome/Chromium in container
- **Setup Options**:
  - Option 1: Use container with pre-installed browser
  - Option 2: Add browser to existing container
  - Option 3: Initial setup on desktop → copy cookies to container

### ✅ **Home Assistant Core (Python venv)**
- **Status**: Supported with browser installation
- **Requirements**: Install Chrome/Chromium and ChromeDriver on host system
  ```bash
  # x86_64 systems (Intel/AMD)
  sudo apt install chromium-browser

  # ARM64 systems (Raspberry Pi 4, etc.)
  sudo apt install chromium chromium-driver

  # RHEL/CentOS/Fedora (x64)
  sudo dnf install chromium
  ```
- **Display**: Requires GUI access or X11 forwarding for initial setup

### ⚠️ **Headless/Server Environments**
- **Status**: Supported with workaround
- **Issue**: No display for browser during initial setup
- **Workaround**:
  1. Set up integration on desktop/laptop with browser
  2. Copy authentication cookies to server
  3. Import cookies in headless environment

### 🔧 **Architecture Support**
- **x86_64**: Chrome + ChromeDriver (optimal)
- **ARM64**: Chromium browser (tested)
- **ARMv7**: Chromium browser (should work)

### 🚨 **Troubleshooting Common Issues**

| Error | Solution |
|-------|----------|
| "Browser required" | Install Chrome/Chromium on your system |
| "No display available" | Enable X11 forwarding or use headless workaround |
| "ChromeDriver failed" | **x64**: Auto-installs ChromeDriver<br>**ARM64**: `sudo apt install chromium-driver` |
| "Unable to obtain driver" | ARM64 systems need system ChromeDriver, not auto-installer |
| "Authentication timeout" | Ensure stable internet and complete MitID approval |

### 📱 **Alternative Setup Methods**

1. **Desktop Setup**: Configure on Windows/Mac → export config
2. **Cookie Import**: Manual cookie extraction from existing session
3. **Development Container**: Use VS Code dev containers with browser support

### Known issues

## Support
Join our Discord https://discord.gg/SnfRg3DWG6 and feel free to ask in #homeassistant
