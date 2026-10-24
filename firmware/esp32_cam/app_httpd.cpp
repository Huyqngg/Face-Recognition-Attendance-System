// Minimal HTTP camera service using the Arduino-ESP32 / esp32-camera APIs.
// Port 80 serves JPEG snapshots; a separate port-81 task serves MJPEG.
#include "esp_camera.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>

static httpd_handle_t snapshotServer = nullptr;
static httpd_handle_t streamServer = nullptr;

static esp_err_t homeHandler(httpd_req_t *request) {
  httpd_resp_set_type(request, "text/plain");
  return httpd_resp_send(request, "ESP32-CAM ready. JPEG: /capture (port 80). MJPEG: /stream (port 81).", HTTPD_RESP_USE_STRLEN);
}

static esp_err_t captureHandler(httpd_req_t *request) {
  camera_fb_t *frame = esp_camera_fb_get();
  if (frame == nullptr) {
    return httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Camera capture failed");
  }
  httpd_resp_set_type(request, "image/jpeg");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  esp_err_t result = httpd_resp_send(request, reinterpret_cast<const char *>(frame->buf), frame->len);
  esp_camera_fb_return(frame);
  return result;
}

static esp_err_t streamHandler(httpd_req_t *request) {
  esp_err_t result = httpd_resp_set_type(request, "multipart/x-mixed-replace;boundary=frame");
  if (result != ESP_OK) return result;
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  while (true) {
    camera_fb_t *frame = esp_camera_fb_get();
    if (frame == nullptr) return ESP_FAIL;
    char header[128];
    const int count = snprintf(header, sizeof(header), "\r\n--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", static_cast<unsigned>(frame->len));
    result = httpd_resp_send_chunk(request, header, count);
    if (result == ESP_OK) {
      result = httpd_resp_send_chunk(request, reinterpret_cast<const char *>(frame->buf), frame->len);
    }
    esp_camera_fb_return(frame);
    if (result != ESP_OK) return result;
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

bool startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.stack_size = 8192;
  if (httpd_start(&snapshotServer, &config) != ESP_OK) return false;
  httpd_uri_t home = {};
  home.uri = "/";
  home.method = HTTP_GET;
  home.handler = homeHandler;
  httpd_uri_t capture = {};
  capture.uri = "/capture";
  capture.method = HTTP_GET;
  capture.handler = captureHandler;
  if (httpd_register_uri_handler(snapshotServer, &home) != ESP_OK || httpd_register_uri_handler(snapshotServer, &capture) != ESP_OK) {
    httpd_stop(snapshotServer);
    snapshotServer = nullptr;
    return false;
  }

  config.server_port = 81;
  config.ctrl_port += 1;
  if (httpd_start(&streamServer, &config) != ESP_OK) {
    httpd_stop(snapshotServer);
    snapshotServer = nullptr;
    return false;
  }
  httpd_uri_t stream = {};
  stream.uri = "/stream";
  stream.method = HTTP_GET;
  stream.handler = streamHandler;
  if (httpd_register_uri_handler(streamServer, &stream) != ESP_OK) {
    httpd_stop(streamServer);
    httpd_stop(snapshotServer);
    streamServer = snapshotServer = nullptr;
    return false;
  }
  return true;
}
