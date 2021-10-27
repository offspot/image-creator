# image-creator

Hotspot image creator to build OLIP or Kiwix Offspot off [`base-image`](https://github.com/offspot/base-image).

## Scope

- Validate inputs
- Download base image
- Resize image to match contents
- Download contents into mounted `/data`
- Post-process downloaded contents
- Configure from inputs
- Re-generate SSH server keys
- *Pull* application images
- Prepares JSON config

## Inputs

- Target system (OLIP or Offspot)
- Image name
- Hostname
- domain name
- SSID
- WiFi AP password (if any)
- WiFi Country code
- WiFi channel
- Timezone
- SSH Public keys to add
- VPN configuration (tinc)
- Contents

## App Containers

- **OLIP**
 - API
 - Frontend
 - Stats
 - Controller
- **Offspot**
 - Kiwix-serve
 - WikiFundi (en/fr/es)
 - Aflatoun (en/fr)
 - Surfer
- IPFS daemon
- Captive portal

## data partition


| /data subfolders | Usage |
|---|---|
| `offspot/zim` | Offspot Kiwix serve ZIM files|
| `offspot/wikifundi` | Offspot WikiFundi data |
| `offspot/files` | Offspot Surfer data |
| `offspot/xxx` | Offspot data for other apps |
| `olip` | OLIP data |


## JSON Configurator

JSON config file at `/boot/config.json` is read and parsed on startup by the boot-time config script.
It looks for the following properties. Dotted ones means nested.

Behavior is to adjust configuration only if the property is present. Script will remove property from JSON once applied.

Configurator is also responsible for resizing `/data` partition to device size on first boot but this is not configurable via JSON.

| Property| Type | Usage |
|---|---|---|
| `hostname` | `string` | Pi host name |
| `domain` | `string` | FQDN to answer to on DNS |
| `wifi.ssid` | `string` | WiFi SSID |
| `wifi.password` | `string` | WiFi password (clear). If `null`, auth not required |
| `wifi.country-code` | `string` | ISO-639-2 Country code for WiFI |
| `wifi.channel` | `int` | 1-11 channel for WiFi |
| `timezone` | `string` | Timezone to configure date with |
| `ssh-keys` | `string[]` | List of public keys to add to user |
| `tinc-vpn` | `string` | tinc-VPN configuration |
| `env.all`  | `string[]` | List of `KEY=VALUE` environment variables to pass to **all applications** |
| `env.xxx`  | `string[]` | List of `KEY=VALUE` environment variables to pass **containers matching _xxx_** |