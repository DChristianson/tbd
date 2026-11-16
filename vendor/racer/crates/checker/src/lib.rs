//! The checker: runs a ROM in the simulator and checks the properties of a
//! check file (`racer-checkfile`) against its trace (milestone 1, task 9).
//! Results are reported as text or JSON, so that people and tools can both
//! read them.

pub mod check;
pub mod crosscheck;
pub mod frames;
pub mod state;

use check::Failure;
pub use racer_checkfile::{property, CheckFile, InputEntry, RunConfig};

use property::Property;
use racer_sim::board::PowerOn;
use racer_sim::input::{Entry, Input, Script};
use racer_sim::machine::Machine;
use racer_sim::trace::TraceConfig;
use serde::Serialize;
use std::collections::BTreeSet;
use std::path::Path;

/// The script a check file's input entries make.
pub fn script(entries: &[InputEntry]) -> Result<Script, String> {
    let mut out = Vec::new();
    for e in entries {
        let mut input = Input::default();
        for (n, held) in [&e.joystick0, &e.joystick1].into_iter().enumerate() {
            for name in held {
                input
                    .hold(n, name)
                    .map_err(|m| format!("input from frame {}: {m}", e.from))?;
            }
        }
        for name in &e.console {
            match name.as_str() {
                "reset" => input.reset = true,
                "select" => input.select = true,
                _ => {
                    return Err(format!(
                        "input from frame {}: unknown console switch {name:?}: give reset or select",
                        e.from
                    ))
                }
            }
        }
        out.push(Entry { from: e.from, input });
    }
    Script::new(out)
}

/// The result of one run from one power-on state.
#[derive(Clone, Debug, Serialize)]
pub struct RunReport {
    /// `"zero"`, or `"random seed N"`.
    pub power_on: String,
    pub failures: Vec<Failure>,
    /// Set if the simulator stopped, for example on an undocumented opcode.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// The result of checking one file.
#[derive(Clone, Debug, Serialize)]
pub struct FileReport {
    pub file: String,
    pub rom: String,
    pub passed: bool,
    pub expect_failures: Vec<String>,
    /// How many runs there were, and how many did not match expectations.
    pub runs: usize,
    pub runs_unexpected: usize,
    /// The runs that did not match expectations.
    pub unexpected: Vec<RunReport>,
}

/// Run `machine` and check `properties`.
pub fn check_run(machine: &mut Machine, config: &RunConfig, properties: &[Property]) -> Result<Vec<Failure>, String> {
    machine.record(TraceConfig {
        cpu_cycles: false,
        register_writes: properties.iter().any(Property::needs_writes),
        video: properties.iter().any(Property::needs_video),
        audio: properties.iter().any(Property::needs_audio),
    });
    machine.reset().map_err(|s| format!("{s:?}"))?;
    // The moments state properties read: the start of each line they name,
    // and each frame's end.
    let lines: Vec<Option<u32>> = properties.iter().filter_map(Property::state_line).collect();
    let mut snapshots = Vec::new();
    let (mut line, mut ended) = (machine.board.tia.line, 0);
    // Frames end when the next one starts; run until `frames` have ended.
    let limit = 200_000 * (config.frames as u64 + 1);
    while machine.board.tia.frame_lengths.len() < config.frames {
        machine
            .step()
            .map_err(|s| format!("simulator stopped at ${:04x}: {s:?}", machine.regs.pc))?;
        if !lines.is_empty() {
            let tia = &machine.board.tia;
            if tia.frame_lengths.len() > ended {
                ended = tia.frame_lengths.len();
                if lines.contains(&None) {
                    snapshots.push(state::Snapshot::of(&machine.board, ended as u64, None));
                }
            } else if tia.line != line && lines.contains(&Some(tia.line)) {
                snapshots.push(state::Snapshot::of(&machine.board, tia.frame, Some(tia.line)));
            }
            line = machine.board.tia.line;
        }
        if machine.board.cycles > limit {
            return Err(format!(
                "only {} frames completed in {limit} cycles",
                machine.board.tia.frame_lengths.len()
            ));
        }
    }
    Ok(check::check(
        properties,
        &machine.board.trace,
        &machine.board.tia.frame_lengths,
        config.skip_frames,
        &snapshots,
    ))
}

/// Load and check a check file.
pub fn check_file(path: &Path) -> Result<FileReport, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    check_text(&text, path)
}

