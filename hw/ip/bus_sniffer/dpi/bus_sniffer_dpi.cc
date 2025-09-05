// hw/ip/bus_sniffer/dpi/bus_sniffer_dpi.cc
#include <svdpi.h>
#include <stdint.h>
#include <stdio.h>
#include <atomic>
#include <thread>
#include <chrono>
#include <stdlib.h>

struct Frame { uint32_t w[4]; };

// Ring SPSC
static const uint32_t RB_SIZE = 1u << 16;   // 65536 frame
static Frame* ring = nullptr;
static std::atomic<uint32_t> head{0}; // producer (SV)
static std::atomic<uint32_t> tail{0}; // consumer (C)
static std::atomic<bool> running{false};

// Output
static FILE* fbin = nullptr;   // binario 16B/frame
static FILE* fcsv = nullptr;   // CSV leggibile

// Console print (opzionale, controllata da env-var)
static bool     print_enable = false;
static unsigned print_every  = 1;    // stampa 1 ogni N frame
static unsigned print_count  = 0;

static inline uint32_t rb_used(uint32_t h, uint32_t t){ return (h - t) & (RB_SIZE - 1); }
static inline uint32_t rb_free(uint32_t h, uint32_t t){ return (RB_SIZE - 1) - rb_used(h,t); }

// Decode dei 128 bit (DATA0=MSW) in campi
static inline void decode_to_fields(const Frame& fr,
                                    uint32_t &src,
                                    uint32_t &req_ts,
                                    uint32_t &resp_ts,
                                    uint32_t &addr,
                                    uint32_t &data,
                                    uint32_t &be,
                                    uint32_t &we,
                                    uint32_t &valid,
                                    uint32_t &gnt)
{
  const uint32_t w0 = fr.w[0]; // 127..96
  const uint32_t w1 = fr.w[1]; // 95..64
  const uint32_t w2 = fr.w[2]; // 63..32
  const uint32_t w3 = fr.w[3]; // 31..0

  src     = (w0 >> 28) & 0xF;
  req_ts  = ((w0 & 0x0FFFFFFFu) << 4) | (w1 >> 28);
  resp_ts = (w1 >> 12) & 0xFFFFu;
  addr    = ((w1 & 0xFFFu) << 20) | (w2 >> 12);
  data    = ((w2 & 0xFFFu) << 20) | (w3 >> 12);
  be      = (w3 >> 8) & 0xFu;
  we      = (w3 >> 7) & 0x1u;
  valid   = (w3 >> 6) & 0x1u;
  gnt     = (w3 >> 5) & 0x1u;
}

static void start_consumer() {
  if (running.load()) return;

  ring = (Frame*)malloc(sizeof(Frame)*RB_SIZE);
  if (!ring) { fprintf(stderr, "[sniffer_dpi] malloc ring failed\n"); abort(); }

  fbin = fopen("sniffer_frames.bin","wb");
  if (!fbin) { fprintf(stderr, "[sniffer_dpi] fopen bin failed\n"); abort(); }

  fcsv = fopen("sniffer_frames.csv","w");
  if (!fcsv) { fprintf(stderr, "[sniffer_dpi] fopen csv failed\n"); abort(); }
  fprintf(fcsv, "src,req_ts,resp_ts,address,data,be,we,valid,gnt\n");

  // buffer grandi per meno syscalls
  setvbuf(fbin, NULL, _IOFBF, 1<<20);
  setvbuf(fcsv, NULL, _IOFBF, 1<<20);

  // Stampa a video opzionale
  const char* p  = getenv("SNIFFER_PRINT");          // "1" per abilitare
  const char* pe = getenv("SNIFFER_PRINT_EVERY");    // es. "100"
  if (p && *p && p[0] != '0') print_enable = true;
  if (pe) { unsigned v = (unsigned)atoi(pe); if (v) print_every = v; }

  running.store(true, std::memory_order_release);

  std::thread([](){
    while (running.load(std::memory_order_acquire)) {
      uint32_t t = tail.load(std::memory_order_relaxed);
      uint32_t h = head.load(std::memory_order_acquire);
      while (t != h) {
        const Frame& fr = ring[t];

        // 1) Binario
        fwrite(&fr, sizeof(Frame), 1, fbin);

        // 2) CSV
        uint32_t src, req_ts, resp_ts, addr, data, be, we, valid, gnt;
        decode_to_fields(fr, src, req_ts, resp_ts, addr, data, be, we, valid, gnt);
        fprintf(fcsv, "%u,%u,%u,0x%08X,0x%08X,%X,%u,%u,%u\n",
                src, req_ts, resp_ts, addr, data, be, we, valid, gnt);

        // 3) Console (opzionale)
        if (print_enable) {
          if ((++print_count % print_every) == 0) {
            fprintf(stderr,
              "src=%u ts=%08X/%04X addr=%08X data=%08X be=%X we=%u v%u g%u\n",
              src, req_ts, resp_ts, addr, data, be, we, valid, gnt);
          }
        }

        t = (t + 1) & (RB_SIZE - 1);
      }
      tail.store(t, std::memory_order_release);

      fflush(fbin);
      fflush(fcsv);
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
  if (rb_free(h,t) == 0) return 0; // backpressure

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

  if (fbin) { fflush(fbin); fclose(fbin); fbin = nullptr; }
  if (fcsv) { fflush(fcsv); fclose(fcsv); fcsv = nullptr; }
  if (ring) { free(ring); ring = nullptr; }
}
