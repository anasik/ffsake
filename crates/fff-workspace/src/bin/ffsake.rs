use fff_workspace::Workspace;
use fff_workspace::resident::{Request, Resident};
use serde_json::json;
use std::io::{self, BufRead, Write};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 2 || args[0] != "serve" {
        eprintln!(
            "usage: ffsake serve WORKSPACE\nOne JSON request per stdin line; one JSON response per stdout line."
        );
        std::process::exit(2);
    }
    let mut runtime = Resident::open(Workspace::new(&args[1])?)?;
    let mut input = io::stdin().lock();
    let mut output = io::stdout().lock();
    loop {
        // Bound malformed/untrusted input without accumulating an unlimited line.
        let mut bytes = Vec::new();
        let mut oversized = false;
        loop {
            let chunk = input.fill_buf()?;
            if chunk.is_empty() {
                break;
            }
            let end = chunk.iter().position(|b| *b == b'\n');
            let used = end.map_or(chunk.len(), |n| n + 1);
            if bytes.len() + used <= 1024 * 1024 && !oversized {
                bytes.extend_from_slice(&chunk[..used]);
            } else {
                oversized = true;
            }
            input.consume(used);
            if end.is_some() {
                break;
            }
        }
        if bytes.is_empty() && !oversized {
            break;
        }
        let response = if oversized {
            json!({"ok": false, "error": "request exceeds 1 MiB"})
        } else {
            match serde_json::from_slice::<Request>(&bytes) {
                Ok(request) => match runtime.execute(request) {
                    Ok(result) => json!({"ok": true, "result": result}),
                    Err(error) => json!({"ok": false, "error": error.to_string()}),
                },
                Err(error) => json!({"ok": false, "error": error.to_string()}),
            }
        };
        serde_json::to_writer(&mut output, &response)?;
        writeln!(output)?;
        output.flush()?;
    }
    Ok(())
}
