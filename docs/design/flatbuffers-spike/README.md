# FlatBuffers spike — evaluation only, not adopted

Write-up: [../emulator-protocol-flatbuffers.md](../emulator-protocol-flatbuffers.md).

Not part of the build — `generated/` and the compiled `cpp_benchmark`
binary are gitignored; regenerate them to reproduce:

```sh
brew install flatbuffers   # Apache-2.0
flatc --cpp --python -o generated emulator_task1.fbs

.venv/bin/pip install flatbuffers numpy   # not project dependencies — spike-only
.venv/bin/python docs/design/flatbuffers-spike/benchmark.py

clang++ -std=c++17 -O2 \
  -I generated \
  -I "$(brew --prefix flatbuffers)/include" \
  -I ../../../stella-core/vendor/stella/src/lib/json \
  cpp_benchmark.cpp -o cpp_benchmark
./cpp_benchmark
```
