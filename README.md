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

This integration uses a **user-redirect authentication** approach suitable for all Home Assistant deployment types:

#### **Step 1: Feature Selection**
- Go to Settings → Integrations → Add Integration → Search for "Aula"
- Select which Aula features you want to enable (calendars, sensors, etc.)

#### **Step 2: MitID Authentication**
- The integration provides you with a MitID login URL
- **Open this URL in your own browser** (desktop, mobile, tablet - any device with a browser)
- Complete the MitID authentication process in your browser:
  - Scan QR code with MitID app, OR
  - Use MitID code reader, OR
  - Use other available MitID methods
- After successful login, you'll be redirected to the Aula portal

#### **Step 3: Extract Session Cookies**
- With Aula portal open in your browser, open Developer Tools (F12)
- Go to **Application** tab → **Storage** → **Cookies** → `https://www.aula.dk`
- Copy all session cookies in JSON format (e.g., `{"PHPSESSID": "abc123", "aula_token": "xyz789"}`)
- Paste the JSON into the Home Assistant configuration form
- Click Submit to complete setup

#### **Key Benefits**
- ✅ **Works on ALL Home Assistant deployments** (OS, Container, Core, Cloud, etc.)
- ✅ **No browser required on HA server** - use any device with a browser
- ✅ **No display/GUI needed** on Home Assistant system
- ✅ **No ChromeDriver installation** required
- ✅ **Works in headless environments** (Docker, VMs, cloud instances)
- ✅ **Cross-platform compatible** (ARM64, x86_64, all architectures)
- ✅ **Secure**: No credentials stored, only session cookies

## System Requirements & Compatibility

This integration uses **user-redirect authentication** and **works on all Home Assistant deployment types** without requiring any special setup:

### ✅ **Home Assistant OS**
- **Status**: Fully supported
- **Requirements**: None
- **Setup**: Install → Configure → Complete MitID in your browser

### ✅ **Home Assistant Container (Docker)**
- **Status**: Fully supported
- **Requirements**: None (no browser needed on container)
- **Setup**: Install → Configure → Complete MitID in your browser

### ✅ **Home Assistant Core (Python venv)**
- **Status**: Fully supported
- **Requirements**: None (no browser installation needed)
- **Setup**: Install → Configure → Complete MitID in your browser

### ✅ **Home Assistant Cloud & Hosted Solutions**
- **Status**: Fully supported
- **Requirements**: None
- **Setup**: Install → Configure → Complete MitID in your browser

### ✅ **Headless/Server Environments**
- **Status**: Fully supported
- **Requirements**: None (authenticate using any device with browser)
- **Setup**: Use your phone/computer browser for authentication

### 🎯 **Universal Compatibility**
- **All architectures**: x86_64, ARM64, ARMv7, etc.
- **All deployment types**: OS, Container, Core, Supervisor, etc.
- **All environments**: GUI, headless, cloud, local, etc.
- **Authentication device**: Any device with web browser (phone, tablet, computer)

### 🚨 **Troubleshooting**

| Issue | Solution |
|-------|----------|
| "Cookies required" | Make sure to provide session cookies from your browser |
| "Invalid cookies" | Check cookie format - should be valid JSON like `{"name": "value"}` |
| "Authentication failed" | Re-authenticate in browser and get fresh cookies |
| "Invalid JSON" | Ensure cookies are in proper JSON format with quotes around keys and values |
| MitID login fails | Try different MitID method (QR code, code reader, etc.) |
| Portal doesn't load | Wait a moment after MitID login for Aula portal to fully load |

**Getting Cookies Help:**
1. **Chrome/Edge**: F12 → Application → Storage → Cookies → aula.dk
2. **Firefox**: F12 → Storage → Cookies → aula.dk
3. **Safari**: Develop → Web Inspector → Storage → Cookies
4. **Mobile**: Use desktop browser for easier cookie extraction

### Known issues

## Support
Join our Discord https://discord.gg/SnfRg3DWG6 and feel free to ask in #homeassistant
