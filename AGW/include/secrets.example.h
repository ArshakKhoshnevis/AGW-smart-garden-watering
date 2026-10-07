#pragma once

// Copy this file to secrets.h (which is ignored by Git), then fill in values.
// Never commit secrets.h or real Wi-Fi/device credentials.
#define AGW_SERVER_URL "https://your-domain.example"
#define AGW_WIFI_SSID "replace-with-your-wifi-name"
#define AGW_WIFI_PASSWORD "replace-with-your-wifi-password"
#define AGW_DEVICE_TOKEN "replace-with-the-same-random-token-as-the-server"

// Hard maximum for one pump command; keep this at 90 minutes to match the server.
#define AGW_MAX_PUMP_RUN_MS 5400000UL

// Replace the placeholder below with the public root CA certificate that issued
// the HTTPS certificate for AGW_SERVER_URL. This is public certificate material,
// not a private key. Do not use setInsecure().
static const char AGW_ROOT_CA_CERTIFICATE[] = R"AGW_CA(
-----BEGIN CERTIFICATE-----
REPLACE_WITH_THE_ISSUING_ROOT_CA_PEM
-----END CERTIFICATE-----
)AGW_CA";
