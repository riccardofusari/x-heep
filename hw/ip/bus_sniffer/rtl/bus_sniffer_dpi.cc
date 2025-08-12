// sniffer_dpi.cc
#include <svdpi.h>
#include <stdint.h>
#include <stdio.h>
#include <atomic>
#include <thread>
#include <chrono>
#include <stdlib.h>

struct Frame { uint32_t w[4]; };

static const uint32_t RB_SIZE = 1u << 16;   // 65536 frame (~1 MiB)
static Frame* ring = nullptr;
static std::atomic<uint32_t> head{0}; // producer (SV)
static std::atomic<uint32_t> tail{0}; // consumer (C)
static std::atomic<bool> running{false};
static FILE* fout = nullptr;

static inline uint32_t rb_used(uint32_t h, uint32_t t){ return (h - t) & (RB_SIZE - 1); }
static inline uint32_t rb_free(uint32_t h, uint32_t t){ return (RB_SIZE - 1) - rb_used(h,t); }

static void start_consumer() {
  if (running.load()) return;
  ring = (Frame*)malloc(sizeof(Frame)*RB_SIZE);
  fout = fopen("sniffer_frames.bin","wb");
  running.store(true, std::memory_order_release);
  std::thread([](){
    while (running.load(std::memory_order_acquire)) {
      uint32_t t = tail.load(std::memory_order_relaxed);
      uint32_t h = head.load(std::memory_order_acquire);
      while (t != h) {
        fwrite(&ring[t], sizeof(Frame), 1, fout);
        t = (t + 1) & (RB_SIZE - 1);
      }
      tail.store(t, std::memory_order_release);
      fflush(fout);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  }).detach();
}

extern "C" int sniffer_dpi_push(int stream_id, int nwords,
                                unsigned w0, unsigned w1, unsigned w2, unsigned w3)
{
  (void)stream_id;
  if (nwords != 4) return 0;
  if (!ring) start_consumer();

  uint32_t h = head.load(std::memory_order_relaxed);
  uint32_t t = tail.load(std::memory_order_acquire);
  if (rb_free(h,t) == 0) return 0;     // backpressure: no pop lato SV

  ring[h].w[0] = w0;  // DATA0 = MSW
  ring[h].w[1] = w1;
  ring[h].w[2] = w2;
  ring[h].w[3] = w3;

  head.store((h + 1) & (RB_SIZE - 1), std::memory_order_release);
  return 1;
}

extern "C" void sniffer_dpi_close(void) {
  running.store(false, std::memory_order_release);
  std::this_thread::sleep_for(std::chrono::milliseconds(5));
  if (fout) { fclose(fout); fout = nullptr; }
  if (ring) { free(ring); ring = nullptr; }
}
