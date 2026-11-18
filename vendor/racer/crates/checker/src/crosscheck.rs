//! The emulator cross-check (milestone 1, done criterion 2): run a ROM in
//! the simulator and in each surveyed emulator, and compare what they did.
//!
//! - **Frame lengths,** from Stella, Gopher2600, and 6502.ts.
//! - **`COLUBK` write positions** over a frame, from Gopher2600 and 6502.ts.
//! - **Frame images,** from 6502.ts, and from Gopher2600 through
//!   `tools/emulator-probes/gopher-frame`, which runs its emulation as a
//!   library because its debugger cannot report images.
//!
//! Each emulator is optional: one that cannot be found is reported as
//! skipped. See docs/target-model.md, "Cross-check procedure", for why the
//! comparison takes this shape.
//!
//! **Under a script** (milestone 9, task 5), a check file's input entries,
//! the simulator, 6502.ts, and Gopher2600's emulation through `gopher-frame`
//! each hold each entry's controls from its frame on, applied as `VSYNC`
//! turns on, and the frame compared may be chosen. Stella's core shim and
//! Gopher2600's debugger take no such input, so their comparisons are
//! skipped.

use crate::InputEntry;
use racer_sim::board::PowerOn;
use racer_sim::machine::Machine;
use racer_sim::tia::{Signal, COLUBK};
use racer_sim::trace::{Chip, TraceConfig};
use serde::Serialize;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// The frame whose writes and image are compared, unless a session says.
const FRAME: u64 = 5;

/// What to run: an input script, and the frame to compare.
#[derive(Clone, Debug)]
pub struct Session {
    pub script: Vec<InputEntry>,
    pub frame: u64,
}

impl Default for Session {
    fn default() -> Session {
        Session {
            script: Vec::new(),
            frame: FRAME,
        }
    }
}
/// The first frame line in 6502.ts's image (docs/experiments.md, E5).
const IMAGE_FIRST_LINE: usize = 41;
/// Gopher2600 numbers lines 4 behind this model (docs/experiments.md, E1).
const GOPHER_LINE_OFFSET: u32 = 4;

/// Where the emulators are. Defaults are the development machine's layout;
/// each can be overridden by the environment variable of the same name.
#[derive(Clone, Debug)]
pub struct Emulators {
    /// `STELLA_CORE`: the headless Stella core binary.
    pub stella_core: PathBuf,
    /// `GOPHER2600`: the Gopher2600 binary.
    pub gopher2600: PathBuf,
    /// `NODE_PATH`: a node_modules directory containing 6502.ts 1.1.4.
    pub node_path: PathBuf,
    /// The directory of the probe scripts.
    pub probes: PathBuf,
    /// `GOPHER_FRAME`: a built `gopher-frame` tool. Without it, the tool is
    /// built once from its source into `target/gopher-frame/`.
    pub gopher_frame: Option<PathBuf>,
}

