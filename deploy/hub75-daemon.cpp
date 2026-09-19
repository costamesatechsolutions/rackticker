// Privileged electrical refresh companion for RackTicker's unprivileged app.
#include "led-matrix.h"

#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <vector>

namespace {
constexpr int kWidth = 128;
constexpr int kHeight = 32;
constexpr size_t kFrameBytes = kWidth * kHeight * 3;
constexpr char kSocketPath[] = "/run/rackticker/matrix.sock";
volatile std::sig_atomic_t running = 1;
int socket_fd = -1;

void stop(int) {
  running = 0;
  if (socket_fd >= 0) close(socket_fd);
}
}  // namespace

int main(int argc, char *argv[]) {
  // Defaults match the reference build: two 64×32 panels chained on a
  // "regular"-pinout HAT. Any standard --led-* flag (see --help or the
  // rpi-rgb-led-matrix README) overrides them, usually via
  // /etc/rackticker/matrix.conf, so other panels and HATs need no rebuild.
  rgb_matrix::RGBMatrix::Options options;
  options.rows = 32;
  options.cols = 64;
  options.chain_length = 2;
  options.parallel = 1;
  options.hardware_mapping = "regular";
  options.brightness = 5;

  rgb_matrix::RuntimeOptions runtime;
  runtime.gpio_slowdown = 2;
  runtime.drop_privileges = 1;
  runtime.drop_priv_user = "rackticker";
  runtime.drop_priv_group = "rackticker";
  if (!rgb_matrix::ParseOptionsFromFlags(&argc, &argv, &options, &runtime)) {
    rgb_matrix::PrintMatrixFlags(stderr, options, runtime);
    return 1;
  }
  auto *matrix = rgb_matrix::RGBMatrix::CreateFromOptions(options, runtime);
  if (!matrix) {
    std::fprintf(stderr, "Unable to initialize HUB75 matrix\n");
    return 1;
  }
  if (matrix->width() != kWidth || matrix->height() != kHeight) {
    std::fprintf(stderr, "RackTicker renders %dx%d, but the matrix options describe %dx%d; "
                 "check --led-rows, --led-cols, --led-chain and --led-parallel\n",
                 kWidth, kHeight, matrix->width(), matrix->height());
    delete matrix;
    return 1;
  }
  auto *canvas = matrix->CreateFrameCanvas();

  socket_fd = socket(AF_UNIX, SOCK_DGRAM, 0);
  if (socket_fd < 0) {
    std::perror("socket");
    delete matrix;
    return 1;
  }
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  std::strncpy(address.sun_path, kSocketPath, sizeof(address.sun_path) - 1);
  unlink(kSocketPath);
  if (bind(socket_fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
    std::perror("bind");
    close(socket_fd);
    delete matrix;
    return 1;
  }
  chmod(kSocketPath, 0660);
  std::signal(SIGINT, stop);
  std::signal(SIGTERM, stop);

  constexpr int kChunks = 8;
  constexpr size_t kChunkBytes = kFrameBytes / kChunks;
  constexpr size_t kHeaderBytes = 10;
  std::vector<uint8_t> packet(kHeaderBytes + kChunkBytes);
  std::vector<uint8_t> frame(kFrameBytes);
  uint32_t pending_sequence = 0;
  uint8_t pending_brightness = 5;
  uint8_t received = 0;
  int applied_brightness = -1;
  while (running) {
    const ssize_t count = recv(socket_fd, packet.data(), packet.size(), 0);
    if (count < 0) {
      if (errno == EINTR || errno == EBADF) continue;
      std::perror("recv");
      break;
    }
    if (static_cast<size_t>(count) != packet.size() ||
        std::memcmp(packet.data(), "RTK1", 4) != 0 || packet[9] >= kChunks) continue;
    const uint32_t sequence = (static_cast<uint32_t>(packet[4]) << 24) |
                              (static_cast<uint32_t>(packet[5]) << 16) |
                              (static_cast<uint32_t>(packet[6]) << 8) | packet[7];
    if (sequence != pending_sequence) {
      pending_sequence = sequence;
      pending_brightness = packet[8];
      received = 0;
    }
    const int chunk = packet[9];
    std::memcpy(frame.data() + chunk * kChunkBytes,
                packet.data() + kHeaderBytes, kChunkBytes);
    received |= static_cast<uint8_t>(1u << chunk);
    if (received != 0xFF) continue;
    const bool blank = pending_brightness == 0;
    const int requested_brightness = blank ? 1 : pending_brightness;
    // Reprogramming PWM brightness for every transition frame can produce a
    // brief full-panel pulse on some adapter/panel combinations. Brightness is
    // an electrical setting, so only touch it when the requested value changes.
    if (requested_brightness != applied_brightness) {
      matrix->SetBrightness(requested_brightness);
      applied_brightness = requested_brightness;
    }
    const uint8_t *pixel = frame.data();
    for (int y = 0; y < kHeight; ++y) {
      for (int x = 0; x < kWidth; ++x, pixel += 3) {
        canvas->SetPixel(x, y, blank ? 0 : pixel[0], blank ? 0 : pixel[1], blank ? 0 : pixel[2]);
      }
    }
    canvas = matrix->SwapOnVSync(canvas);
    received = 0;
  }

  matrix->Clear();
  unlink(kSocketPath);
  delete matrix;
  return 0;
}