/// Check the contents of a check file. `path` is used for messages, and its
/// directory to find the ROM.
pub fn check_text(text: &str, path: &Path) -> Result<FileReport, String> {
    let file = CheckFile::parse(text).map_err(|e| format!("{}: {e}", path.display()))?;
    check(&file, path)
}

/// Check a check file. `path` is used for messages, and its directory to
/// find the ROM.
pub fn check(file: &CheckFile, path: &Path) -> Result<FileReport, String> {
    for p in &file.properties {
        p.validate().map_err(|e| format!("{}: {e}", path.display()))?;
        p.validate_references(&file.properties)
            .map_err(|e| format!("{}: {e}", path.display()))?;
    }
    let script = script(&file.inputs).map_err(|e| format!("{}: {e}", path.display()))?;
    let rom_path = path.parent().unwrap_or(Path::new(".")).join(&file.rom);
    let rom = std::fs::read(&rom_path).map_err(|e| format!("{}: {e}", rom_path.display()))?;

    let mut power_ons = Vec::new();
    if file.run.zero {
        power_ons.push(PowerOn::Zero);
    }
    power_ons.extend((1..=file.run.random_seeds).map(PowerOn::Random));

    let expected: BTreeSet<&str> = file.expect_failures.iter().map(String::as_str).collect();
    let mut unexpected = Vec::new();
    for power_on in &power_ons {
        let label = match power_on {
            PowerOn::Zero => "zero".to_string(),
            PowerOn::Random(seed) => format!("random seed {seed}"),
        };
        let mut machine = Machine::new(&rom, *power_on)?;
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
        let report = match check_run(&mut machine, &run, &file.properties) {
            Ok(failures) => RunReport {
                power_on: label,
                failures,
                error: None,
            },
            Err(e) => RunReport {
                power_on: label,
                failures: Vec::new(),
                error: Some(e),
            },
        };
        let failed: BTreeSet<&str> = report.failures.iter().map(|f| f.property.as_str()).collect();
        if report.error.is_some() || failed != expected {
            unexpected.push(report);
        }
    }
    Ok(FileReport {
        file: path.display().to_string(),
        rom: rom_path.display().to_string(),
        passed: unexpected.is_empty(),
        expect_failures: file.expect_failures.clone(),
        runs: power_ons.len(),
        runs_unexpected: unexpected.len(),
        unexpected,
    })
}

impl FileReport {
    /// A readable summary: one line if the file passed, else the first
    /// unexpected run's failures.
    pub fn text(&self) -> String {
        let expected = if self.expect_failures.is_empty() {
            String::new()
        } else {
            format!(", failing {} as expected", self.expect_failures.join(", "))
        };
        if self.passed {
            return format!("{}: passed ({} runs{expected})\n", self.file, self.runs);
        }
        let mut out = format!(
            "{}: FAILED ({} of {} runs unexpected{expected})\n",
            self.file, self.runs_unexpected, self.runs
        );
        if let Some(run) = self.unexpected.first() {
            out.push_str(&format!("  first unexpected run: {}\n", run.power_on));
            if let Some(e) = &run.error {
                out.push_str(&format!("    {e}\n"));
            }
            for f in &run.failures {
                let note = if self.expect_failures.contains(&f.property) {
                    " (expected)"
                } else {
                    ""
                };
                out.push_str(&format!("    {f}{note}\n"));
            }
            let failed: Vec<&str> = run.failures.iter().map(|f| f.property.as_str()).collect();
            for missing in self.expect_failures.iter().filter(|e| !failed.contains(&e.as_str())) {
                out.push_str(&format!("    {missing} was expected to fail but passed\n"));
            }
        }
        out
    }
}
