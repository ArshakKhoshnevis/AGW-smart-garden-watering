#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include "secrets.h"

#ifndef AGW_SERVER_URL
#error "Set AGW_SERVER_URL in your local, untracked secrets.h"
#endif
#ifndef AGW_WIFI_SSID
#error "Set AGW_WIFI_SSID in your local, untracked secrets.h"
#endif
#ifndef AGW_WIFI_PASSWORD
#error "Set AGW_WIFI_PASSWORD in your local, untracked secrets.h"
#endif
#ifndef AGW_DEVICE_TOKEN
#error "Set AGW_DEVICE_TOKEN in your local, untracked secrets.h"
#endif
#ifndef AGW_MAX_PUMP_RUN_MS
#error "Set AGW_MAX_PUMP_RUN_MS in your local, untracked secrets.h"
#endif

// Active-high pump outputs, based on the current prototype wiring.
const int pumpPins[4] = {18, 19, 21, 22};
const int ledPins[4] = {23, 25, 26, 27};
const int soilPins[4] = {32, 33, 34, 35};
const int wet[4] = {2000, 2000, 2000, 2000};
const int dry[4] = {3300, 3000, 3000, 3000};

constexpr unsigned long POLL_INTERVAL_MS = 5000UL;
constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 30000UL;
constexpr unsigned long HTTP_TIMEOUT_MS = 8000UL;
constexpr unsigned long MAX_PUMP_RUN_MS = AGW_MAX_PUMP_RUN_MS;

WiFiClientSecure tlsClient;
bool pumpStates[4] = {false, false, false, false};
unsigned long pumpStartedAt[4] = {0, 0, 0, 0};
unsigned long lastPollAt = 0;
unsigned long lastWifiAttemptAt = 0;
bool clockReady = false;

void setPumpOutput(int index, bool on) {
    pumpStates[index] = on;
    digitalWrite(pumpPins[index], on ? HIGH : LOW);
    digitalWrite(ledPins[index], on ? HIGH : LOW);
}

void enforceLocalRunLimits() {
    const unsigned long now = millis();
    for (int i = 0; i < 4; i++) {
        if (pumpStates[i] && now - pumpStartedAt[i] >= MAX_PUMP_RUN_MS) {
            setPumpOutput(i, false);
            pumpStartedAt[i] = 0;
            Serial.printf("Pump %d stopped by the local 90-minute safety limit.\n", i + 1);
        }
    }
}

bool syncClock() {
    configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
    struct tm timeInfo;
    clockReady = getLocalTime(&timeInfo, 10000);
    if (!clockReady) {
        Serial.println("Could not set the clock; HTTPS requests are paused until TLS certificates can be checked.");
    }
    return clockReady;
}

bool connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) {
        if (!clockReady) {
            syncClock();
        }
        return clockReady;
    }

    WiFi.mode(WIFI_STA);
    WiFi.begin(AGW_WIFI_SSID, AGW_WIFI_PASSWORD);
    const unsigned long attemptStarted = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - attemptStarted < 15000UL) {
        enforceLocalRunLimits();
        delay(100);
    }

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("Wi-Fi unavailable; will retry.");
        return false;
    }

    Serial.println("Wi-Fi connected.");
    clockReady = syncClock();
    return clockReady;
}

int readMoisture(int index) {
    int value = analogRead(soilPins[index]);
    value = constrain(value, wet[index], dry[index]);
    return map(value, wet[index], dry[index], 100, 0);
}

void pollServer() {
    if (WiFi.status() != WL_CONNECTED || !clockReady) {
        return;
    }

    StaticJsonDocument<256> requestDocument;
    JsonArray soil = requestDocument.createNestedArray("soil");
    for (int i = 0; i < 4; i++) {
        soil.add(readMoisture(i));
    }

    String body;
    serializeJson(requestDocument, body);

    HTTPClient http;
    http.setConnectTimeout(HTTP_TIMEOUT_MS);
    http.setTimeout(HTTP_TIMEOUT_MS);
    if (!http.begin(tlsClient, String(AGW_SERVER_URL) + "/api-sensors")) {
        Serial.println("Could not initialize the HTTPS request.");
        return;
    }

    http.addHeader("Content-Type", "application/json");
    http.addHeader("Authorization", String("Bearer ") + AGW_DEVICE_TOKEN);
    const int httpCode = http.POST(body);

    if (httpCode == HTTP_CODE_OK) {
        StaticJsonDocument<256> responseDocument;
        DeserializationError error = deserializeJson(responseDocument, http.getString());
        if (!error) {
            for (int i = 0; i < 4; i++) {
                const String key = String("pump") + String(i + 1);
                const bool requestedState = responseDocument[key] | false;

                if (requestedState && !pumpStates[i]) {
                    pumpStartedAt[i] = millis();
                    setPumpOutput(i, true);
                } else if (!requestedState && pumpStates[i]) {
                    setPumpOutput(i, false);
                    pumpStartedAt[i] = 0;
                }
            }
        } else {
            Serial.println("Invalid response from the server; keeping current outputs until the local cutoff.");
        }
    } else {
        Serial.printf("Server request failed (HTTP %d); keeping current outputs until the local cutoff.\n", httpCode);
    }

    http.end();
}

void setup() {
    Serial.begin(115200);

    // Drive every active-high relay output OFF as early as possible after boot.
    for (int i = 0; i < 4; i++) {
        digitalWrite(pumpPins[i], LOW);
        pinMode(pumpPins[i], OUTPUT);
        digitalWrite(ledPins[i], LOW);
        pinMode(ledPins[i], OUTPUT);
        pumpStates[i] = false;
        pumpStartedAt[i] = 0;
    }

    tlsClient.setCACert(AGW_ROOT_CA_CERTIFICATE);
    connectWiFi();
    lastWifiAttemptAt = millis();
    lastPollAt = millis() - POLL_INTERVAL_MS;
}

void loop() {
    enforceLocalRunLimits();

    const unsigned long now = millis();
    if (WiFi.status() != WL_CONNECTED && now - lastWifiAttemptAt >= WIFI_RETRY_INTERVAL_MS) {
        lastWifiAttemptAt = now;
        clockReady = false;
        connectWiFi();
    } else if (WiFi.status() == WL_CONNECTED && !clockReady) {
        clockReady = syncClock();
    }

    if (WiFi.status() == WL_CONNECTED && clockReady && now - lastPollAt >= POLL_INTERVAL_MS) {
        lastPollAt = now;
        pollServer();
    }

    delay(20);
}
