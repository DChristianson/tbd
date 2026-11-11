// stella-core's real entry point: one implementation of Emulator Protocol
// v1 — see docs/design/emulator-protocol.md for the full, backend-agnostic
// command spec (this file just implements it) and
// docs/plan/stella-core-integration.md for why a subprocess rather than an
// in-process Python extension.

#include <fcntl.h>
#include <unistd.h>

#include <cstdio>
#include <iostream>
#include <string>

#include "json.hpp"
#include "stella_core.hxx"

using json = nlohmann::json;

namespace {

// Stella itself writes stray diagnostics straight to stdout during console
// setup (confirmed: "File open/read error", from a properties/settings file
// lookup that doesn't matter here since we use Noop repositories) — fatal
// for a line-oriented protocol sharing that same stream. Mutes real fd 1 for
// the duration of a command, restores it before we write our own response,
// so nothing but our own JSON ever reaches the pipe tbd.tools.emulator reads.
class StdoutMute {
 public:
  StdoutMute() {
    std::fflush(stdout);
    saved_ = dup(STDOUT_FILENO);
    const int devnull = open("/dev/null", O_WRONLY);
    dup2(devnull, STDOUT_FILENO);
    close(devnull);
  }
  ~StdoutMute() {
    std::fflush(stdout);
    dup2(saved_, STDOUT_FILENO);
    close(saved_);
  }

 private:
  int saved_;
};

json ok(json extra = json::object()) {
  extra["ok"] = true;
  return extra;
}

json fail(const std::string& error) {
  return json{{"ok", false}, {"error", error}};
}

json handle(StellaCore& core, const json& req) {
  const std::string cmd = req.value("cmd", "");

  if (cmd == "load_rom") {
    const std::string error = core.loadRom(req.at("path").get<std::string>());
    return error.empty() ? ok() : fail(error);
  }

  if (cmd == "quit") return ok();

  if (!core.loaded()) return fail("no ROM loaded — send load_rom first");

  if (cmd == "reset") {
    core.reset();
    return ok();
  }
  if (cmd == "step_frame") {
    core.stepFrame(req.value("n", 1));
    return ok();
  }
  if (cmd == "step_scanline") {
    core.stepScanline(req.value("n", 1));
    return ok();
  }
  if (cmd == "read_memory") {
    const uint16_t addr = req.at("addr").get<uint16_t>();
    return ok({{"value", core.readMemory(addr)}});
  }
  if (cmd == "write_memory") {
    const uint16_t addr = req.at("addr").get<uint16_t>();
    const uint8_t value = req.at("value").get<uint8_t>();
    core.writeMemory(addr, value);
    return ok();
  }
  if (cmd == "get_ram") {
    const auto ram = core.getRam();
    return ok({{"ram", std::vector<uint8_t>(ram.begin(), ram.end())}});
  }
  if (cmd == "set_ram") {
    const uint8_t addr = req.at("addr").get<uint8_t>();
    const uint8_t value = req.at("value").get<uint8_t>();
    core.setRam(addr, value);
    return ok();
  }
  if (cmd == "get_cpu_state") {
    const CpuState s = core.getCpuState();
    return ok({{"pc", s.pc},
               {"a", s.a},
               {"x", s.x},
               {"y", s.y},
               {"sp", s.sp},
               {"cycles", s.cycles}});
  }
  if (cmd == "get_tia_state") {
    const TiaState s = core.getTiaState();
    return ok({{"scanlines", s.scanlines},
               {"scanlines_last_frame", s.scanlinesLastFrame},
               {"frame_number", s.frameNumber}});
  }
  if (cmd == "get_tia_registers") {
    const auto regs = core.getTiaRegisters();
    return ok({{"registers", std::vector<uint8_t>(regs.begin(), regs.end())}});
  }

  return fail("unknown command: " + cmd);
}

}  // namespace

int main() {
  StellaCore core;
  std::string line;

  while (std::getline(std::cin, line)) {
    if (line.empty()) continue;

    json response;
    bool shouldQuit = false;
    try {
      const json request = json::parse(line);
      {
        StdoutMute mute;
        response = handle(core, request);
      }
      shouldQuit = request.value("cmd", "") == "quit";
    } catch (const std::exception& e) {
      response = fail(std::string("exception: ") + e.what());
    }

    std::cout << response.dump() << std::endl;
    std::cout.flush();

    if (shouldQuit) break;
  }

  return 0;
}
