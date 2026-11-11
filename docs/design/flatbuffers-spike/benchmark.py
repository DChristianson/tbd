"""Measures real encoded size and encode/decode time: JSON vs FlatBuffers,
for the exact 6 message types in emulator_task1.fbs. Not part of the
package — a one-off evaluation script, run manually, output pasted into
docs/design/emulator-protocol-flatbuffers.md.
"""

import json
import sys
import time

sys.path.insert(0, "docs/design/flatbuffers-spike/generated")

import flatbuffers
from tbd.emulator.fbs import (
    Command,
    CpuStateResponse,
    LoadRomRequest,
    OkResponse,
    Request,
    RequestArgs,
    Response,
    ResponseBody,
    TiaRegistersResponse,
    TiaStateResponse,
)

N = 100_000


def time_it(fn, n=N):
    start = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - start) / n * 1e6  # microseconds/op


# ---- Real example payloads, taken from actual stella_core output ----

ROM_PATH = "/tmp/red_background.bin"  # realistic length for this project


def json_load_rom_request():
    return json.dumps({"cmd": "load_rom", "path": ROM_PATH}).encode()


def fb_load_rom_request():
    builder = flatbuffers.Builder(128)
    path = builder.CreateString(ROM_PATH)
    LoadRomRequest.Start(builder)
    LoadRomRequest.AddPath(builder, path)
    args = LoadRomRequest.End(builder)
    Request.Start(builder)
    Request.AddCmd(builder, Command.Command.LoadRom)
    Request.AddArgsType(builder, RequestArgs.RequestArgs.LoadRomRequest)
    Request.AddArgs(builder, args)
    req = Request.End(builder)
    builder.Finish(req)
    return bytes(builder.Output())


def json_step_scanline_request():
    return json.dumps({"cmd": "step_scanline"}).encode()


def fb_step_scanline_request():
    builder = flatbuffers.Builder(32)
    Request.Start(builder)
    Request.AddCmd(builder, Command.Command.StepScanline)
    req = Request.End(builder)
    builder.Finish(req)
    return bytes(builder.Output())


def json_ok_response():
    return json.dumps({"ok": True}).encode()


def fb_ok_response():
    builder = flatbuffers.Builder(32)
    OkResponse.Start(builder)
    ok = OkResponse.End(builder)
    Response.Start(builder)
    Response.AddOk(builder, True)
    Response.AddBodyType(builder, ResponseBody.ResponseBody.OkResponse)
    Response.AddBody(builder, ok)
    resp = Response.End(builder)
    builder.Finish(resp)
    return bytes(builder.Output())


# Real example from the actual CLI run earlier in this conversation:
# {"a":100,"cycles":0,"ok":true,"pc":61440,"sp":253,"x":13,"y":96}
def json_cpu_state_response():
    return json.dumps(
        {"ok": True, "pc": 61440, "a": 100, "x": 13, "y": 96, "sp": 253, "cycles": 42962}
    ).encode()


def fb_cpu_state_response():
    builder = flatbuffers.Builder(64)
    CpuStateResponse.Start(builder)
    CpuStateResponse.AddPc(builder, 61440)
    CpuStateResponse.AddA(builder, 100)
    CpuStateResponse.AddX(builder, 13)
    CpuStateResponse.AddY(builder, 96)
    CpuStateResponse.AddSp(builder, 253)
    CpuStateResponse.AddCycles(builder, 42962)
    body = CpuStateResponse.End(builder)
    Response.Start(builder)
    Response.AddOk(builder, True)
    Response.AddBodyType(builder, ResponseBody.ResponseBody.CpuStateResponse)
    Response.AddBody(builder, body)
    resp = Response.End(builder)
    builder.Finish(resp)
    return bytes(builder.Output())


# Real example: {"frame_number":3,"ok":true,"scanlines_last_frame":263}
# (plus the live "scanlines" field, 263 here too mid-run)
def json_tia_state_response():
    return json.dumps(
        {"ok": True, "scanlines": 263, "scanlines_last_frame": 263, "frame_number": 3}
    ).encode()


def fb_tia_state_response():
    builder = flatbuffers.Builder(64)
    TiaStateResponse.Start(builder)
    TiaStateResponse.AddScanlines(builder, 263)
    TiaStateResponse.AddScanlinesLastFrame(builder, 263)
    TiaStateResponse.AddFrameNumber(builder, 3)
    body = TiaStateResponse.End(builder)
    Response.Start(builder)
    Response.AddOk(builder, True)
    Response.AddBodyType(builder, ResponseBody.ResponseBody.TiaStateResponse)
    Response.AddBody(builder, body)
    resp = Response.End(builder)
    builder.Finish(resp)
    return bytes(builder.Output())


