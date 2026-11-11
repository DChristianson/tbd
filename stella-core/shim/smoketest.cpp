// Smoke test, not the real shim: proves the vendored headless Stella core
// actually compiles, links, loads a ROM with no catalog/MD5 gate, and that
// the direct emucore accessors (System::cycles(), TIA::registerValue()) read
// back real values. See docs/plan/stella-core-integration.md.
//
// Usage: stella_core_smoketest <path-to-rom> <num-frames>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

#include "StellaLIBRETRO.hxx"

// TIA write-register address for COLUBK, same value as vcs.h's COLUBK
// equate (0009) vendored at tests/fixtures/vendor/atari2600/vcs.h.
constexpr uInt8 COLUBK_ADDR = 0x09;

// A real libretro frontend (RetroArch) provides this — it reads ROM content
// through whatever sandboxed storage the platform has. We have an ordinary
// filesystem and already hold the ROM bytes in `stella` (set via setROM()
// below), so this just copies from there, matching the reference
// implementation in Stella's own os/libretro/libretro.cxx.
static StellaLIBRETRO stella;

uInt32 libretro_read_rom(void* data) {
  std::memcpy(data, stella.getROM(), stella.getROMSize());
  return stella.getROMSize();
}

int main(int argc, char** argv) {
  if (argc < 3) {
    std::fprintf(stderr, "usage: %s <rom> <num_frames>\n", argv[0]);
    return 2;
  }
  const char* romPath = argv[1];
  const int numFrames = std::atoi(argv[2]);

  std::ifstream in(romPath, std::ios::binary);
  if (!in) {
    std::fprintf(stderr, "could not open ROM: %s\n", romPath);
    return 2;
  }
  std::vector<char> rom((std::istreambuf_iterator<char>(in)),
                         std::istreambuf_iterator<char>());

  stella.setROM(romPath, rom.data(), rom.size());
  if (!stella.create(/*logging=*/false)) {
    std::fprintf(stderr, "create() failed — console construction rejected the ROM\n");
    return 1;
  }

  System& system = stella.osystem().console().system();
  TIA& tia = stella.osystem().console().tia();

  for (int i = 0; i < numFrames; ++i) {
    const uInt64 cyclesBefore = system.cycles();
    stella.runFrame();
    const uInt64 cyclesAfter = system.cycles();

    const uInt8 colubk = tia.registerValue(COLUBK_ADDR);
    std::printf(
        "frame=%d cycles_this_frame=%llu total_cycles=%llu colubk=0x%02x scanlines=%u\n",
        i, static_cast<unsigned long long>(cyclesAfter - cyclesBefore),
        static_cast<unsigned long long>(cyclesAfter), colubk, tia.scanlinesLastFrame());
  }

  return 0;
}
