#include "stella_core.hxx"

#include <cstring>
#include <fstream>

#include "M6502.hxx"
#include "M6532.hxx"
#include "System.hxx"
#include "tia/TIA.hxx"

// A real libretro frontend (RetroArch) provides this — see
// shim/smoketest.cpp for the longer explanation. Exactly one StellaCore is
// ever active per process, so a single global pointer is enough.
namespace {
StellaLIBRETRO* g_active = nullptr;
}

uInt32 libretro_read_rom(void* data) {
  std::memcpy(data, g_active->getROM(), g_active->getROMSize());
  return g_active->getROMSize();
}

StellaCore::StellaCore() { g_active = &stella_; }

std::string StellaCore::loadRom(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) return "could not open ROM file: " + path;

  romBytes_.assign(std::istreambuf_iterator<char>(in),
                    std::istreambuf_iterator<char>());
  if (romBytes_.empty()) return "ROM file is empty: " + path;

  stella_.setROM(path.c_str(), romBytes_.data(), romBytes_.size());
  // StellaLIBRETRO::create() discards the detailed error string
  // OSystem::createConsole() produces internally — a known limitation,
  // fine for now (Task 1 et al. use known-good ROMs); worth revisiting if
  // a later task needs to distinguish *why* a ROM was rejected.
  if (!stella_.create(/*logging=*/false)) {
    loaded_ = false;
    return "console construction failed (invalid or unsupported ROM?)";
  }
  loaded_ = true;
  frameNumber_ = 0;
  return "";
}

void StellaCore::reset() {
  stella_.reset();
  frameNumber_ = 0;
}

void StellaCore::stepFrame(int n) {
  for (int i = 0; i < n; ++i) {
    stella_.runFrame();
    ++frameNumber_;
  }
}

void StellaCore::stepScanline(int n) {
  TIA& tia = stella_.osystem().console().tia();
  for (int i = 0; i < n; ++i) tia.updateScanline();
}

uint8_t StellaCore::readMemory(uint16_t addr) {
  return stella_.osystem().console().system().peekOob(addr);
}

void StellaCore::writeMemory(uint16_t addr, uint8_t value) {
  stella_.osystem().console().system().pokeOob(addr, value);
}

std::array<uint8_t, 128> StellaCore::getRam() {
  std::array<uint8_t, 128> out{};
  const uInt8* ram = stella_.osystem().console().system().m6532().getRAM();
  std::memcpy(out.data(), ram, 128);
  return out;
}

void StellaCore::setRam(uint8_t addr, uint8_t value) {
  // RIOT RAM lives at 0x80-0xFF in the real address space — same
  // convention StellaLIBRETRO::runFrame() itself uses.
  stella_.osystem().console().system().m6532().poke(
      static_cast<uInt16>(addr) | 0x80, value);
}

CpuState StellaCore::getCpuState() {
  System& system = stella_.osystem().console().system();
  M6502& cpu = system.m6502();
  return CpuState{cpu.getPC(), cpu.getA(), cpu.getX(),
                  cpu.getY(), cpu.getSP(), system.cycles()};
}

TiaState StellaCore::getTiaState() {
  TIA& tia = stella_.osystem().console().tia();
  return TiaState{tia.scanlines(), tia.scanlinesLastFrame(), frameNumber_};
}

std::array<uint8_t, 64> StellaCore::getTiaRegisters() {
  std::array<uint8_t, 64> out{};
  TIA& tia = stella_.osystem().console().tia();
  for (uint8_t i = 0; i < 64; ++i) out[i] = tia.registerValue(i);
  return out;
}
