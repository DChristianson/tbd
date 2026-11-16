//! `racer-check [--json] <file.check.toml>...`
//! `racer-check --frames <file.check.toml>...`
//!
//! Checks each file and prints a summary per file, or with `--json`, one
//! JSON report per line. Exits with status 0 if every file passed, 1 if any
//! failed, and 2 if a file could not be checked.
//!
//! With `--frames` (added in tbd's vendored copy), checks nothing: prints
//! each run of each file as video, one JSON object per line (see
//! `racer_checker::frames`). Exits with status 2 if a file could not be run.

use racer_checker::check_file;
use std::path::Path;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let json = args.iter().any(|a| a == "--json");
    let frames = args.iter().any(|a| a == "--frames");
    let files: Vec<&String> = args.iter().filter(|a| *a != "--json" && *a != "--frames").collect();
    if files.is_empty() {
        eprintln!("usage: racer-check [--json | --frames] <file.check.toml>...");
        return ExitCode::from(2);
    }
    if frames {
        return print_frames(&files);
    }
    let mut status = 0;
    for file in files {
        match check_file(Path::new(file)) {
            Ok(report) => {
                if json {
                    println!("{}", serde_json::to_string(&report).expect("serializable"));
                } else {
                    print!("{}", report.text());
                }
                if !report.passed {
                    status = status.max(1);
                }
            }
            Err(e) => {
                if json {
                    println!("{}", serde_json::json!({ "file": file, "error": e }));
                } else {
                    eprintln!("{file}: {e}");
                }
                status = 2;
            }
        }
    }
    ExitCode::from(status)
}

fn print_frames(files: &[&String]) -> ExitCode {
    let mut status = 0;
    for file in files {
        match racer_checker::frames::frames_file(Path::new(file)) {
            Ok(runs) => {
                for run in runs {
                    println!("{run}");
                }
            }
            Err(e) => {
                println!("{}", serde_json::json!({ "file": file, "error": e }));
                status = 2;
            }
        }
    }
    ExitCode::from(status)
}
