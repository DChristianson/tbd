// Real C++-side encode/decode timing: nlohmann::json (what stella_core
// actually uses today) vs FlatBuffers, for get_cpu_state and
// get_tia_registers responses — the two cases with the most interesting
// Python-side results. Not part of the build; compiled and run manually
// for docs/design/emulator-protocol-flatbuffers.md.
#include <chrono>
#include <cstdio>
#include <string>
#include <vector>

#include "emulator_task1_generated.h"
#include "json.hpp"

using json = nlohmann::json;
using namespace tbd::emulator::fbs;

constexpr int N = 200000;

template <typename Fn>
double time_us(Fn&& fn) {
  const auto start = std::chrono::steady_clock::now();
  for (int i = 0; i < N; ++i) fn();
  const auto elapsed = std::chrono::steady_clock::now() - start;
  return std::chrono::duration<double, std::micro>(elapsed).count() / N;
}

std::string json_encode_cpu_state() {
  json j{{"ok", true}, {"pc", 61440}, {"a", 100}, {"x", 13},
         {"y", 96}, {"sp", 253}, {"cycles", 42962ULL}};
  return j.dump();
}

std::vector<uint8_t> fb_encode_cpu_state() {
  flatbuffers::FlatBufferBuilder builder(64);
  auto body = CreateCpuStateResponse(builder, 61440, 100, 13, 96, 253, 42962ULL);
  auto resp = CreateResponse(builder, true, ResponseBody_CpuStateResponse, body.Union());
  builder.Finish(resp);
  return std::vector<uint8_t>(builder.GetBufferPointer(),
                              builder.GetBufferPointer() + builder.GetSize());
}

// Decode targets, matching the Python decode benchmark's field tuple.
struct CpuFields { uint16_t pc; uint8_t a, x, y, sp; uint64_t cycles; };

CpuFields json_decode_cpu_state(const std::string& raw) {
  json j = json::parse(raw);
  return {j["pc"], j["a"], j["x"], j["y"], j["sp"], j["cycles"]};
}

CpuFields fb_decode_cpu_state(const std::vector<uint8_t>& raw) {
  auto resp = flatbuffers::GetRoot<Response>(raw.data());
  auto body = static_cast<const CpuStateResponse*>(resp->body());
  return {body->pc(), body->a(), body->x(), body->y(), body->sp(), body->cycles()};
}

const std::vector<uint8_t> REGISTERS = [] {
  std::vector<uint8_t> v(64, 0);
  v[0] = 1;
  v[9] = 66;
  return v;
}();

std::string json_encode_tia_registers() {
  json j{{"ok", true}, {"registers", REGISTERS}};
  return j.dump();
}

std::vector<uint8_t> fb_encode_tia_registers() {
  flatbuffers::FlatBufferBuilder builder(256);
  auto regs = builder.CreateVector(REGISTERS);
  auto body = CreateTiaRegistersResponse(builder, regs);
  auto resp = CreateResponse(builder, true, ResponseBody_TiaRegistersResponse, body.Union());
  builder.Finish(resp);
  return std::vector<uint8_t>(builder.GetBufferPointer(),
                              builder.GetBufferPointer() + builder.GetSize());
}

std::vector<uint8_t> json_decode_tia_registers(const std::string& raw) {
  json j = json::parse(raw);
  return j["registers"].get<std::vector<uint8_t>>();
}

std::vector<uint8_t> fb_decode_tia_registers(const std::vector<uint8_t>& raw) {
  auto resp = flatbuffers::GetRoot<Response>(raw.data());
  auto body = static_cast<const TiaRegistersResponse*>(resp->body());
  return std::vector<uint8_t>(body->registers()->begin(), body->registers()->end());
}

int main() {
  const std::string cpu_json = json_encode_cpu_state();
  const auto cpu_fb = fb_encode_cpu_state();
  const std::string regs_json = json_encode_tia_registers();
  const auto regs_fb = fb_encode_tia_registers();

  std::printf("%-28s %10s %9s %11s %9s\n", "message", "json bytes", "fb bytes",
              "json enc us", "fb enc us");
  std::printf("%-28s %10zu %9zu %11.3f %9.3f\n", "get_cpu_state response",
              cpu_json.size(), cpu_fb.size(), time_us(json_encode_cpu_state),
              time_us(fb_encode_cpu_state));
  std::printf("%-28s %10zu %9zu %11.3f %9.3f\n", "get_tia_registers response",
              regs_json.size(), regs_fb.size(), time_us(json_encode_tia_registers),
              time_us(fb_encode_tia_registers));

  std::printf("\n--- decode ---\n");
  std::printf("%-28s %11s %9s\n", "message", "json dec us", "fb dec us");
  std::printf("%-28s %11.3f %9.3f\n", "get_cpu_state response",
              time_us([&] { json_decode_cpu_state(cpu_json); }),
              time_us([&] { fb_decode_cpu_state(cpu_fb); }));
  std::printf("%-28s %11.3f %9.3f\n", "get_tia_registers response",
              time_us([&] { json_decode_tia_registers(regs_json); }),
              time_us([&] { fb_decode_tia_registers(regs_fb); }));

  return 0;
}