impl Emulators {
    pub fn from_env() -> Emulators {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let workspace = root.join("..");
        let probes = root.join("tools/emulator-probes");
        let local_modules = probes.join("node_modules");
        let var = |name: &str, default: PathBuf| std::env::var_os(name).map(PathBuf::from).unwrap_or(default);
        Emulators {
            stella_core: var("STELLA_CORE", workspace.join("tbd/stella-core/build/shim/stella_core")),
            gopher2600: var("GOPHER2600", workspace.join("Gopher2600/gopher2600")),
            node_path: var(
                "NODE_PATH",
                if local_modules.is_dir() {
                    local_modules
                } else {
                    workspace.join("four-k-of-hate/node/cpu/node_modules")
                },
            ),
            gopher_frame: std::env::var_os("GOPHER_FRAME").map(PathBuf::from),
            probes,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "outcome", rename_all = "lowercase")]
pub enum Outcome {
    Agrees {
        detail: String,
    },
    Disagrees {
        detail: String,
    },
    /// A disagreement an experiment has explained, which does not fail.
    Known {
        detail: String,
    },
    Skipped {
        reason: String,
    },
}

#[derive(Clone, Debug, Serialize)]
pub struct Comparison {
    pub emulator: &'static str,
    pub aspect: &'static str,
    #[serde(flatten)]
    pub outcome: Outcome,
}

/// A write as (line, clock, value).
type Write = (u32, u16, u8);

/// What the simulator did, in the terms the emulators are compared on.
struct Simulated {
    frame_lengths: Vec<u32>,
    writes: Vec<Write>,
    /// Frame `FRAME`'s lines, each as 160 visible values: the color, or 0
    /// for blank and sync (which 6502.ts draws as black, palette value 0).
    image: Vec<Vec<u8>>,
    /// RAM at the end of the compared frame, as the next one's `VSYNC`
    /// turns on, and whether it is the same at the end of the frame after
    /// (milestone 9, task 13).
    ram: [u8; 128],
    ram_steady: bool,
    /// The bytes the program determines: the same at that moment from zero
    /// and from a random power-on state. The others hold what power-on left,
    /// which each emulator chooses for itself.
    determined: [bool; 128],
    /// RAM at the same moment with `SWCHB`'s unused bits read as 0, as
    /// 6502.ts reads them (E28), from zero.
    ram_unused_low: [u8; 128],
}

/// Run `m` until `frames` frames have ended, or fail after as many cycles as
/// the checker allows that many frames: a ROM that stops making frames, as
/// a probe that ends in a loop does, is reported rather than run forever.
fn run_frames(m: &mut Machine, frames: usize) -> Result<(), String> {
    let limit = 200_000 * (frames as u64 + 1);
    while m.board.tia.frame_lengths.len() < frames {
        m.step()
            .map_err(|s| format!("simulator stopped at ${:04x}: {s:?}", m.regs.pc))?;
        if m.board.cycles > limit {
            return Err(format!(
                "only {} frames completed in {limit} cycles",
                m.board.tia.frame_lengths.len()
            ));
        }
    }
    Ok(())
}

fn simulate(rom: &[u8], session: &Session) -> Result<Simulated, String> {
    let mut m = Machine::new(rom, PowerOn::Zero)?;
    if !session.script.is_empty() {
        m.board.script = Some(crate::script(&session.script)?);
    }
    m.record(TraceConfig {
        cpu_cycles: false,
        register_writes: true,
        video: true,
        audio: false,
    });
    m.reset().map_err(|s| format!("{s:?}"))?;
    run_frames(&mut m, session.frame as usize)?;
    let ram = m.board.ram;
    run_frames(&mut m, session.frame as usize + 1)?;
    let ram_steady = m.board.ram == ram;
    run_frames(&mut m, session.frame as usize + 3)?;
    let mut other = Machine::new(rom, PowerOn::Random(1))?;
    if !session.script.is_empty() {
        other.board.script = Some(crate::script(&session.script)?);
    }
    other.reset().map_err(|s| format!("{s:?}"))?;
    run_frames(&mut other, session.frame as usize)?;
    let determined = std::array::from_fn(|i| other.board.ram[i] == ram[i]);
    let mut low = Machine::new(rom, PowerOn::Zero)?;
    low.board.port_b_unused = 0;
    if !session.script.is_empty() {
        low.board.script = Some(crate::script(&session.script)?);
    }
    low.reset().map_err(|s| format!("{s:?}"))?;
    run_frames(&mut low, session.frame as usize)?;
    let trace = &m.board.trace;
    let frame = trace
        .frames
        .iter()
        .find(|f| f.number == session.frame)
        .ok_or("frame not recorded")?;
    let image = frame
        .lines
        .iter()
        .map(|line| {
            (68..228)
                .map(|c| match line.signal_at(c) {
                    Some(Signal::Color(v)) => v,
                    _ => 0,
                })
                .collect()
        })
        .collect();
    Ok(Simulated {
        frame_lengths: m.board.tia.frame_lengths.clone(),
        writes: trace
            .writes_to(Chip::Tia, COLUBK, session.frame)
            .map(|w| (w.line, w.clock, w.value))
            .collect(),
        image,
        ram,
        ram_steady,
        determined,
        ram_unused_low: low.board.ram,
    })
}

/// The simulator's RAM against 6502.ts's, which reads `SWCHB`'s unused
/// bits as 0 where the simulator, Gopher2600, and Stella read 1 (E28): a
/// byte that holds them, as a program that stores `SWCHB` keeps, is
/// compared with the simulator's run that reads them as 0, and a
/// difference there alone is known.
fn compare_ram_6502ts(sim: &Simulated, emulated: Result<Vec<u8>, String>) -> Outcome {
    let outcome = compare_ram(sim, emulated.clone());
    match (outcome, emulated) {
        (Outcome::Disagrees { detail }, Ok(ram))
            if ram.len() == 128 && (0..128).all(|i| !sim.determined[i] || ram[i] == sim.ram_unused_low[i]) =>
        {
            Outcome::Known {
                detail: format!(
                    "{detail}: the byte holds SWCHB's unused bits 2, 4, and 5, which 6502.ts reads as 0, and Gopher2600, Stella, and the simulator as 1 (E28)"
                ),
            }
        }
        (outcome, _) => outcome,
    }
}

/// The simulator's RAM against an emulator's, `$80` to `$FF`.
fn compare_ram(sim: &Simulated, emulated: Result<Vec<u8>, String>) -> Outcome {
    match emulated {
        Err(reason) => Outcome::Skipped { reason },
        Ok(ram) if ram.len() != 128 => Outcome::Skipped {
            reason: format!("{} bytes of RAM read", ram.len()),
        },
        Ok(ram) => match (0..128).find(|&i| sim.determined[i] && ram[i] != sim.ram[i]) {
            None => Outcome::Agrees {
                detail: format!(
                    "{} bytes the program determines",
                    sim.determined.iter().filter(|d| **d).count()
                ),
            },
            Some(i) => Outcome::Disagrees {
                detail: format!(
                    "${:02x} is ${:02x} in the simulator, ${:02x} here",
                    0x80 + i,
                    sim.ram[i],
                    ram[i]
                ),
            },
        },
    }
}

/// RAM in an emulator whose frames are not aligned with the simulator's
/// (E1): compared only where the simulator's RAM does not change from the
/// compared frame's end to the next one's, as a probe's results do not.
fn steady_ram(sim: &Simulated, scripted: bool, read: impl FnOnce() -> Result<Vec<u8>, String>) -> Outcome {
    if scripted {
        return Outcome::Skipped {
            reason: "it takes no scripted input".into(),
        };
    }
    if !sim.ram_steady {
        return Outcome::Skipped {
            reason: "RAM changes from frame to frame, and its frames are not aligned with the simulator's (E1)".into(),
        };
    }
    compare_ram(sim, read())
}

/// RAM after `frames` frames in the Stella core's shim.
fn stella_ram(emu: &Emulators, rom: &Path, frames: u64) -> Result<Vec<u8>, String> {
    if !emu.stella_core.is_file() {
        return Err(format!("no Stella core at {}", emu.stella_core.display()));
    }
    let input = format!(
        "{{\"cmd\":\"load_rom\",\"path\":{:?}}}\n{{\"cmd\":\"step_frame\",\"n\":{frames}}}\n{{\"cmd\":\"get_ram\"}}\n{{\"cmd\":\"quit\"}}\n",
        rom.display().to_string()
    );
    let out = run(&emu.stella_core, &[], &input)?;
    let line = out.lines().nth(2).ok_or("no RAM from the Stella core")?;
    let v: serde_json::Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
    Ok(v["ram"]
        .as_array()
        .ok_or("no RAM from the Stella core")?
        .iter()
        .filter_map(|b| b.as_u64().map(|b| b as u8))
        .collect())
}

/// RAM after `frames` frames in Gopher2600's debugger.
fn gopher_ram(emu: &Emulators, rom: &Path, frames: u64) -> Result<Vec<u8>, String> {
    if !emu.gopher2600.is_file() {
        return Err(format!("no Gopher2600 at {}", emu.gopher2600.display()));
    }
    let script = std::env::temp_dir().join(format!("racer-gopher-ram-{}.txt", std::process::id()));
    std::fs::write(&script, "STEP FRAME\n".repeat(frames as usize) + "RAM\n").map_err(|e| e.to_string())?;
    let out = run(
        &emu.gopher2600,
        &[
            "debug",
            "-term",
            "PLAIN",
            "-tv",
            "NTSC",
            "-script",
            &script.display().to_string(),
            &rom.display().to_string(),
        ],
        "",
    );
    let _ = std::fs::remove_file(&script);
    // Eight lines of 16 bytes, `000000r0 xx xx ...`, for `$80 + r0`.
    let bytes: Vec<u8> = out?
        .lines()
        .filter(|l| l.len() > 10 && l.starts_with("000000") && l.as_bytes()[7] == b'0')
        .flat_map(|l| {
            l[9..]
                .split_whitespace()
                .take(16)
                .filter_map(|b| u8::from_str_radix(b, 16).ok())
                .collect::<Vec<_>>()
        })
        .collect();
    Ok(bytes)
}

fn run(program: &Path, args: &[&str], stdin: &str) -> Result<String, String> {
    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("{}: {e}", program.display()))?;
    child
        .stdin
        .take()
        .expect("stdin")
        .write_all(stdin.as_bytes())
        .map_err(|e| e.to_string())?;
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// The steady frame length: the simulator's frames after the first, which
/// must all be the same.
fn steady(lengths: &[u32]) -> Option<u32> {
    let rest = lengths.get(1..)?;
    let first = *rest.first()?;
    rest.iter().all(|&n| n == first).then_some(first)
}

fn compare_lengths(emulator: &'static str, sim: &Simulated, emulated: Result<u32, String>) -> Comparison {
    let outcome = match (emulated, steady(&sim.frame_lengths)) {
        (Err(reason), _) => Outcome::Skipped { reason },
        (Ok(_), None) => Outcome::Disagrees {
            detail: format!("simulator frames vary: {:?}", sim.frame_lengths),
        },
        (Ok(e), Some(s)) if e == s => Outcome::Agrees {
            detail: format!("{s} lines"),
        },
        (Ok(e), Some(s)) => Outcome::Disagrees {
            detail: format!("emulator {e} lines, simulator {s}"),
        },
    };
    Comparison {
        emulator,
        aspect: "frame lengths",
        outcome,
    }
}

/// Compare the simulator's `COLUBK` writes with an emulator's. With
/// `partial`, the emulator may report fewer than it made: Gopher2600's
/// `TRACE` leaves out writes made back to back on a line, a few cycles
/// apart (docs/experiments.md, E21 and E26), though its frame image shows
/// them. Each write it does report must then be one of the simulator's, at
/// the same line and clock, with the same value.
fn compare_writes(
    emulator: &'static str,
    mut sim: Vec<Write>,
    emulated: Result<Vec<Write>, String>,
    partial: bool,
) -> Comparison {
    let outcome = match emulated {
        Err(reason) => Outcome::Skipped { reason },
        Ok(mut emu) => {
            sim.sort();
            emu.sort();
            // Whether every one of `emu`'s writes is one of `sim`'s, as
            // sorted lists, each used once.
            let within = || {
                let mut rest = sim.iter();
                emu.iter().all(|e| rest.any(|s| s == e))
            };
            if sim == emu {
                Outcome::Agrees {
                    detail: format!("{} writes", sim.len()),
                }
            } else if partial && !emu.is_empty() && within() {
                Outcome::Agrees {
                    detail: format!(
                        "{} of the simulator's {} writes, each at its line and clock; its trace leaves out writes made back to back",
                        emu.len(),
                        sim.len()
                    ),
                }
            } else {
                let first = sim.iter().zip(&emu).find(|(a, b)| a != b);
                Outcome::Disagrees {
                    detail: match first {
                        Some((s, e)) => format!("simulator {s:?}, emulator {e:?} (line, clock, value)"),
                        None => format!("simulator {} writes, emulator {}", sim.len(), emu.len()),
                    },
                }
            }
        }
    };
    Comparison {
        emulator,
        aspect: "COLUBK writes",
        outcome,
    }
}

fn stella_length(emu: &Emulators, rom: &Path) -> Result<u32, String> {
    if !emu.stella_core.is_file() {
        return Err(format!("{} not found", emu.stella_core.display()));
    }
    let commands = format!(
        "{}\n{}\n{}\n{}\n",
        serde_json::json!({ "cmd": "load_rom", "path": rom }),
        r#"{"cmd":"step_frame","n":10}"#,
        r#"{"cmd":"get_tia_state"}"#,
        r#"{"cmd":"quit"}"#
    );
    let out = run(&emu.stella_core, &[], &commands)?;
    let state: serde_json::Value =
        serde_json::from_str(out.lines().nth(2).ok_or("no response from stella_core")?).map_err(|e| e.to_string())?;
    state["scanlines_last_frame"]
        .as_u64()
        .map(|n| n as u32)
        .ok_or_else(|| format!("unexpected response: {state}"))
}

/// Gopher2600's frame length, and the COLUBK writes over one frame's worth of
/// lines, converted to this model's line and clock numbering.
fn gopher(emu: &Emulators, rom: &Path, length: u32) -> Result<(u32, Vec<Write>), String> {
    if !emu.gopher2600.is_file() {
        return Err(format!("{} not found", emu.gopher2600.display()));
    }
    let mut script = String::new();
    for _ in 0..FRAME {
        script.push_str("STEP FRAME\n");
    }
    script.push_str("TV FRAME\nTRACE COLUBK\nONTRACE TV\n");
    for _ in 0..length {
        script.push_str("STEP SCANLINE\n");
    }
    let script_path = std::env::temp_dir().join(format!("racer-crosscheck-{}.script", std::process::id()));
    std::fs::write(&script_path, script).map_err(|e| e.to_string())?;
    let rom_arg = rom.to_string_lossy();
    let script_arg = script_path.to_string_lossy();
    let out = run(
        &emu.gopher2600,
        &[
            "debug",
            "-term",
            "PLAIN",
            "-tv",
            "NTSC",
            "-script",
            &script_arg,
            &rom_arg,
        ],
        "",
    );
    let _ = std::fs::remove_file(&script_path);
    let out = out?;

    let total = out
        .lines()
        .find_map(|l| l.split("total: ").nth(1))
        .and_then(|t| t.trim().parse().ok())
        .ok_or("no frame length in Gopher2600's output")?;
    // Each write follows the TV position it happened at:
    //   FR=0005 SL=036 CL=-44
    //   write 0x00 to 0x0009 (COLUBK) (TIA)
    let mut writes = Vec::new();
    let mut position: Option<(u32, i32)> = None;
    for l in out.lines() {
        if let Some(rest) = l.trim().strip_prefix("FR=") {
            let field = |key: &str| {
                rest.split_whitespace()
                    .find_map(|f| f.strip_prefix(key))
                    .and_then(|v| v.parse().ok())
            };
            if let (Some(sl), Some(cl)) = (field("SL="), field("CL=")) {
                position = Some((sl as u32, cl));
            }
        } else if let Some(rest) = l.trim().strip_prefix("write 0x") {
            let value = u8::from_str_radix(&rest[..2], 16).map_err(|e| e.to_string())?;
            let (sl, cl) = position.take().ok_or("a write without a TV position")?;
            writes.push(((sl + GOPHER_LINE_OFFSET) % length, (cl + 68) as u16, value));
        }
    }
    Ok((total, writes))
}

struct Report6502ts {
    frame_lengths: Vec<u32>,
    ram: Option<Vec<u8>>,
    writes: Vec<Write>,
    image_first_line: usize,
    rows: Vec<Vec<i32>>,
}

fn run_6502ts(emu: &Emulators, rom: &Path, script: &Path, frame: u64) -> Result<Report6502ts, String> {
    if !emu.node_path.join("6502.ts").is_dir() {
        return Err(format!("6502.ts not installed in {}", emu.node_path.display()));
    }
    let out = Command::new("node")
        .arg(emu.probes.join("6502ts-report.js"))
        .arg(rom)
        .arg(script)
        .arg(frame.to_string())
        .env("NODE_PATH", &emu.node_path)
        .output()
        .map_err(|e| format!("node: {e}"))?;
    let v: serde_json::Value = serde_json::from_slice(&out.stdout)
        .map_err(|e| format!("6502.ts report: {e}: {}", String::from_utf8_lossy(&out.stderr)))?;
    let nums =
        |v: &serde_json::Value| -> Vec<i64> { v.as_array().into_iter().flatten().filter_map(|n| n.as_i64()).collect() };
    Ok(Report6502ts {
        frame_lengths: nums(&v["frameLengths"]).into_iter().map(|n| n as u32).collect(),
        ram: v["ram"]
            .is_array()
            .then(|| nums(&v["ram"]).into_iter().map(|n| n as u8).collect()),
        writes: v["writes"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|w| {
                (
                    w["line"].as_u64().unwrap_or(0) as u32,
                    w["clock"].as_u64().unwrap_or(0) as u16,
                    w["value"].as_u64().unwrap_or(0) as u8,
                )
            })
            .collect(),
        image_first_line: v["imageFirstLine"].as_u64().unwrap_or(IMAGE_FIRST_LINE as u64) as usize,
        rows: v["rows"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|r| nums(r).into_iter().map(|n| n as i32).collect())
            .collect(),
    })
}

/// Gopher2600's image of frame `frame`, as rows of 160 values, and the
/// frame line of its first row, under the script in `script`, if any.
fn gopher_image(
    emu: &Emulators,
    rom: &Path,
    frame: u64,
    script: Option<&Path>,
) -> Result<(usize, Vec<Vec<i32>>), String> {
    let rom_arg = rom.to_string_lossy();
    let frame = frame.to_string();
    let script_arg = script.map(|s| s.to_string_lossy().into_owned());
    let mut args = vec![rom_arg.as_ref(), frame.as_str()];
    args.extend(script_arg.as_deref());
    let tool = match &emu.gopher_frame {
        Some(tool) => tool.clone(),
        None => built_gopher_frame(emu)?,
    };
    let out = run(&tool, &args, "")?;
    let v: serde_json::Value = serde_json::from_str(&out).map_err(|e| format!("gopher-frame: {e}"))?;
    let rows = v["rows"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|r| {
            r.as_array()
                .into_iter()
                .flatten()
                .filter_map(|n| n.as_i64())
                .map(|n| n as i32)
                .collect()
        })
        .collect();
    Ok((GOPHER_LINE_OFFSET as usize, rows))
}

/// The `gopher-frame` tool, built once from its source into `target/`, and
/// again only when its source is newer than the binary: `go run` built it
/// on every call, which was most of a cross-check's time.
fn built_gopher_frame(emu: &Emulators) -> Result<PathBuf, String> {
    static BUILT: std::sync::OnceLock<Result<PathBuf, String>> = std::sync::OnceLock::new();
    BUILT
        .get_or_init(|| {
            let source = emu.probes.join("gopher-frame");
            if !source.join("go.mod").is_file() || !emu.gopher2600.parent().is_some_and(|d| d.join("go.mod").is_file())
            {
                return Err("gopher-frame needs Gopher2600's source beside its binary".into());
            }
            let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/gopher-frame");
            let binary = dir.join("gopher-frame");
            let modified = |p: &Path| std::fs::metadata(p).and_then(|m| m.modified()).ok();
            let newest = ["main.go", "go.mod", "go.sum"]
                .iter()
                .filter_map(|f| modified(&source.join(f)))
                .max();
            if modified(&binary).is_some_and(|b| newest.is_none_or(|n| b >= n)) {
                return Ok(binary);
            }
            std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
            // Built under a name of its own, then moved into place, so that
            // a cross-check running beside this one never runs half a file.
            let partial = dir.join(format!("gopher-frame.{}", std::process::id()));
            let out = Command::new("go")
                .args(["build", "-o"])
                .arg(&partial)
                .arg(".")
                .current_dir(&source)
                .output()
                .map_err(|e| format!("go: {e}"))?;
            if !out.status.success() {
                return Err(format!(
                    "building gopher-frame: {}",
                    String::from_utf8_lossy(&out.stderr).trim()
                ));
            }
            std::fs::rename(&partial, &binary).map_err(|e| e.to_string())?;
            Ok(binary)
        })
        .clone()
}

/// Compare an emulator's image, whose row 0 is frame line `first_line`,
/// with the simulator's.
fn compare_image(sim: &Simulated, first_line: usize, rows: &[Vec<i32>]) -> Outcome {
    let mut mismatched_rows = 0;
    let mut first = None;
    for (r, row) in rows.iter().enumerate() {
        let line = first_line + r;
        let expected = sim.image.get(line);
        let differs = match expected {
            None => row.iter().any(|&v| v != 0),
            Some(e) => row.len() != e.len() || row.iter().zip(e).any(|(&got, &want)| got != want as i32),
        };
        if differs {
            mismatched_rows += 1;
            if first.is_none() {
                let x = expected
                    .and_then(|e| row.iter().zip(e).position(|(&got, &want)| got != want as i32))
                    .unwrap_or(0);
                let want = expected.and_then(|e| e.get(x)).copied().unwrap_or(0);
                first = Some(format!(
                    "line {line}, x {x}: emulator {}, simulator {want}",
                    row.get(x).unwrap_or(&-1)
                ));
            }
        }
    }
    match first {
        None => Outcome::Agrees {
            detail: format!("{} rows of 160 pixels", rows.len()),
        },
        Some(f) => Outcome::Disagrees {
            detail: format!("{mismatched_rows} rows differ; first at {f}"),
        },
    }
}

/// Run every comparison for `rom`, with no input, on frame 5.
pub fn crosscheck(rom_path: &Path, emu: &Emulators) -> Result<Vec<Comparison>, String> {
    crosscheck_session(rom_path, emu, &Session::default())
}

/// Run every comparison for `rom` under `session`.
pub fn crosscheck_session(rom_path: &Path, emu: &Emulators, session: &Session) -> Result<Vec<Comparison>, String> {
    let rom_path = rom_path
        .canonicalize()
        .map_err(|e| format!("{}: {e}", rom_path.display()))?;
    let rom = std::fs::read(&rom_path).map_err(|e| e.to_string())?;
    let sim = simulate(&rom, session)?;
    let length = steady(&sim.frame_lengths).unwrap_or(262);
    let scripted = !session.script.is_empty();
    let script_path = std::env::temp_dir().join(format!("racer-crosscheck-{}.json", std::process::id()));
    std::fs::write(
        &script_path,
        serde_json::to_string(&session.script).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    let no_input = |what: &str| format!("{what} takes no scripted input");
    let mut results = vec![compare_lengths(
        "stella-core",
        &sim,
        if scripted {
            Err(no_input("the Stella core's shim"))
        } else {
            stella_length(emu, &rom_path)
        },
    )];

    match if scripted {
        Err(no_input("Gopher2600's debugger"))
    } else {
        gopher(emu, &rom_path, length)
    } {
        Ok((total, writes)) => {
            results.push(compare_lengths("gopher2600", &sim, Ok(total)));
            results.push(compare_writes("gopher2600", sim.writes.clone(), Ok(writes), true));
        }
        Err(reason) => {
            results.push(compare_lengths("gopher2600", &sim, Err(reason.clone())));
            results.push(compare_writes("gopher2600", sim.writes.clone(), Err(reason), true));
        }
    }
    results.push(Comparison {
        emulator: "gopher2600",
        aspect: "frame image",
        outcome: if session.script.iter().any(InputEntry::uses_paddles) {
            Outcome::Skipped {
                reason: "its paddles are a different model from the simulator's and 6502.ts's (E34)".into(),
            }
        } else {
            match gopher_image(emu, &rom_path, session.frame, scripted.then_some(script_path.as_path())) {
                Ok((first_line, rows)) => compare_image(&sim, first_line, &rows),
                Err(reason) => Outcome::Skipped { reason },
            }
        },
    });

    let report = run_6502ts(emu, &rom_path, &script_path, session.frame);
    let _ = std::fs::remove_file(&script_path);
    match report {
        Ok(report) => {
            // Compare the frames after the first, as for the simulator.
            let emu_steady = steady(&report.frame_lengths).ok_or("6502.ts frame lengths vary");
            results.push(compare_lengths("6502.ts", &sim, emu_steady.map_err(String::from)));
            results.push(compare_writes(
                "6502.ts",
                sim.writes.clone(),
                Ok(report.writes.clone()),
                false,
            ));
            results.push(Comparison {
                emulator: "6502.ts",
                aspect: "frame image",
                outcome: compare_image(&sim, report.image_first_line, &report.rows),
            });
            results.push(Comparison {
                emulator: "6502.ts",
                aspect: "RAM",
                outcome: compare_ram_6502ts(&sim, report.ram.clone().ok_or_else(|| "no RAM reported".to_string())),
            });
        }
        Err(reason) => {
            results.push(compare_lengths("6502.ts", &sim, Err(reason.clone())));
            results.push(compare_writes(
                "6502.ts",
                sim.writes.clone(),
                Err(reason.clone()),
                false,
            ));
            results.push(Comparison {
                emulator: "6502.ts",
                aspect: "frame image",
                outcome: Outcome::Skipped { reason: reason.clone() },
            });
            results.push(Comparison {
                emulator: "6502.ts",
                aspect: "RAM",
                outcome: Outcome::Skipped { reason },
            });
        }
    }
    // RAM in the emulators whose frames are not the simulator's, where it
    // has settled (milestone 9, task 13).
    let frames = session.frame + 1;
    results.push(Comparison {
        emulator: "stella-core",
        aspect: "RAM",
        outcome: steady_ram(&sim, scripted, || stella_ram(emu, &rom_path, frames)),
    });
    results.push(Comparison {
        emulator: "gopher2600",
        aspect: "RAM",
        outcome: steady_ram(&sim, scripted, || gopher_ram(emu, &rom_path, frames)),
    });
    Ok(results)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// An emulator whose trace may leave writes out agrees if each write it
    /// reports is one of the simulator's; one that must report every write
    /// agrees only if it reports exactly the simulator's.
    #[test]
    fn a_partial_trace_agrees_only_with_writes_the_simulator_made() {
        let sim: Vec<Write> = vec![(40, 69, 0x46), (40, 117, 0x0E), (40, 126, 0x84)];
        let some = vec![(40, 69, 0x46), (40, 126, 0x84)];
        let wrong = vec![(40, 69, 0x46), (40, 129, 0x84)];
        let agrees = |c: Comparison| matches!(c.outcome, Outcome::Agrees { .. });
        assert!(agrees(compare_writes("e", sim.clone(), Ok(sim.clone()), false)));
        assert!(!agrees(compare_writes("e", sim.clone(), Ok(some.clone()), false)));
        assert!(agrees(compare_writes("e", sim.clone(), Ok(some), true)));
        assert!(!agrees(compare_writes("e", sim.clone(), Ok(wrong), true)));
        assert!(!agrees(compare_writes("e", sim, Ok(vec![]), true)));
    }
}
