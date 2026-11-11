// Our own API surface over StellaLIBRETRO + direct emucore access.
// See docs/plan/stella-core-integration.md for the overall design and
// stella-core/vendor/stella/NOTICE.md for why this reads System/TIA/M6502
// directly instead of Stella's debugger/CpuDebug etc.
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "StellaLIBRETRO.hxx"

struct CpuState {
  uint16_t pc;
  uint8_t a, x, y, sp;
  uint64_t cycles;  // System::cycles() — total 6507 cycles since reset
};

struct TiaState {
  uint32_t scanlines;            // live count for the in-progress frame —
                                  // wraps to 0 right when a new frame starts,
                                  // which is how callers stepping scanline-by-
                                  // scanline (not via stepFrame) detect a
                                  // frame boundary.
  uint32_t scanlinesLastFrame;
  uint32_t frameNumber;  // frames stepped via stepFrame(), counted by us
};

// One loaded ROM per process, for the lifetime of the process — matches how
// tbd.tools.emulator.run() will drive it: launch, load_rom once, issue a
// sequence of step/read commands, exit.
class StellaCore {
 public:
  StellaCore();

  // Empty string on success, error message on failure. Never hard-exits —
  // this is the one requirement ALE's loadROM() didn't meet.
  std::string loadRom(const std::string& path);

  void reset();
  void stepFrame(int n = 1);
  void stepScanline(int n = 1);

  // Full 0x0000-0xFFFF address space, out-of-band (doesn't perturb cycle
  // counts or bus state the way a real CPU access would) — matches the
  // debug-read semantics the design doc wants, not a real bus peek.
  uint8_t readMemory(uint16_t addr);
  void writeMemory(uint16_t addr, uint8_t value);

  // The 128-byte RIOT RAM window, same shape as ALE's getRAM()/setRAM().
  std::array<uint8_t, 128> getRam();
  void setRam(uint8_t addr, uint8_t value);

  CpuState getCpuState();
  TiaState getTiaState();

  // TIA's shadow copy of its 64 write-only registers (COLUBK, etc.) — the
  // thing System::peek can't give you, since those addresses are write-only
  // from the CPU's perspective on real hardware. Index == write address,
  // e.g. COLUBK is index 0x09 (same as vcs.h's COLUBK equate).
  std::array<uint8_t, 64> getTiaRegisters();

  bool loaded() const { return loaded_; }

 private:
  StellaLIBRETRO stella_;
  std::vector<char> romBytes_;
  bool loaded_{false};
  uint32_t frameNumber_{0};
};
