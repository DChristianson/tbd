//! `racer-check --frames`: a check file's runs as video, for tools that check
//! output themselves. (Added in tbd's vendored copy.)
//!
//! Each run (the zero power-on state, then random seeds 1-N, as the check
//! file's `[run]` says) is simulated as `check` would, with video recorded,
//! and given as one JSON object: its `power_on` label, its `frame_lengths`,
//! and each checked frame (after `skip_frames`) as `{ number, lines }`, each
//! line a list of runs `[start, length, signal]`, where `signal` is
//! `"sync"`, `"blank"`, or a color value. Properties are not checked.

use crate::{script, CheckFile, RunConfig};
use racer_sim::board::PowerOn;
use racer_sim::machine::Machine;
use racer_sim::tia::Signal;
use racer_sim::trace::{TraceConfig, VideoFrame};
use serde_json::{json, Value};
use std::path::Path;

fn signal(s: Signal) -> Value {
    match s {
        Signal::Sync => json!("sync"),
        Signal::Blank => json!("blank"),
        Signal::Color(c) => json!(c),
    }
}

fn frame(f: &VideoFrame) -> Value {
    let lines: Vec<Value> = f
        .lines
        .iter()
        .map(|l| Value::Array(l.runs.iter().map(|r| json!([r.start, r.length, signal(r.signal)])).collect()))
        .collect();
    json!({ "number": f.number, "lines": lines })
}

/// Run `machine` for `config.frames` frames, recording video.
fn record(machine: &mut Machine, config: &RunConfig) -> Result<(), String> {
    machine.record(TraceConfig {
        video: true,
        ..TraceConfig::OFF
    });
    machine.reset().map_err(|s| format!("{s:?}"))?;
    let limit = 200_000 * (config.frames as u64 + 1);
    while machine.board.tia.frame_lengths.len() < config.frames {
        machine
            .step()
            .map_err(|s| format!("simulator stopped at ${:04x}: {s:?}", machine.regs.pc))?;
        if machine.board.cycles > limit {
            return Err(format!(
                "only {} frames completed in {limit} cycles",
                machine.board.tia.frame_lengths.len()
            ));
        }
    }
    Ok(())
}

/// Every run of the check file at `path`, as video.
pub fn frames_file(path: &Path) -> Result<Vec<Value>, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let file = CheckFile::parse(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    let script = script(&file.inputs).map_err(|e| format!("{}: {e}", path.display()))?;
    let rom_path = path.parent().unwrap_or(Path::new(".")).join(&file.rom);
    let rom = std::fs::read(&rom_path).map_err(|e| format!("{}: {e}", rom_path.display()))?;

    let mut power_ons = Vec::new();
    if file.run.zero {
        power_ons.push(PowerOn::Zero);
    }
    power_ons.extend((1..=file.run.random_seeds).map(PowerOn::Random));

    let mut out = Vec::new();
    for power_on in power_ons {
        let label = match power_on {
            PowerOn::Zero => "zero".to_string(),
            PowerOn::Random(seed) => format!("random seed {seed}"),
        };
        let mut machine = Machine::new(&rom, power_on)?;
        if !file.inputs.is_empty() {
            machine.board.script = Some(script.clone());
        }
        let run = match power_on {
            PowerOn::Random(_) => RunConfig {
                frames: file.run.random_frames.unwrap_or(file.run.frames),
                ..file.run.clone()
            },
            PowerOn::Zero => file.run.clone(),
        };
        let error = record(&mut machine, &run).err();
        let skip = run.skip_frames as u64;
        let frames: Vec<Value> = machine
            .board
            .trace
            .frames
            .iter()
            .filter(|f| f.number > skip)
            .map(frame)
            .collect();
        let mut report = json!({
            "file": path.display().to_string(),
            "power_on": label,
            "frame_lengths": machine.board.tia.frame_lengths,
            "frames": frames,
        });
        if let Some(e) = error {
            report["error"] = json!(e);
        }
        out.push(report);
    }
    Ok(out)
}
