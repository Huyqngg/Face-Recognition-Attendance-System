#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ESP32Servo.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include "config.h"

constexpr int PIR_PIN = 14;
constexpr int SERVO_PIN = 13;
constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 22;
constexpr uint32_t OPEN_TIME_MS = 8000;
constexpr uint32_t CLOSE_TIME_MS = 1000;
constexpr uint32_t FAILURE_TIME_MS = 5000;

Adafruit_SSD1306 display(128, 64, &Wire, -1);
WebServer server(80);
Servo doorServo;
QueueHandle_t servoQueue = nullptr;
SemaphoreHandle_t oledMutex = nullptr;
bool displayReady = false;

enum class CommandType : uint8_t { OPEN, FAILURE };
struct ControlCommand {
  CommandType type;
  char name[64];
};

void showMessage(const char *heading, const char *message) {
  if (!displayReady || xSemaphoreTake(oledMutex, pdMS_TO_TICKS(100)) != pdTRUE) return;
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println(heading);
  display.setTextSize(2);
  display.setCursor(0, 20);
  display.println(message);
  display.display();
  xSemaphoreGive(oledMutex);
}

void clearScreen() {
  if (!displayReady || xSemaphoreTake(oledMutex, pdMS_TO_TICKS(100)) != pdTRUE) return;
  display.clearDisplay();
  display.display();
  xSemaphoreGive(oledMutex);
}

void handleCheckPIR() {
  server.send(200, "text/plain", digitalRead(PIR_PIN) == HIGH ? "1" : "0");
}

void handleOpen() {
  ControlCommand command = {};
  command.type = CommandType::OPEN;
  String name = server.hasArg("name") ? server.arg("name") : "Guest";
  name.toCharArray(command.name, sizeof(command.name));
  if (xQueueSend(servoQueue, &command, 0) != pdTRUE) {
    server.send(503, "text/plain", "QUEUE_FULL");
    return;
  }
  server.send(200, "text/plain", "CMD_SENT");
}

void handleFail() {
  ControlCommand command = {};
  command.type = CommandType::FAILURE;
  if (xQueueSend(servoQueue, &command, 0) != pdTRUE) {
    server.send(503, "text/plain", "QUEUE_FULL");
    return;
  }
  server.send(200, "text/plain", "FAIL_SENT");
}

void handleScan() {
  showMessage("HE THONG:", "DANG QUET...");
  server.send(200, "text/plain", "OK");
}

void taskServoControl(void *) {
  ControlCommand command;
  for (;;) {
    if (xQueueReceive(servoQueue, &command, portMAX_DELAY) != pdTRUE) continue;
    if (command.type == CommandType::FAILURE) {
      showMessage("CANH BAO:", "KHONG NHAN DIEN");
      vTaskDelay(pdMS_TO_TICKS(FAILURE_TIME_MS));
    } else {
      showMessage("XIN CHAO:", command.name);
      doorServo.attach(SERVO_PIN);
      doorServo.write(180);
      vTaskDelay(pdMS_TO_TICKS(OPEN_TIME_MS));
      doorServo.write(0);
      vTaskDelay(pdMS_TO_TICKS(CLOSE_TIME_MS));
      doorServo.detach();
    }
    clearScreen();
  }
}

void setup() {
  Serial.begin(115200);
  servoQueue = xQueueCreate(5, sizeof(ControlCommand));
  oledMutex = xSemaphoreCreateMutex();
  if (servoQueue == nullptr || oledMutex == nullptr) {
    Serial.println("Cannot allocate FreeRTOS queue/mutex.");
    while (true) delay(1000);
  }

  Wire.begin(SDA_PIN, SCL_PIN);
  displayReady = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (!displayReady) displayReady = display.begin(SSD1306_SWITCHCAPVCC, 0x3D);
  if (!displayReady) Serial.println("OLED unavailable; continuing without display.");
  showMessage("HE THONG:", "KHOI TAO...");
  pinMode(PIR_PIN, INPUT);
  doorServo.attach(SERVO_PIN);
  doorServo.write(0);
  delay(500);
  doorServo.detach();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
  }

  if (xTaskCreatePinnedToCore(taskServoControl, "ServoTask", 8192, nullptr, 1, nullptr, 1) != pdPASS) {
    Serial.println("Cannot start servo task.");
    while (true) delay(1000);
  }
  server.on("/check_pir", HTTP_GET, handleCheckPIR);
  server.on("/scan", HTTP_GET, handleScan);
  server.on("/open", HTTP_GET, handleOpen);
  server.on("/fail", HTTP_GET, handleFail);
  server.begin();
  Serial.print("\nController IP: ");
  Serial.println(WiFi.localIP());
  showMessage("HE THONG:", "SAN SANG");
}

void loop() {
  server.handleClient();
  delay(2);
}
