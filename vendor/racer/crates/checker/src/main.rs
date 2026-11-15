//! `racer-check [--json] <file.check.toml>...`
//!
//! Checks each file and prints a summary per file, or with `--json`, one
//! JSON report per line. Exits with status 0 if every file passed, 1 if any
//! failed, and 2 if a file could not be checked.

use racer_checker::check_file;
use std::path::Path;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let json = args.iter().any(|a| a == "--json");
    let files: Vec<&String> = args.iter().filter(|a| *a != "--json").collect();
    if files.is_empty() {
        eprintln!("usage: racer-check [--json] <file.check.toml>...");
        return ExitCode::from(2);
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
