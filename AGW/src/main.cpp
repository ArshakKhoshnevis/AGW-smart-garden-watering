#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <FS.h>
#include <LittleFS.h>
#include "secrets.h"
#include <AESLib.h>
#include <base64.h>

#define SERVER_URL "https://your-server.example.com"

String esp32token = "";
String ssid = "", wifiPass = "", webUsername, webPassword;
AESLib aesLib;

// const int pumpPins[4] = {2, 4, 5, 18};
const int pumpPins[4] = {18, 19, 21, 22};
const int ledPins[4] = {23, 25, 26, 27};
const int soilPins[4] = {32, 33, 34, 35};
const int checkPins[8] = {18, 19, 21, 22, 23, 25, 26, 27};
const int wet[4] = {2000, 2000, 2000, 2000};
const int dry[4] = {3300, 3000, 3000, 3000};
unsigned long tokenExpires = 0, expireTime = 600 * 1000, start;
bool pumpStates[4];
int soilVals[4];

void loadCredentials() {
    if(!LittleFS.begin()) {
        Serial.println("Failed to mount filesystem");
        return;
    }
    File file = LittleFS.open("/credentials.json", "r");

    if(!file) {
        Serial.println("Failed to open credentials file");
        return;
    }

    StaticJsonDocument<250> doc;
    DeserializationError error = deserializeJson(doc, file);

    if(error) {
        Serial.println("Failed to parse JSON");
        return;
    }

    webUsername = doc["webUsername"].as<String>();
    webPassword = doc["webPassword"].as<String>();
    ssid = "your-ssid";
    wifiPass = "your-pass";

    Serial.println("fourth succsess");

    file.close();
}

bool login() {
    if(WiFi.status() != WL_CONNECTED) {
        Serial.println("Failed to connect");
        return false;
    }

    HTTPClient http;
    http.begin(SERVER_URL "/login-esp32");
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<250> doc;
    doc["username"] = webUsername, doc["password"] = webPassword;

    String body;
    serializeJson(doc, body);

    delay(100);

    int httpCode = http.POST(body);

    delay(100);

    Serial.println("Here");

    if(httpCode == 200) {
        String payload = http.getString();
        StaticJsonDocument<250> res;
        deserializeJson(res, payload);
        // String ct = res["token"].as<String>();
        // char ct_array[128], decrypted[128];
        // ct.toCharArray(ct_array, 128);
        // byte iv[16] = {0};
        // aesLib.decrypt64(ct_array, strlen(ct_array), (byte*)decrypted, SECRET_KEY, 128, iv);
        esp32token = res["token"].as<String>();
        tokenExpires = millis() + expireTime;
        http.end();
        return true;
    }
    http.end();
    return false;
}

inline void refresh_token() {
    if((long)(tokenExpires - millis()) <= 0) {
        delay(1);
        if(login()) {
            tokenExpires = millis() + expireTime;
            Serial.println("Token refreshed! : " + esp32token);
        }
        else {
            Serial.print("Login Failed!!!");
            delay(100);
        }
    }
}

int Get(int id) {
    int val = analogRead(soilPins[id]);
    val = constrain(val, wet[id], dry[id]);
    return map(val, wet[id], dry[id], 100, 0);
}

bool Upd() {
    refresh_token();

    HTTPClient http;
    http.begin(SERVER_URL "/api-sensors");
    http.addHeader("Content-Type", "application/json");
    http.addHeader("Authorization", "Bearer " + esp32token);

    StaticJsonDocument<250> doc;
    for(int i = 0; i < 4; i++)
        doc["soil"][i] = Get(i);

    String body;
    serializeJson(doc, body);

    int httpCode = http.POST(body);
    if(httpCode == 200) {
        String payload = http.getString();
        Serial.println("Data was sent successfully: " + payload);

        StaticJsonDocument<200> res;
        DeserializationError error = deserializeJson(res, payload);
        if (!error) {
            for(int i = 0; i < 4; i++) {
                pumpStates[i] = res["pump" + String(i + 1)].as<bool>();
                digitalWrite(pumpPins[i], pumpStates[i] ? HIGH : LOW);
                digitalWrite(ledPins[i], pumpStates[i] ? HIGH : LOW);
            }
        }

        http.end();
        return true;
    }
    Serial.println("Failed to send data");
    http.end();
    return false;
}

void setup() {
    Serial.begin(115200);

    delay(6000);

    for(int i = 0; i < 4; i++) {
        pumpStates[i] = false;
        pinMode(pumpPins[i], OUTPUT);
        pinMode(ledPins[i], OUTPUT);
        pinMode(checkPins[2 * i], OUTPUT);
        pinMode(checkPins[2 * i + 1], OUTPUT);
    }

    delay(5000);

    loadCredentials();

    Serial.println(ssid + " " + wifiPass);

    WiFi.begin(ssid.c_str(), wifiPass.c_str());

    start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        Serial.println(WiFi.status());

        if (millis() - start > 15000) {
            Serial.println("Failed to connect WiFi");
            return;
        }

        delay(1);
    }

    Serial.println("\nWiFi connected!");

    delay(5000);

    if(login())
        Serial.println("Login successful! Token: " + esp32token);
    else
        Serial.println("Login failed");

    delay(5000);

    start = 5001;
}

void loop() {
    if(millis() - start > 5000) {
        Upd();
        start = millis();
    }
}