# Real example: 64 register values, from the actual run shown earlier.
REGISTERS = [1, 0, 0, 0, 0, 0, 0, 0, 0, 66] + [0] * 54


def json_tia_registers_response():
    return json.dumps({"ok": True, "registers": REGISTERS}).encode()


def fb_tia_registers_response():
    import numpy as np
    builder = flatbuffers.Builder(256)
    regs = builder.CreateNumpyVector(np.array(REGISTERS, dtype=np.uint8))
    TiaRegistersResponse.Start(builder)
    TiaRegistersResponse.AddRegisters(builder, regs)
    body = TiaRegistersResponse.End(builder)
    Response.Start(builder)
    Response.AddOk(builder, True)
    Response.AddBodyType(builder, ResponseBody.ResponseBody.TiaRegistersResponse)
    Response.AddBody(builder, body)
    resp = Response.End(builder)
    builder.Finish(resp)
    return bytes(builder.Output())


def json_decode_cpu_state(raw):
    d = json.loads(raw)
    return (d["pc"], d["a"], d["x"], d["y"], d["sp"], d["cycles"])


def fb_decode_cpu_state(raw):
    resp = Response.Response.GetRootAs(raw, 0)
    body = CpuStateResponse.CpuStateResponse()
    body.Init(resp.Body().Bytes, resp.Body().Pos)
    return (body.Pc(), body.A(), body.X(), body.Y(), body.Sp(), body.Cycles())


def json_decode_tia_registers(raw):
    return json.loads(raw)["registers"]


def fb_decode_tia_registers_naive(raw):
    resp = Response.Response.GetRootAs(raw, 0)
    body = TiaRegistersResponse.TiaRegistersResponse()
    body.Init(resp.Body().Bytes, resp.Body().Pos)
    return [body.Registers(i) for i in range(body.RegistersLength())]


def fb_decode_tia_registers(raw):
    resp = Response.Response.GetRootAs(raw, 0)
    body = TiaRegistersResponse.TiaRegistersResponse()
    body.Init(resp.Body().Bytes, resp.Body().Pos)
    return body.RegistersAsNumpy()  # zero-copy numpy view, not a Python list


CASES = [
    ("load_rom request", json_load_rom_request, fb_load_rom_request),
    ("step_scanline request", json_step_scanline_request, fb_step_scanline_request),
    ("ok response (no body)", json_ok_response, fb_ok_response),
    ("get_cpu_state response", json_cpu_state_response, fb_cpu_state_response),
    ("get_tia_state response", json_tia_state_response, fb_tia_state_response),
    ("get_tia_registers response", json_tia_registers_response, fb_tia_registers_response),
]

print(f"{'message':<28} {'json bytes':>10} {'fb bytes':>9} {'json enc us':>11} {'fb enc us':>9}")
for name, json_fn, fb_fn in CASES:
    json_bytes = json_fn()
    fb_bytes = fb_fn()
    json_us = time_it(json_fn)
    fb_us = time_it(fb_fn)
    print(
        f"{name:<28} {len(json_bytes):>10} {len(fb_bytes):>9} "
        f"{json_us:>11.3f} {fb_us:>9.3f}"
    )

print()
print("--- decode (the other half: Python reading a response) ---")
cpu_json, cpu_fb = json_cpu_state_response(), fb_cpu_state_response()
regs_json, regs_fb = json_tia_registers_response(), fb_tia_registers_response()
assert json_decode_cpu_state(cpu_json) == fb_decode_cpu_state(cpu_fb)
assert list(json_decode_tia_registers(regs_json)) == list(fb_decode_tia_registers_naive(regs_fb))
assert list(json_decode_tia_registers(regs_json)) == list(fb_decode_tia_registers(regs_fb))
print(f"{'message':<28} {'json dec us':>11} {'fb dec us':>9}")
print(
    f"{'get_cpu_state response':<28} "
    f"{time_it(lambda: json_decode_cpu_state(cpu_json)):>11.3f} "
    f"{time_it(lambda: fb_decode_cpu_state(cpu_fb)):>9.3f}"
)
print(
    f"{'get_tia_registers (naive per-elem)':<28} "
    f"{time_it(lambda: json_decode_tia_registers(regs_json)):>11.3f} "
    f"{time_it(lambda: fb_decode_tia_registers_naive(regs_fb)):>9.3f}"
)
print(
    f"{'get_tia_registers (AsNumpy)':<28} "
    f"{time_it(lambda: json_decode_tia_registers(regs_json)):>11.3f} "
    f"{time_it(lambda: fb_decode_tia_registers(regs_fb)):>9.3f}"
)
